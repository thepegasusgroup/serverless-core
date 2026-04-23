"""Background task that polls vast.ai for each in-flight instance and writes
a human-readable stage message back to Supabase. The dashboard subscribes to
the `instances` table via Realtime so users see updates live.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from supabase import Client

from serverless_core_api.vast import VastClient

logger = logging.getLogger("serverless_core_api.status_poller")

# Statuses whose vast.ai state we keep watching.
_ACTIVE_STATUSES = ("provisioning", "booting")
POLL_INTERVAL_S = 15.0


def _short(s: str | None, limit: int = 120) -> str:
    if not s:
        return ""
    s = s.strip().splitlines()[0]
    return s if len(s) <= limit else s[: limit - 1] + "…"


def build_stage_msg(vast_info: dict[str, Any], our_status: str) -> str:
    actual = (vast_info.get("actual_status") or "").lower()
    raw = _short(vast_info.get("status_msg"))

    if actual in ("offline", "stopped"):
        return f"Container stopped · {raw}" if raw else "Container stopped"
    if actual == "exited":
        return f"Container exited · {raw}" if raw else "Container exited"
    if actual == "loading":
        if raw:
            return f"Pulling image · {raw}"
        return "Pulling image from GHCR"
    if actual == "running":
        if our_status == "provisioning":
            if raw:
                return f"Container running · {raw}"
            return "Container running, waiting for vLLM to boot"
        if our_status == "booting":
            return "vLLM booting (downloading model / loading weights)"
    return raw or actual or ""


async def _poll_one(
    vast: VastClient, sb: Client, row: dict[str, Any]
) -> None:
    cid = row.get("vast_contract_id")
    if not cid:
        return
    try:
        info = await vast.show_instance(int(cid))
    except Exception as e:  # noqa: BLE001
        logger.debug("show_instance(%s) failed: %s", cid, e)
        return

    i = info.get("instances", info) if isinstance(info, dict) else {}
    if not isinstance(i, dict):
        return

    stage_msg = build_stage_msg(i, row.get("status", ""))
    vast_actual = i.get("actual_status")

    patch: dict[str, Any] = {}
    if stage_msg != row.get("stage_msg"):
        patch["stage_msg"] = stage_msg
    if vast_actual != row.get("vast_actual_status"):
        patch["vast_actual_status"] = vast_actual
    if patch:
        sb.table("instances").update(patch).eq("id", row["id"]).execute()


async def poll_once(vast: VastClient, sb: Client) -> None:
    rows = (
        sb.table("instances")
        .select("id,vast_contract_id,status,stage_msg,vast_actual_status")
        .in_("status", list(_ACTIVE_STATUSES))
        .execute()
        .data
        or []
    )
    if not rows:
        return
    await asyncio.gather(*(_poll_one(vast, sb, row) for row in rows))


async def poll_forever(vast: VastClient, sb: Client) -> None:
    logger.info("Status poller started (every %ss)", POLL_INTERVAL_S)
    while True:
        try:
            await poll_once(vast, sb)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("status poller tick errored: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)
