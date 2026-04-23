from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client

logger = logging.getLogger("serverless_core_api.routing")

HEARTBEAT_FRESHNESS_S = 90
WAKE_TIMEOUT_S = 240  # 4 min — paused boxes come back in ~60-120s (model cached)
WAKE_POLL_S = 2.0


def _resolve_model(slug_or_repo: str, sb: Client) -> dict[str, Any] | None:
    # Accept either our slug ("qwen2.5-7b-instruct") or the HF repo
    # ("Qwen/Qwen2.5-7B-Instruct") that vLLM uses internally.
    if "/" in slug_or_repo:
        res = (
            sb.table("models")
            .select("id,slug,hf_repo")
            .eq("hf_repo", slug_or_repo)
            .eq("enabled", True)
            .limit(1)
            .execute()
        )
    else:
        res = (
            sb.table("models")
            .select("id,slug,hf_repo")
            .eq("slug", slug_or_repo.lower())
            .eq("enabled", True)
            .limit(1)
            .execute()
        )
    return res.data[0] if res.data else None


def pick_instance(model: str, sb: Client) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return (instance_row, model_row) for a healthy instance serving the model,
    or None if no match.
    """
    m = _resolve_model(model, sb)
    if not m:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_FRESHNESS_S)
    res = (
        sb.table("instances")
        .select("*")
        .eq("model_id", m["id"])
        .eq("status", "ready")
        .gte("last_heartbeat_at", cutoff.isoformat())
        .order("last_heartbeat_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0], m


def find_dormant_instance(
    model: str, sb: Client
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Find a paused or currently-waking instance for this model (most recent first).
    Used by the proxy to wake-on-request.
    """
    m = _resolve_model(model, sb)
    if not m:
        return None
    res = (
        sb.table("instances")
        .select("*")
        .eq("model_id", m["id"])
        .in_("status", ["waking", "paused"])
        .order("paused_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0], m


async def wait_for_ready(
    instance_id: str,
    sb: Client,
    timeout_s: int = WAKE_TIMEOUT_S,
    poll_s: float = WAKE_POLL_S,
) -> dict[str, Any] | None:
    """Poll the DB until the instance is `ready` with an IP, or time out.
    Returns the refreshed row, or None on timeout / terminal failure.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = (
            sb.table("instances")
            .select("*")
            .eq("id", instance_id)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            status = row.get("status")
            if status == "ready" and row.get("ip"):
                return row
            if status in ("destroyed", "unhealthy"):
                logger.warning(
                    "wait_for_ready: instance %s terminal status=%s",
                    instance_id, status,
                )
                return None
        await asyncio.sleep(poll_s)
    logger.warning("wait_for_ready: timed out for %s", instance_id)
    return None
