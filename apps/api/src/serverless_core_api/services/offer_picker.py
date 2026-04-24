"""Shared offer-filtering + picking logic.

Used by:
  • /admin/offers — human browsing via the dashboard
  • replicator — auto-rent the cheapest compliant offer for a model

Everything here is "additive": default args reproduce today's /admin/offers
behaviour. The replicator layers a model's `offer_filters` jsonb on top.
"""
from __future__ import annotations

import logging
from typing import Any

from supabase import Client

from serverless_core_api.vast import VastClient, build_offer_query

logger = logging.getLogger("serverless_core_api.offer_picker")


# Keep this list in one place — admin.py imports from here too.
REGION_SETS: dict[str, set[str]] = {
    "eu": {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
        "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
        "RO", "SK", "SI", "ES", "SE", "IS", "NO", "LI", "CH", "GB", "UA",
    },
    "us": {"US"},
    "na": {"US", "CA"},
}

# Blocked by default because vast boxes there consistently have slow / broken
# access to GHCR and HuggingFace (GFW, sanctions). Operator can override via
# offer_filters.block_countries = [] or include_blocked.
DEFAULT_BLOCKED_COUNTRIES: set[str] = {"CN", "RU", "BY", "IR", "KP", "SY"}


def country_code(offer: dict[str, Any]) -> str:
    g = offer.get("geolocation") or ""
    if "," in g:
        return g.rsplit(",", 1)[-1].strip().upper()
    return g.strip().upper()


def _filter_offers(
    raw: list[dict[str, Any]],
    *,
    regions: list[str] | None,
    block_countries: set[str],
    bad_machine_ids: set[int],
    bad_cpu_patterns: list[str] | None = None,
) -> list[dict[str, Any]]:
    out = raw
    if regions:
        target: set[str] = set()
        for r in regions:
            target |= REGION_SETS.get(r.lower(), set())
        if target:
            out = [o for o in out if country_code(o) in target]
    if block_countries:
        out = [o for o in out if country_code(o) not in block_countries]
    if bad_machine_ids:
        out = [o for o in out if o.get("machine_id") not in bad_machine_ids]
    if bad_cpu_patterns:
        def _cpu_ok(o: dict[str, Any]) -> bool:
            name = (o.get("cpu_name") or "").lower()
            if not name:
                return True  # don't reject hosts with missing CPU data
            return not any(p in name for p in bad_cpu_patterns)
        out = [o for o in out if _cpu_ok(o)]
    return out


async def fetch_bad_machine_ids(sb: Client) -> set[int]:
    rows = sb.table("bad_machines").select("machine_id").execute().data or []
    return {int(r["machine_id"]) for r in rows}


async def fetch_bad_cpu_patterns(sb: Client) -> list[str]:
    """Return lowercased substrings of CPU names to filter out.

    Matches case-insensitively against offer.cpu_name. Used to reject hosts
    with CPUs that bottleneck Docker extraction / vLLM startup, since
    vast.ai's min_cpu_ghz filter doesn't catch hosts where cpu_ghz is null.
    """
    rows = sb.table("bad_cpus").select("cpu_name").execute().data or []
    return [(r.get("cpu_name") or "").lower() for r in rows if r.get("cpu_name")]


async def pick_offer_for_model(
    *,
    model: dict[str, Any],
    vast: VastClient,
    sb: Client,
) -> dict[str, Any] | None:
    """Return the cheapest compliant offer for `model`, or None if nothing fits.

    Reads ALL filters from the model row (including the free-form
    `offer_filters` jsonb). Nothing model-specific is hardcoded here — every
    knob is optional.
    """
    extras: dict[str, Any] = dict(model.get("offer_filters") or {})

    rental_mode = model.get("rental_mode") or "on_demand"
    # Price ceiling: for interruptible, max_bid_dph (the bid) is also a hard
    # upper bound on the offer's spot price — no point fetching unaffordable
    # offers. For on-demand, max_dph is the ceiling.
    if rental_mode == "interruptible":
        max_dph = model.get("max_bid_dph") or model.get("max_dph")
    else:
        max_dph = model.get("max_dph")

    # `offer_filters.allowed_gpus` lets an operator widen the pool beyond a
    # single gpu_name (e.g., ["RTX 3090","RTX 3090 Ti","RTX 4090"] is the
    # classic "cheap consumer card" pool). Takes precedence over gpu_name.
    allowed_gpus = extras.get("allowed_gpus")
    gpu_param: str | list[str] | None
    if allowed_gpus:
        gpu_param = [g for g in allowed_gpus if g]
    else:
        gpu_param = model.get("gpu_name")

    query = build_offer_query(
        gpu=gpu_param,
        max_dph=float(max_dph) if max_dph is not None else None,
        min_vram_gb=model.get("min_vram_gb"),
        num_gpus=int(model.get("num_gpus") or 1),
        min_reliability=float(extras.get("min_reliability", 0.95)),
        verified=extras.get("verified"),
        min_cpu_cores=extras.get("min_cpu_cores"),
        min_cpu_ghz=extras.get("min_cpu_ghz"),
        min_inet_down_mbps=extras.get("min_inet_down_mbps"),
        datacenter_only=bool(extras.get("datacenter_only", False)),
        rental_mode=rental_mode,
    )

    try:
        raw = await vast.search_offers(query)
    except Exception as e:  # noqa: BLE001
        logger.warning("offer_picker: vast search failed for model=%s: %s",
                       model.get("slug"), e)
        return None

    regions = extras.get("regions")
    if isinstance(regions, str):
        regions = [regions]
    block_countries = DEFAULT_BLOCKED_COUNTRIES | {
        c.upper() for c in (extras.get("block_countries") or [])
    }
    # Operators can also explicitly un-block countries.
    for c in (extras.get("allow_countries") or []):
        block_countries.discard(c.upper())

    bad_ids = await fetch_bad_machine_ids(sb)
    bad_cpus = await fetch_bad_cpu_patterns(sb)
    filtered = _filter_offers(
        raw,
        regions=regions,
        block_countries=block_countries,
        bad_machine_ids=bad_ids,
        bad_cpu_patterns=bad_cpus,
    )
    if not filtered:
        logger.info(
            "offer_picker: no offers for model=%s after filters (raw=%d)",
            model.get("slug"), len(raw),
        )
        return None

    # Cheapest first (search_offers already sorts by dph_total ascending, but
    # filters may have left holes, so sort again to be safe).
    filtered.sort(key=lambda o: o.get("dph_total", 9e9))
    return filtered[0]
