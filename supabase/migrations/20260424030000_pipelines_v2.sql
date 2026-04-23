-- Pipelines v2: Input / Process / Output model.
--
-- user_template      : optional Jinja-lite wrap for the user message,
--                      supports {{input}} for raw user content.
-- vllm_overrides     : merged into forwarded body — e.g. temperature, max_tokens.
-- response_format    : 'text' | 'json_object' | 'json_schema' (forwarded to vLLM).
-- response_schema    : used when response_format = 'json_schema'.
-- output_mode        : 'return'     → proxy response to client (default)
--                      'webhook'    → POST response to webhook_url; client gets ack
--                      'json_only'  → parse message.content as JSON, return that only.
-- webhook_url / headers : target + auth for 'webhook' mode.
-- timeout_seconds    : cap on end-to-end time before 504.

alter table public.pipelines
  add column if not exists user_template text,
  add column if not exists vllm_overrides jsonb default '{}'::jsonb,
  add column if not exists response_format text default 'text',
  add column if not exists response_schema jsonb,
  add column if not exists output_mode text default 'return' check (output_mode in ('return','webhook','json_only')),
  add column if not exists webhook_url text,
  add column if not exists webhook_headers jsonb default '{}'::jsonb,
  add column if not exists timeout_seconds int default 120;
