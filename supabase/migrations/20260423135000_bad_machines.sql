-- Tracks vast.ai machines that repeatedly fail our rentals (broken Docker,
-- full disk, slow network, etc.). Offers on these machines are filtered out
-- server-side so we never rent them again.
create table public.bad_machines (
  machine_id bigint primary key,
  reason text not null,
  added_at timestamptz not null default now()
);

alter table public.bad_machines enable row level security;

create policy staff_read_bad_machines on public.bad_machines
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));
