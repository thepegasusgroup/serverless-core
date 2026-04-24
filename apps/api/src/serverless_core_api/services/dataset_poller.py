"""Background task: poll Anthropic for each in-flight dataset batch.

Runs every 60s (most batches finish in minutes, but the API allows up to
24h so polling a bit loosely is fine). For each dataset with status in
{submitting, running}, fetches the batch state. When Anthropic reports
processing_status=ended, streams all results into dataset_rows + updates
the dataset row with usage totals + marks completed.

No-op when ANTHROPIC_API_KEY is not configured.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from supabase import Client

from serverless_core_api.anthropic_client import (
    AnthropicBatchClient,
    estimate_cost_usd,
)

logger = logging.getLogger("serverless_core_api.dataset_poller")

POLL_INTERVAL_S = 60.0
ACTIVE_STATUSES = ("submitting", "running")


async def _reconcile(
    sb: Client, anthropic_client: AnthropicBatchClient, row: dict[str, Any]
) -> None:
    batch_id = row.get("external_batch_id")
    if not batch_id:
        return

    try:
        state = await anthropic_client.retrieve_batch(batch_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("batch %s retrieve failed: %s", batch_id, e)
        return

    counts = state.get("request_counts", {})
    patch: dict[str, Any] = {
        "progress_completed": counts.get("succeeded", 0),
        "progress_errored": counts.get("errored", 0) + counts.get("expired", 0),
    }
    ps = state.get("processing_status")

    # Not done yet — push the progress update and move on.
    if ps != "ended":
        if row.get("status") == "submitting":
            patch["status"] = "running"
        sb.table("datasets").update(patch).eq("id", row["id"]).execute()
        return

    # Batch ended. Pull all results, fan them out into dataset_rows.
    try:
        results = await anthropic_client.iter_results(batch_id)
    except Exception as e:  # noqa: BLE001
        logger.error("batch %s results fetch failed: %s", batch_id, e)
        sb.table("datasets").update(
            {"status": "failed", "error": f"results fetch: {e}"}
        ).eq("id", row["id"]).execute()
        return

    cfg = row.get("config") or {}
    model = cfg.get("model", "claude-opus-4-7")
    system_prompt = cfg.get("system", "")
    user_prompts: list[str] = cfg.get("prompts") or []

    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cost_usd": 0.0,
    }

    # Build the dataset_rows upsert payload.
    rows_to_insert: list[dict[str, Any]] = []
    for r in results:
        # custom_id format is "row-{i}" from submit_batch
        try:
            idx = int(r["custom_id"].split("-", 1)[1])
        except (KeyError, ValueError, IndexError):
            logger.warning("unparseable custom_id: %s", r.get("custom_id"))
            continue
        user_prompt = user_prompts[idx] if idx < len(user_prompts) else ""
        row_data: dict[str, Any] = {
            "dataset_id": row["id"],
            "row_index": idx,
            "input": {
                "system": system_prompt,
                "user": user_prompt,
            },
        }
        if r["type"] == "succeeded":
            row_data["output"] = r.get("output", "")
            u = r.get("usage") or {}
            row_data["usage"] = {
                **u,
                "cost_usd": estimate_cost_usd(
                    model=model,
                    input_tokens=u.get("input_tokens", 0),
                    output_tokens=u.get("output_tokens", 0),
                    cache_read_input_tokens=u.get("cache_read_input_tokens", 0),
                    cache_creation_input_tokens=u.get(
                        "cache_creation_input_tokens", 0
                    ),
                    is_batch=True,
                ),
            }
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                total_usage[k] += u.get(k, 0) or 0
            total_usage["cost_usd"] += row_data["usage"]["cost_usd"]
        else:
            row_data["error"] = r.get("error", "unknown error")
        rows_to_insert.append(row_data)

    # Insert in reasonable-sized chunks to stay under PostgREST payload caps.
    CHUNK = 500
    for i in range(0, len(rows_to_insert), CHUNK):
        sb.table("dataset_rows").upsert(
            rows_to_insert[i : i + CHUNK],
            on_conflict="dataset_id,row_index",
        ).execute()

    total_usage["cost_usd"] = round(total_usage["cost_usd"], 6)
    now = datetime.now(timezone.utc).isoformat()

    sb.table("datasets").update(
        {
            "status": (
                "completed"
                if counts.get("succeeded", 0) > 0
                else "failed"
            ),
            "progress_completed": counts.get("succeeded", 0),
            "progress_errored": counts.get("errored", 0) + counts.get("expired", 0),
            "usage": total_usage,
            "completed_at": now,
        }
    ).eq("id", row["id"]).execute()

    logger.info(
        "dataset %s reconciled: %d succeeded, %d errored, $%.4f total",
        row["slug"],
        counts.get("succeeded", 0),
        counts.get("errored", 0) + counts.get("expired", 0),
        total_usage["cost_usd"],
    )


async def poll_once(
    sb: Client, anthropic_client: AnthropicBatchClient | None
) -> None:
    if anthropic_client is None:
        return
    rows = (
        sb.table("datasets")
        .select("id,slug,status,config,external_batch_id")
        .in_("status", list(ACTIVE_STATUSES))
        .execute()
        .data
        or []
    )
    if not rows:
        return
    await asyncio.gather(
        *(_reconcile(sb, anthropic_client, row) for row in rows),
        return_exceptions=True,
    )


async def run_forever(
    sb: Client, anthropic_client: AnthropicBatchClient | None
) -> None:
    if anthropic_client is None:
        logger.info("Dataset poller disabled: ANTHROPIC_API_KEY not configured")
        return
    logger.info("Dataset poller started (every %ss)", POLL_INTERVAL_S)
    while True:
        try:
            await poll_once(sb, anthropic_client)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("dataset poller tick errored: %s", e)
        await asyncio.sleep(POLL_INTERVAL_S)
