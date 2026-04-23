"""OpenAI-compatible reverse proxy.

Clients POST to `/v1/chat/completions` (or /v1/completions) on our Fly API
with their usual OpenAI-shaped body; we pick a healthy vast.ai instance and
forward the request, passing streaming SSE back through transparently.

M2.5 scope: no auth yet (M5 adds API keys).
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from supabase import Client

from serverless_core_api.deps import get_service_client, require_api_key
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
) -> Response | StreamingResponse:
    t0 = time.perf_counter()
    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        _log_request(
            sb, api_key_id=api_key_id, instance_id=None, model_slug=None,
            path=path, streaming=False, status_code=400,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=f"invalid JSON: {e}",
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid JSON: {e}")

    model = body.get("model")
    if not model:
        _log_request(
            sb, api_key_id=api_key_id, instance_id=None, model_slug=None,
            path=path, streaming=False, status_code=400,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error="missing model field",
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'model' field")

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
    api_key_id: str = Depends(require_api_key),
):
    return await _proxy("/v1/chat/completions", request, sb, api_key_id=api_key_id)


@router.post("/v1/completions")
async def completions(
    request: Request,
    sb: Client = Depends(get_service_client),
    api_key_id: str = Depends(require_api_key),
):
    return await _proxy("/v1/completions", request, sb, api_key_id=api_key_id)


@router.get("/v1/models")
def list_models(
    sb: Client = Depends(get_service_client),
    _api_key_id: str = Depends(require_api_key),
):
    rows = (
        sb.table("models")
        .select("slug,hf_repo,created_at")
        .eq("enabled", True)
        .execute()
        .data
        or []
    )
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
                for r in rows
            ],
        }
    )
