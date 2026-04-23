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
from serverless_core_api.services.pipeline_exec import (
    apply_transform,
    merge_usage,
    render,
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


def _extract_last_user_content(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            content = m.get("content", "")
            # content can be a string or a list of parts (OpenAI vision spec);
            # we treat lists as stringified fallback.
            return content if isinstance(content, str) else str(content)
    return ""


async def _run_model_step(
    step: dict[str, Any],
    context: dict[str, str],
    request: Request,
    sb: Client,
    principal: ApiPrincipal | None,
    api_key_id: str | None,
) -> tuple[str, dict[str, Any]]:
    """Execute one `kind=model` step. Returns (text_output, usage_dict)."""
    rendered_user = render(step.get("user_template") or "{{input}}", context)
    system = render(step.get("system") or "", context)
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": rendered_user})

    body: dict[str, Any] = {
        "model": step["model_slug"],
        "messages": messages,
        "stream": False,
    }
    for k, v in (step.get("vllm_overrides") or {}).items():
        body[k] = v

    resp_fmt = step.get("response_format") or "text"
    if resp_fmt == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif resp_fmt == "json_schema" and step.get("response_schema"):
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": step["response_schema"],
        }

    response = await _proxy_with_body(
        "/v1/chat/completions", body, request, sb,
        api_key_id=api_key_id, principal=principal,
    )
    if not isinstance(response, Response):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Step got a stream")
    try:
        data = json.loads(response.body.decode())
    except Exception as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Step response not JSON: {e}"
        ) from e
    if response.status_code >= 400:
        err = data.get("error") or data
        raise HTTPException(response.status_code, f"Step upstream error: {err}")
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get(
        "content", ""
    )
    return content, data.get("usage") or {}


async def _run_pipeline(
    slug: str,
    body: dict[str, Any],
    request: Request,
    sb: Client,
    principal: ApiPrincipal | None,
    api_key_id: str | None,
):
    """Execute a pipeline as a chain of steps, return final output."""
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
    steps = pipe.get("steps") or []
    if not isinstance(steps, list) or len(steps) == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Pipeline '{slug}' has no steps configured",
        )

    # Starting input = last user message (ignore any system the client sent;
    # pipeline steps own system messages).
    user_input = _extract_last_user_content(body.get("messages") or [])
    context: dict[str, str] = {"input": user_input, "prev": user_input}

    usage_acc: dict[str, int] = {}
    last_output = user_input

    for idx, step in enumerate(steps, start=1):
        kind = step.get("kind")
        if kind == "model":
            text, usage = await _run_model_step(
                step, context, request, sb, principal, api_key_id
            )
            merge_usage(usage_acc, usage)
            last_output = text
        elif kind == "transform":
            try:
                last_output = apply_transform(step, last_output)
            except Exception as e:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Step {idx} transform failed: {e}",
                ) from e
        else:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Step {idx} has unknown kind '{kind}'",
            )
        context[f"step_{idx}"] = last_output
        context["prev"] = last_output

    # Build an OpenAI-compatible response wrapper around the final text.
    final_payload = {
        "id": f"pipe-{slug}-{int(time.time()*1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"pipeline:{slug}",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": last_output},
                "finish_reason": "stop",
            }
        ],
        "usage": usage_acc or None,
    }

    output_mode = pipe.get("output_mode") or "return"

    if output_mode == "return":
        return JSONResponse(final_payload)

    if output_mode == "json_only":
        try:
            return JSONResponse(json.loads(last_output))
        except Exception as e:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Final step output was not valid JSON: {e}",
            ) from e

    # output_mode == "webhook"
    url = pipe.get("webhook_url")
    if not url:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "pipeline output_mode=webhook but webhook_url is empty",
        )
    hdrs = pipe.get("webhook_headers") or {}
    try:
        webhook_status = await _post_webhook(url, hdrs, final_payload)
    except Exception as e:
        logger.warning("webhook POST to %s failed: %s", url, e)
        return JSONResponse(
            {"ok": False, "webhook_error": str(e), "payload": final_payload},
            status_code=502,
        )
    return JSONResponse(
        {
            "ok": True,
            "webhook_url": url,
            "webhook_status": webhook_status,
            "usage": final_payload["usage"],
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
    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid JSON: {e}")
    return await _run_pipeline(
        slug, body, request, sb, principal=principal, api_key_id=principal.id
    )


# Staff-only aliases for the dashboard playground — authenticate via Supabase
# JWT instead of requiring an sc_live_ API key.
@router.post("/admin/playground/chat")
async def playground_chat(
    request: Request,
    sb: Client = Depends(get_service_client),
    _user: dict = Depends(get_staff_user),
):
    return await _proxy("/v1/chat/completions", request, sb, api_key_id=None)


@router.post("/admin/playground/pipeline/{slug}")
async def playground_pipeline(
    slug: str,
    request: Request,
    sb: Client = Depends(get_service_client),
    _user: dict = Depends(get_staff_user),
):
    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid JSON: {e}")
    return await _run_pipeline(
        slug, body, request, sb, principal=None, api_key_id=None
    )


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
