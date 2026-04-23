"""OpenAI-compatible reverse proxy.

Clients POST to `/v1/chat/completions` (or /v1/completions) on our Fly API
with their usual OpenAI-shaped body; we pick a healthy vast.ai instance and
forward the request, passing streaming SSE back through transparently.

M2.5 scope: no auth yet (M5 adds API keys).
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from supabase import Client

from serverless_core_api.deps import get_service_client, require_api_key
from serverless_core_api.services.routing import pick_instance

logger = logging.getLogger("serverless_core_api.proxy")

router = APIRouter(tags=["proxy"])


def _upstream_url(ip: str, port: int, path: str) -> str:
    return f"http://{ip}:{port}{path}"


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
    path: str, request: Request, sb: Client
) -> Response | StreamingResponse:
    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid JSON: {e}")

    model = body.get("model")
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing 'model' field")

    picked = pick_instance(model, sb)
    if not picked:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"No healthy instance for model '{model}'",
        )
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
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Upstream unreachable: {e}"
            ) from e
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    sb: Client = Depends(get_service_client),
    _api_key_id: str = Depends(require_api_key),
):
    return await _proxy("/v1/chat/completions", request, sb)


@router.post("/v1/completions")
async def completions(
    request: Request,
    sb: Client = Depends(get_service_client),
    _api_key_id: str = Depends(require_api_key),
):
    return await _proxy("/v1/completions", request, sb)


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
