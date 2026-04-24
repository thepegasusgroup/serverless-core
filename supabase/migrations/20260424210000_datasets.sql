-- Synthetic dataset generation via the Claude Message Batches API.
--
-- A `dataset` is a named job that submits N prompts to Anthropic's Batches
-- endpoint and collects the assistant responses. Each successful row becomes
-- a `dataset_rows` entry ready for export as JSONL / CSV.
--
-- Design notes:
--   • Large system prompts are stored once in `datasets.config.system` and
--     repeated across every row's Batch request. Prompt caching
--     (cache_control) makes that cheap — the first request pays full price,
--     all subsequent requests in the batch pay ~10% of the input cost for
--     the cached prefix.
--   • `provider` is an enum even though we only support 'claude_batch' now —
--     future phases can add 'local_model' (our own vLLM fleet) without a
--     schema change.
--   • `external_batch_id` pins the vendor-side batch so the poller can
--     resume on server restart.

create table public.datasets (
  id                      uuid primary key default gen_random_uuid(),
  slug                    text unique not null,
  label                   text not null,
  status                  text not null default 'draft'
    check (status in ('draft','submitting','running','completed','failed','canceled')),
  provider                text not null default 'claude_batch'
    check (provider in ('claude_batch','local_model')),
  config                  jsonb not null default '{}'::jsonb,
    -- {model, max_tokens, system, prompts: [...], cache_system: bool}
  external_batch_id       text,
  progress_completed      int  not null default 0,
  progress_total          int  not null default 0,
  progress_errored        int  not null default 0,
  usage                   jsonb not null default '{}'::jsonb,
    -- aggregate: {input_tokens, output_tokens, cache_read_input_tokens,
    --            cache_creation_input_tokens, cost_usd}
  error                   text,
  submitted_at            timestamptz,
  completed_at            timestamptz,
  created_at              timestamptz not null default now()
);

create index datasets_status_idx on public.datasets (status, created_at desc);

alter table public.datasets enable row level security;

create policy staff_read_datasets on public.datasets
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));

alter publication supabase_realtime add table public.datasets;


create table public.dataset_rows (
  id             uuid primary key default gen_random_uuid(),
  dataset_id     uuid not null references public.datasets(id) on delete cascade,
  row_index      int  not null,
  input          jsonb not null,    -- {system, user} that was sent
  output         text,              -- the assistant response
  usage          jsonb,             -- per-row token + cost breakdown
  error          text,
  created_at     timestamptz not null default now(),
  unique (dataset_id, row_index)
);

create index dataset_rows_dataset_idx on public.dataset_rows (dataset_id, row_index);

alter table public.dataset_rows enable row level security;

create policy staff_read_dataset_rows on public.dataset_rows
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));
