-- Known-bad CPUs. Filtered out of offer search by offer_picker.py.
-- Works the same way as bad_machines: populated as we encounter hosts that
-- bottleneck Docker extraction / vLLM startup despite passing every other
-- filter. Matches offer.cpu_name via case-insensitive substring.
--
-- Examples of why a CPU ends up here:
--   Xeon Phi 7250     — 1.4 GHz, 2016 HPC chip, terrible single-thread perf
--   Xeon E5-2630 v4   — 2.2 GHz base Broadwell, weak for modern inference boot
--
-- Seed a few known-bad ones so fresh installs get sensible defaults.

create table public.bad_cpus (
  id         uuid primary key default gen_random_uuid(),
  cpu_name   text not null,       -- case-insensitive substring to match against offer.cpu_name
  reason     text,
  created_at timestamptz not null default now()
);

create unique index bad_cpus_cpu_name_lower_key on public.bad_cpus (lower(cpu_name));

alter table public.bad_cpus enable row level security;

create policy staff_read_bad_cpus on public.bad_cpus
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));

-- Seed with known offenders observed in practice.
insert into public.bad_cpus (cpu_name, reason) values
  ('Xeon Phi', '1.4 GHz HPC chip (2016 Knights Landing). Terrible single-thread perf; bottlenecks Docker extract + Python startup.'),
  ('E5-2630 v4', '2.2 GHz Broadwell (2016). Weak single-thread perf; observed 10+ min image pulls that should take 3-5 min.')
on conflict do nothing;
