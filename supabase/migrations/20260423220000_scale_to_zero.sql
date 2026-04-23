-- M3: scale-to-zero groundwork.
-- New statuses: paused (stopped on vast, disk kept), waking (vast start issued, vLLM not up yet).
-- last_request_at drives idle detection for auto-pause.
-- models.auto_pause_minutes = null means "never auto-pause".

alter table public.instances drop constraint instances_status_check;
alter table public.instances add constraint instances_status_check
  check (status in ('provisioning','booting','ready','unhealthy','paused','waking','destroyed'));

alter table public.instances add column if not exists last_request_at timestamptz;
alter table public.instances add column if not exists paused_at timestamptz;

alter table public.models add column if not exists auto_pause_minutes int default 10;
