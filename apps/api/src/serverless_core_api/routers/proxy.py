"""OpenAI-compatible reverse proxy.

Clients POST to `/v1/chat/completions` (or /v1/completions) on our Fly API
with their usual OpenAI-shaped body; we pick a healthy vast.ai instance and
forward the request, passing streaming SSE back through transparently.

M2.5 scope: no auth yet (M5 adds API keys).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from supabase import Client

from serverless_core_api.deps import (
    ApiPrincipal,
    get_service_client,
    get_staff_user,
    require_api_key,
)
from serverless_core_api.services.rental import resume_instance
from serverless_core_api.services.routing import (
    find_dormant_instance,
    pick_instance,
    wait_for_ready,
)

logger = logging.getLogger("serverless_core_api.proxy")

router = APIRouter(tags=["proxy"])


def _upstream_url(ip: str, port: int, path: str) -> str:
    return f"http://{ip}:{port}{path}"


def _log_request(
    sb: Client,
    *,
    api_key_id: str | None,
    instance_id: str | None,
    model_slug: str | None,
    path: str,
    streaming: bool,
    status_code: int,
    latency_ms: int,
    usage: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Write one row to request_logs. Swallows all errors."""
    try:
        sb.table("request_logs").insert(
            {
                "api_key_id": api_key_id,
                "instance_id": instance_id,
                "model_slug": model_slug,
                "path": path,
                "streaming": streaming,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "prompt_tokens": (usage or {}).get("prompt_tokens"),
                "completion_tokens": (usage or {}).get("completion_tokens"),
                "total_tokens": (usage or {}).get("total_tokens"),
                "error": error,
            }
        ).execute()
    except Exception as e:  # noqa: BLE001
        logger.debug("request log insert failed: %s", e)


async def _stream_from_upstream(
    url: str, body: dict[str, Any], headers: dict[str, str]
) -> AsyncGenerator[bytes, None]:
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, json=body, headers=headers) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode(errors="replace")
                yield (
                    f'data: {{"error":"upstream {r.status_code}: '
                    f'{text[:200]}"}}\n\n'
                ).encode()
                return
            async for chunk in r.aiter_raw():
                yield chunk


async def _proxy(
    path: str,
    request: Request,
    sb: Client,
    api_key_id: str | None = None,
    principal: "ApiPrincipal | None" = None,
) -> Response | StreamingResponse:
    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid JSON: {e}")
    return await _proxy_with_body(
        path, body, request, sb, api_key_id=api_key_id, principal=principal
    )


async def _proxy_with_body(
    path: str,
    body: dict[str, Any],
    request: Request,
    sb: Client,
    api_key_id: str | None = None,
    principal: "ApiPrincipal | None" = None,
) -> Response | StreamingResponse:
    t0 = time.perf_counter()

    model = body.get("model")
    if not model:
        _log_request(
            sb, api_key_id=api_key_id, instance_id=None, model_slug=None,
            path=path, streaming=False, status_code=400,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error="missing model field",
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'model' field")

    # Scope check: does this API key have access to this model?
    # Accept either slug ("qwen2.5-7b-instruct") or HF repo ("Qwen/...").
    if principal is not None:
        slug_form = model.lower().split("/")[-1] if "/" in model else model.lower()
        if not principal.can_use_model(slug_form):
            _log_request(
                sb, api_key_id=api_key_id, instance_id=None, model_slug=slug_form,
                path=path, streaming=False, status_code=403,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                error="model not in allowed_models",
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"API key not authorized for model '{model}'",
            )

    picked = pick_instance(model, sb)

    if not picked:
        # Wake-on-request: if a paused / waking instance exists, resume it
        # and block this request until vLLM is healthy again.
        dormant = find_dormant_instance(model, sb)
        if not dormant:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"No instance for model '{model}' (none ready or paused)",
            )
        instance, model_row = dormant
        if instance["status"] == "paused":
            logger.info(
                "Wake-on-request: resuming %s for model=%s",
                instance["id"], model,
            )
            try:
                await resume_instance(
                    instance_id=instance["id"],
                    vast=request.app.state.vast,
                    sb=sb,
                )
            except Exception as e:  # noqa: BLE001
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, f"Resume failed: {e}"
                ) from e

        ready = await wait_for_ready(instance["id"], sb)
        if not ready:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                "Instance woke but vLLM did not become healthy in time",
            )
        instance = ready
    else:
        instance, model_row = picked

    # Stamp last_request_at so the idle auto-pauser knows this box is in use.
    try:
        sb.table("instances").update({"last_request_at": "now()"}).eq(
            "id", instance["id"]
        ).execute()
    except Exception:  # noqa: BLE001
        pass

    # Rewrite body.model to what vLLM expects (HF repo path).
    body["model"] = model_row["hf_repo"]

    url = _upstream_url(instance["ip"], instance["port"], path)
    is_stream = bool(body.get("stream"))

    # Per-instance vLLM key for boxes rented with --api-key. Older instances
    # (null column) just get no Authorization header and still work.
    upstream_headers: dict[str, str] = {}
    vllm_key = instance.get("vllm_api_key")
    if vllm_key:
        upstream_headers["Authorization"] = f"Bearer {vllm_key}"

    if is_stream:
        # Log stream start (status 200, no token counts — would need to parse
        # SSE final chunk; punt on that for M5 iteration).
        _log_request(
            sb, api_key_id=api_key_id, instance_id=instance["id"],
            model_slug=model_row["slug"], path=path, streaming=True,
            status_code=200,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        return StreamingResponse(
            _stream_from_upstream(url, body, upstream_headers),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
            },
        )

    async with httpx.AsyncClient(timeout=None) as client:
        try:
            r = await client.post(url, json=body, headers=upstream_headers)
        except httpx.HTTPError as e:
            logger.warning("Upstream %s failed: %s", url, e)
            _log_request(
                sb, api_key_id=api_key_id, instance_id=instance["id"],
                model_slug=model_row["slug"], path=path, streaming=False,
                status_code=502,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                error=str(e),
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Upstream unreachable: {e}"
            ) from e

    usage: dict[str, Any] | None = None
    try:
        usage = r.json().get("usage") if r.status_code < 400 else None
    except Exception:  # noqa: BLE001
        pass

    _log_request(
        sb, api_key_id=api_key_id, instance_id=instance["id"],
        model_slug=model_row["slug"], path=path, streaming=False,
        status_code=r.status_code,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        usage=usage,
    )
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    sb: Client = Depends(get_service_client),
    principal: ApiPrincipal = Depends(require_api_key),
):
    return await _proxy(
        "/v1/chat/completions", request, sb,
        api_key_id=principal.id, principal=principal,
    )


def _apply_user_template(
    messages: list[dict[str, Any]], template: str | None
) -> list[dict[str, Any]]:
    """Wrap the last user message content with `template` (supports {{input}}).
    If no template, return messages unchanged.
    """
    if not template:
        return messages
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            raw = out[i].get("content", "")
            out[i] = {**out[i], "content": template.replace("{{input}}", str(raw))}
            break
    return out


async def _post_webhook(
    url: str, headers: dict[str, str] | None, payload: Any
) -> int:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload, headers=headers or {})
        return r.status_code


@router.post("/v1/pipelines/{slug}/chat")
async def pipeline_chat(
    slug: str,
    request: Request,
    sb: Client = Depends(get_service_client),
    principal: ApiPrincipal = Depends(require_api_key),
):
    if not principal.can_use_pipeline(slug):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"API key not authorized for pipeline '{slug}'",
        )

    res = (
        sb.table("pipelines")
        .select("*")
        .eq("slug", slug)
        .eq("enabled", True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Pipeline '{slug}' not found or disabled"
        )
    pipe = res.data[0]

    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid JSON: {e}")

    # ---------------- INPUT stage ----------------
    messages = body.get("messages") or []
    messages = _apply_user_template(messages, pipe.get("user_template"))
    if pipe.get("system_prompt"):
        if not messages or messages[0].get("role") != "system":
            messages = [
                {"role": "system", "content": pipe["system_prompt"]},
                *messages,
            ]
    body["messages"] = messages

    # ---------------- PROCESS stage --------------
    body["model"] = pipe["model_slug"]
    for k, v in (pipe.get("vllm_overrides") or {}).items():
        body[k] = v  # e.g. temperature, max_tokens

    resp_fmt = pipe.get("response_format") or "text"
    if resp_fmt == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif resp_fmt == "json_schema":
        if not pipe.get("response_schema"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "pipeline set to json_schema but response_schema is empty",
            )
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": pipe["response_schema"],
        }

    output_mode = pipe.get("output_mode") or "return"

    # Webhook + json_only need the full response — force non-streaming.
    if output_mode in ("webhook", "json_only"):
        body["stream"] = False

    # ---------------- OUTPUT stage ---------------
    if output_mode == "return":
        return await _proxy_with_body(
            "/v1/chat/completions", body, request, sb,
            api_key_id=principal.id, principal=principal,
        )

    # Non-streaming output modes: get the full response first.
    response = await _proxy_with_body(
        "/v1/chat/completions", body, request, sb,
        api_key_id=principal.id, principal=principal,
    )
    # _proxy_with_body returns a starlette Response; pull JSON out.
    if not isinstance(response, Response):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Upstream returned a stream when a body was expected"
        )
    try:
        data = json.loads(response.body.decode())
    except Exception as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Upstream response was not JSON: {e}"
        ) from e

    if output_mode == "json_only":
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get(
            "content", ""
        )
        try:
            parsed = json.loads(content)
        except Exception as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Model did not return valid JSON: {e}",
            ) from e
        return JSONResponse(parsed)

    # output_mode == "webhook"
    url = pipe.get("webhook_url")
    if not url:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "pipeline output_mode=webhook but webhook_url is empty",
        )
    hdrs = pipe.get("webhook_headers") or {}
    try:
        webhook_status = await _post_webhook(url, hdrs, data)
    except Exception as e:
        logger.warning("webhook POST to %s failed: %s", url, e)
        return JSONResponse(
            {"ok": False, "webhook_error": str(e), "response": data},
            status_code=502,
        )
    return JSONResponse(
        {
            "ok": True,
            "webhook_url": url,
            "webhook_status": webhook_status,
            "usage": data.get("usage"),
        }
    )


@router.post("/v1/completions")
async def completions(
    request: Request,
    sb: Client = Depends(get_service_client),
    principal: ApiPrincipal = Depends(require_api_key),
):
    return await _proxy(
        "/v1/completions", request, sb,
        api_key_id=principal.id, principal=principal,
    )


# Staff-only alias for the dashboard playground — authenticates via Supabase
# JWT instead of requiring an sc_live_ API key. Logs with api_key_id=null.
@router.post("/admin/playground/chat")
async def playground_chat(
    request: Request,
    sb: Client = Depends(get_service_client),
    _user: dict = Depends(get_staff_user),
):
    return await _proxy("/v1/chat/completions", request, sb, api_key_id=None)


@router.get("/v1/models")
def list_models(
    sb: Client = Depends(get_service_client),
    principal: ApiPrincipal = Depends(require_api_key),
):
    rows = (
        sb.table("models")
        .select("slug,hf_repo,created_at")
        .eq("enabled", True)
        .execute()
        .data
        or []
    )
    visible = [r for r in rows if principal.can_use_model(r["slug"])]
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": r["slug"],
                    "object": "model",
                    "owned_by": "serverless-core",
                    "hf_repo": r["hf_repo"],
                }
                for r in visible
            ],
        }
    )
