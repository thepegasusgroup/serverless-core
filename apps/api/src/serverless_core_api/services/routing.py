from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client

logger = logging.getLogger("serverless_core_api.routing")

HEARTBEAT_FRESHNESS_S = 90


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
