"""Fleet replicator — opt-in per model.

For each model where `auto_replicate=true`, maintain `desired_replicas` live
instances. Live = status ∈ {provisioning, booting, ready, paused, waking}.
If count < desired, rent one using the model's policy. If count > desired,
destroy the oldest excess.

Models with `auto_replicate=false` (the default) are skipped entirely, so a
fresh install with the default seed sees zero auto-rentals until the operator
opts in on the Models page.

Why live-count includes paused/waking:
  • a paused instance can wake on demand — it counts as capacity
  • a waking instance is already being resumed, no point renting more
  • interruptible instances destroyed by vast are flipped to `destroyed` by
    status_poller, so they leave the count and trigger a replacement naturally

Failure modes (each handled, none fatal to the loop):
  • no matching offer → log + try again next tick
  • vast rejects create → log + try again next tick
  • cool-down between rentals per model so a flapping search doesn't burn $

The loop is safe to run always; when no model has auto_replicate=true it's a
cheap SELECT that returns nothing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client

from serverless_core_api.config import Settings
from serverless_core_api.services.offer_picker import pick_offer_for_model
from serverless_core_api.services.rental import (
    destroy_instance,
    rent_instance,
)
from serverless_core_api.vast import VastClient

logger = logging.getLogger("serverless_core_api.replicator")

TICK_INTERVAL_S = 30.0
# After we rent an instance, don't try again for this model for N seconds —
# prevents a flapping "rent → destroyed → rent" loop when a model's policy
# is unsatisfiable.
RENT_COOLDOWN_S = 60.0

# Statuses that count as "alive" for replica accounting.
LIVE_STATUSES = ("provisioning", "booting", "ready", "paused", "waking")


# In-process state — no DB table needed. Just a per-model timestamp of the
# last rent attempt so we respect RENT_COOLDOWN_S across ticks.
_last_rent_attempt: dict[str, datetime] = {}


def _within_cooldown(model_id: str) -> bool:
    last = _last_rent_attempt.get(model_id)
    if not last:
        return False
    return (datetime.now(timezone.utc) - last) < timedelta(seconds=RENT_COOLDOWN_S)


def _touch_cooldown(model_id: str) -> None:
    _last_rent_attempt[model_id] = datetime.now(timezone.utc)


async def _reconcile_model(
    model: dict[str, Any],
    *,
    vast: VastClient,
    sb: Client,
    settings: Settings,
) -> None:
    desired = int(model.get("desired_replicas") or 0)
    model_id = model["id"]

    rows = (
        sb.table("instances")
        .select("id,status,created_at")
        .eq("model_id", model_id)
        .in_("status", list(LIVE_STATUSES))
        .order("created_at", desc=False)
        .execute()
        .data
        or []
    )
    alive = len(rows)

    if alive == desired:
        return

    if alive < desired:
        needed = desired - alive
        if _within_cooldown(model_id):
            logger.debug(
                "replicator: %s needs %d more but cooldown active",
                model["slug"], needed,
            )
            return
        logger.info(
            "replicator: %s has %d/%d live replicas — renting 1",
            model["slug"], alive, desired,
        )
        _touch_cooldown(model_id)  # set BEFORE the rent so failures also cool down
        try:
            offer = await pick_offer_for_model(model=model, vast=vast, sb=sb)
            if not offer:
                logger.warning(
                    "replicator: no offer available for %s (mode=%s max_dph=%s)",
                    model["slug"], model.get("rental_mode"),
                    model.get("max_dph") or model.get("max_bid_dph"),
                )
                return
            await rent_instance(
                offer_id=int(offer["id"]),
                model_id=model_id,
                model_slug=None,
                vast=vast,
                sb=sb,
                settings=settings,
                auto_replicated=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "replicator: rent failed for %s: %s", model["slug"], e
            )
        return

    # alive > desired — destroy the oldest excess. Only touch instances
    # we auto-rented; never kill a hand-rented instance.
    excess = alive - desired
    auto_rows = (
        sb.table("instances")
        .select("id,status,created_at,auto_replicated")
        .eq("model_id", model_id)
        .in_("status", list(LIVE_STATUSES))
        .eq("auto_replicated", True)
        .order("created_at", desc=False)
        .execute()
        .data
        or []
    )
    for row in auto_rows[:excess]:
        logger.info(
            "replicator: %s has %d/%d — destroying auto-rented %s",
            model["slug"], alive, desired, row["id"][:8],
        )
        try:
            await destroy_instance(instance_id=row["id"], vast=vast, sb=sb)
        except Exception as e:  # noqa: BLE001
            logger.warning("replicator: destroy %s failed: %s", row["id"], e)


async def tick_once(
    vast: VastClient, sb: Client, settings: Settings
) -> None:
    models = (
        sb.table("models")
        .select("*")
        .eq("enabled", True)
        .eq("auto_replicate", True)
        .execute()
        .data
        or []
    )
    if not models:
        return
    # Serial to avoid bursting the vast API + to keep logs readable.
    for m in models:
        try:
            await _reconcile_model(m, vast=vast, sb=sb, settings=settings)
        except Exception as e:  # noqa: BLE001
            logger.warning("replicator: model=%s reconcile errored: %s",
                           m.get("slug"), e)


async def run_forever(
    vast: VastClient, sb: Client, settings: Settings
) -> None:
    logger.info(
        "Replicator started (tick every %ss; cooldown %ss/model)",
        TICK_INTERVAL_S, RENT_COOLDOWN_S,
    )
    while True:
        try:
            await tick_once(vast, sb, settings)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Replicator tick errored: %s", e)
        await asyncio.sleep(TICK_INTERVAL_S)
