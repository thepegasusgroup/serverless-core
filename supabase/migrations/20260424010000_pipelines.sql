-- Pipelines MVP: named presets that wrap a model + system prompt. Clients
-- POST to /v1/pipelines/<slug>/chat with just user messages; we inject the
-- system prompt and forward to the underlying model.
create table public.pipelines (
  id            uuid primary key default gen_random_uuid(),
  slug          text unique not null,
  label         text not null,
  model_slug    text not null references public.models(slug) on update cascade,
  system_prompt text,
  enabled       boolean not null default true,
  created_at    timestamptz not null default now()
);

alter table public.pipelines enable row level security;

create policy staff_read_pipelines on public.pipelines
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));
