# Datasets — synthetic data generation

The `/datasets` page turns one shared system prompt + a list of user prompts
into a training-ready JSONL dataset via the Claude Message Batches API.
Typical use case: generating fine-tuning data for a smaller model.

## Why use this (vs. scripting it yourself)

1. **Prompt caching on the shared system prompt.** A 5 KB system prompt sent
   once per row × 5000 rows = 25 MB of repeated tokens. With `cache_control`
   enabled, you pay the full input price for it **once** (first row), then
   ~10% of input price on every row after. Typical savings: 80-90% on the
   system-prompt portion of the bill.
2. **Batch API is already 50% off standard prices.** Stacked with caching,
   a 5000-row dataset on Opus 4.7 typically runs $3-8 instead of $30-50.
3. **No CLI ops.** Dashboard takes care of submit → poll → import → export.
   Realtime status updates via Supabase. One-click JSONL/CSV download.

## Prerequisites

- `ANTHROPIC_API_KEY` set as a Fly secret on the API app
  (`fly secrets set ANTHROPIC_API_KEY=sk-ant-... -a sc-api-thepegasus`).
  Without it, `/admin/datasets` endpoints return 503 and the background
  poller is a no-op.

## Models supported

| Model ID | Context | Batch input $/1M | Batch output $/1M | Cached read $/1M |
|---|---|---|---|---|
| `claude-opus-4-7` | 1M | $2.50 | $12.50 | ~$0.25 |
| `claude-opus-4-6` | 1M | $2.50 | $12.50 | ~$0.25 |
| `claude-sonnet-4-6` | 1M | $1.50 | $7.50 | ~$0.15 |
| `claude-haiku-4-5` | 200K | $0.50 | $2.50 | ~$0.05 |

## Workflow

### Create a dataset

1. Go to `/datasets` → **New dataset**
2. Fill in:
   - **Slug** (URL-friendly, lowercase, e.g. `mc-planner-v1`)
   - **Label** (human-readable)
   - **Model** (Opus 4.7 by default)
   - **System prompt** — the long shared instruction that defines the task/schema
   - **User prompts** (one per line) — up to 5000 rows
   - **Max tokens per response** — default 4096
   - **Cache system prompt** — leave on unless you have a reason not to
   - **Submit immediately** — off saves as draft, on sends to Anthropic
3. Save. A background poller checks every 60 s for completion.

### Monitor progress

The dataset list page auto-updates via Supabase Realtime. Status progresses:

```
draft → submitting → running → completed
                            ↘ failed
                            ↘ canceled
```

- `running` shows `X/Y` completed rows + current spend estimate.
- Batches usually complete in 5-20 min; Anthropic allows up to 24 h.
- Click into the detail page to browse finished rows as they stream in.

### Export

Once status is `completed`:

- **JSONL** — one line per row, OpenAI-chat-messages format ready for
  fine-tuning:
  ```json
  {"messages": [
    {"role": "system", "content": "<system>"},
    {"role": "user", "content": "<user>"},
    {"role": "assistant", "content": "<response>"}
  ]}
  ```
- **CSV** — `row_index, system, user, assistant, error` — for spreadsheets
  or downstream cleaning. Errored rows keep their prompt + error; succeeded
  rows keep the full response.

## Prompt caching details

The system prompt must be ≥ 1024 tokens (Sonnet 4.6) or ≥ 4096 tokens
(Opus 4.7, Haiku 4.5) to be cacheable. Shorter prompts silently won't
cache — the API returns `cache_creation_input_tokens: 0` and charges full
price. Check the dataset's usage breakdown on the detail page:

- **cache read** tokens = paid at ~10% of input
- **cache write** tokens = paid at ~125% of input (one-time)
- **input** tokens = paid at full input price (non-cacheable, or the per-row
  user prompt which is always different)

If cache read is **0** across a completed dataset, something went wrong —
likely the system prompt was too short, or the `cache_system` toggle was
off.

## Operational notes

- **Up to 5000 rows per dataset** (pragmatic cap; Anthropic's actual limit
  is 100K per batch but we stay well under for UI responsiveness).
- **Results are stored in `public.dataset_rows`** permanently until you
  delete the dataset. Export at any time.
- **Errors inside a batch** (Anthropic-side content policy, invalid request,
  etc.) land on the row with an `error` field — they don't fail the whole
  dataset. Successful rows are still exported; errored rows are skipped in
  JSONL (and visible in CSV + UI for inspection).
- **Canceling** a running batch is best-effort; partial results already
  processed are kept.

## Cost estimate example

5000 rows × ~1.5K-token system prompt × ~50-token user prompt × ~500-token
response on Opus 4.7 with caching:

- System prompt cached: first row $0.0075 write, then 4999 × $0.00075 ≈ $3.75 cache reads
- User prompts (non-cacheable): 5000 × 50 × $2.50/M ≈ $0.63
- Responses: 5000 × 500 × $12.50/M ≈ $31.25

**Total ≈ $35.** Without caching: ~$55. Without batching: ~$70.

Haiku 4.5 on the same workload: ~**$7**.
