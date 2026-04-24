"""Thin async wrapper around the Anthropic SDK's Message Batches API.

Used by the `/admin/datasets` endpoints + the `dataset_poller` background
task. The SDK is sync, so every call is wrapped in asyncio.to_thread for
use inside FastAPI's async request cycle.

Design:
- submit_batch(system, user_prompts, model, max_tokens) — builds one
  MessageBatch with N requests; each request pins cache_control on the
  last system block so the shared prefix caches after the first request.
- retrieve_batch(batch_id) — returns current {processing_status, request_counts}.
- iter_results(batch_id) — yields {custom_id, result, message, usage}.
- cancel_batch(batch_id) — idempotent cancel.

Pricing in $/1M tokens (for cost tracking; read at query time, not hardcoded):
- claude-opus-4-7:   $5 in / $25 out  (batch 50% → $2.50 / $12.50;
                                        cached read ~$0.25)
- claude-sonnet-4-6: $3 in / $15 out  (batch 50% → $1.50 / $7.50)
- claude-haiku-4-5:  $1 in / $5 out   (batch 50% → $0.50 / $2.50)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterator

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

logger = logging.getLogger("serverless_core_api.anthropic_client")

# Per-token pricing in USD per million tokens for each supported model.
# Batch API applies a 50% discount on top. Cached reads are ~10% of input.
# Cache writes (5-min TTL) are ~1.25× input.
# Update when Anthropic publishes new pricing.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7":   {"input": 5.0,  "output": 25.0},
    "claude-opus-4-6":   {"input": 5.0,  "output": 25.0},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0},
}

SUPPORTED_MODELS = list(PRICING.keys())


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    is_batch: bool = True,
) -> float:
    """Estimate USD cost for a single request's usage breakdown.

    All _input_tokens fields are mutually exclusive (cache read vs. cache write
    vs. uncached input all tracked separately by the API).
    """
    if model not in PRICING:
        return 0.0
    p = PRICING[model]
    multiplier = 0.5 if is_batch else 1.0
    cost = 0.0
    # Regular input tokens at full input price × batch discount
    cost += (input_tokens / 1_000_000) * p["input"] * multiplier
    # Cached reads at ~10% of input price × batch discount
    cost += (cache_read_input_tokens / 1_000_000) * p["input"] * 0.1 * multiplier
    # Cache creation writes at ~1.25× input price × batch discount
    cost += (cache_creation_input_tokens / 1_000_000) * p["input"] * 1.25 * multiplier
    # Output tokens at output price × batch discount
    cost += (output_tokens / 1_000_000) * p["output"] * multiplier
    return round(cost, 6)


class AnthropicBatchClient:
    """Thin async wrapper. Prefers the SDK over raw HTTP per the claude-api skill."""

    def __init__(self, api_key: str) -> None:
        # SDK is sync — every method below uses asyncio.to_thread to dispatch.
        self._client = anthropic.Anthropic(api_key=api_key)

    async def submit_batch(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompts: list[str],
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> dict[str, Any]:
        """Build a Batch with one request per user prompt.

        The system prompt gets `cache_control: ephemeral` on its text block
        so repeated requests within the batch pay ~10% of input cost for it.
        """
        if not user_prompts:
            raise ValueError("user_prompts cannot be empty")
        if model not in SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported model '{model}'. Supported: {SUPPORTED_MODELS}"
            )

        # Build the shared system block. When cache_system is True, wrap it as
        # a list of content blocks with cache_control on the last one — this
        # is the caching-friendly shape per the Messages API.
        if cache_system and system_prompt:
            system_param: Any = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = system_prompt or anthropic.NOT_GIVEN

        requests = [
            Request(
                custom_id=f"row-{i}",
                params=MessageCreateParamsNonStreaming(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_param,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
            for i, prompt in enumerate(user_prompts)
        ]

        def _submit() -> Any:
            return self._client.messages.batches.create(requests=requests)

        batch = await asyncio.to_thread(_submit)
        logger.info(
            "Anthropic batch submitted: id=%s count=%d model=%s",
            batch.id, len(user_prompts), model,
        )
        return {
            "id": batch.id,
            "processing_status": batch.processing_status,
            "request_counts": {
                "processing": batch.request_counts.processing,
                "succeeded": batch.request_counts.succeeded,
                "errored": batch.request_counts.errored,
                "canceled": batch.request_counts.canceled,
                "expired": batch.request_counts.expired,
            },
            "created_at": str(batch.created_at),
        }

    async def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        def _get() -> Any:
            return self._client.messages.batches.retrieve(batch_id)

        b = await asyncio.to_thread(_get)
        return {
            "id": b.id,
            "processing_status": b.processing_status,
            "request_counts": {
                "processing": b.request_counts.processing,
                "succeeded": b.request_counts.succeeded,
                "errored": b.request_counts.errored,
                "canceled": b.request_counts.canceled,
                "expired": b.request_counts.expired,
            },
            "ended_at": str(b.ended_at) if b.ended_at else None,
        }

    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        def _cancel() -> Any:
            return self._client.messages.batches.cancel(batch_id)

        b = await asyncio.to_thread(_cancel)
        return {"id": b.id, "processing_status": b.processing_status}

    async def iter_results(self, batch_id: str) -> list[dict[str, Any]]:
        """Fetch all results for a batch. Returns a list for simplicity;
        for our 5000-row cap the memory cost is fine.

        Each entry: {custom_id, type, output?, usage?, error?}
        """
        def _iter() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            results: Iterator[Any] = self._client.messages.batches.results(batch_id)
            for r in results:
                row: dict[str, Any] = {
                    "custom_id": r.custom_id,
                    "type": r.result.type,
                }
                if r.result.type == "succeeded":
                    msg = r.result.message
                    text = next(
                        (b.text for b in msg.content if b.type == "text"), ""
                    )
                    u = msg.usage
                    row["output"] = text
                    row["usage"] = {
                        "input_tokens": getattr(u, "input_tokens", 0) or 0,
                        "output_tokens": getattr(u, "output_tokens", 0) or 0,
                        "cache_read_input_tokens": getattr(
                            u, "cache_read_input_tokens", 0
                        ) or 0,
                        "cache_creation_input_tokens": getattr(
                            u, "cache_creation_input_tokens", 0
                        ) or 0,
                    }
                elif r.result.type == "errored":
                    row["error"] = (
                        f"{r.result.error.type}: {r.result.error.message}"
                    )
                else:
                    row["error"] = f"result_type={r.result.type}"
                out.append(row)
            return out

        return await asyncio.to_thread(_iter)
