-- serverless-core M1 schema
-- Tables: staff_allowlist, models, instances
-- All tables have RLS enabled. Reads are gated to emails in staff_allowlist.
-- All writes go through the FastAPI control plane using the service_role key
-- (which bypasses RLS), so no INSERT/UPDATE/DELETE policies are defined here.

-- ---------------------------------------------------------------------------
-- staff_allowlist: single source of truth for "who can read data"
-- ---------------------------------------------------------------------------
create table public.staff_allowlist (
  email      text primary key,
  role       text not null default 'admin',
  created_at timestamptz not null default now()
);

alter table public.staff_allowlist enable row level security;

-- SECURITY DEFINER helper so RLS policies on other tables can consult the
-- allowlist without the querying user needing direct SELECT on it.
create or replace function public.is_staff(user_email text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (select 1 from public.staff_allowlist where email = user_email);
$$;

revoke all on function public.is_staff(text) from public;
grant execute on function public.is_staff(text) to authenticated, anon;

create policy staff_read_allowlist on public.staff_allowlist
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));

-- ---------------------------------------------------------------------------
-- models: catalogue of LLMs we know how to serve
-- ---------------------------------------------------------------------------
create table public.models (
  id            uuid primary key default gen_random_uuid(),
  slug          text unique not null,
  hf_repo       text not null,
  vllm_args     jsonb not null default '{}'::jsonb,
  min_vram_gb   int  not null,
  docker_image  text not null,
  enabled       boolean not null default true,
  created_at    timestamptz not null default now()
);

alter table public.models enable row level security;

create policy staff_read_models on public.models
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));

-- ---------------------------------------------------------------------------
-- instances: rented vast.ai contracts and their lifecycle state
-- ---------------------------------------------------------------------------
create table public.instances (
  id                 uuid primary key default gen_random_uuid(),
  vast_contract_id   bigint unique,
  model_id           uuid not null references public.models(id),
  status             text not null default 'provisioning'
    check (status in ('provisioning','booting','ready','unhealthy','destroyed')),
  ip                 inet,
  port               int,
  gpu_name           text,
  dph                numeric(10,4),
  last_heartbeat_at  timestamptz,
  registered_at      timestamptz,
  created_at         timestamptz not null default now(),
  destroyed_at       timestamptz,
  rent_args          jsonb
);

create index instances_status_model_idx on public.instances (status, model_id);
create index instances_heartbeat_idx    on public.instances (last_heartbeat_at);

alter table public.instances enable row level security;

create policy staff_read_instances on public.instances
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));
