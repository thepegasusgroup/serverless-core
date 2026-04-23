-- One row per /v1/* call. Populated by the proxy after each request.
create table public.request_logs (
  id                 uuid primary key default gen_random_uuid(),
  api_key_id         uuid references public.api_keys(id) on delete set null,
  instance_id        uuid references public.instances(id) on delete set null,
  model_slug         text,
  path               text,
  streaming          boolean default false,
  status_code        int,
  latency_ms         int,
  prompt_tokens      int,
  completion_tokens  int,
  total_tokens       int,
  error              text,
  created_at         timestamptz not null default now()
);

create index request_logs_created_idx on public.request_logs (created_at desc);
create index request_logs_api_key_idx on public.request_logs (api_key_id, created_at desc);

alter table public.request_logs enable row level security;

create policy staff_read_request_logs on public.request_logs
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));

alter publication supabase_realtime add table public.request_logs;
