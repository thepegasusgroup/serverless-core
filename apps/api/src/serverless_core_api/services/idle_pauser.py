"""Auto-pause idle instances to save money.

Once per minute: find `ready` instances whose last_request_at (or
registered_at) is older than the per-model `auto_pause_minutes` setting,
and pause them via the vast.ai API.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client

from serverless_core_api.services.rental import pause_instance
from serverless_core_api.vast import VastClient

logger = logging.getLogger("serverless_core_api.idle_pauser")

POLL_INTERVAL_S = 60.0


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


async def sweep_once(vast: VastClient, sb: Client) -> None:
    rows = (
        sb.table("instances")
        .select(
            "id,vast_contract_id,status,model_id,last_request_at,registered_at"
        )
        .eq("status", "ready")
        .execute()
        .data
        or []
    )
    if not rows:
        return

    now = datetime.now(timezone.utc)
    # Pre-fetch model pause minutes in one query.
    model_ids = list({r["model_id"] for r in rows})
    models = (
        sb.table("models")
        .select("id,auto_pause_minutes,slug")
        .in_("id", model_ids)
        .execute()
        .data
        or []
    )
    pause_by_model = {m["id"]: m.get("auto_pause_minutes") for m in models}

    for row in rows:
        minutes = pause_by_model.get(row["model_id"])
        if not minutes:  # null / 0 → never auto-pause
            continue
        since = _parse_ts(row.get("last_request_at")) or _parse_ts(
            row.get("registered_at")
        )
        if not since:
            continue
        idle_for = now - since
        if idle_for < timedelta(minutes=minutes):
            continue
        logger.info(
            "Auto-pausing %s (idle %dm >= threshold %dm)",
            row["id"][:8],
            int(idle_for.total_seconds() / 60),
            minutes,
        )
        try:
            await pause_instance(instance_id=row["id"], vast=vast, sb=sb)
        except Exception as e:  # noqa: BLE001
            logger.warning("Auto-pause %s failed: %s", row["id"], e)


async def run_forever(vast: VastClient, sb: Client) -> None:
    logger.info("Idle auto-pauser started (tick every %ss)", POLL_INTERVAL_S)
    while True:
        try:
            await sweep_once(vast, sb)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Idle sweep errored: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)
