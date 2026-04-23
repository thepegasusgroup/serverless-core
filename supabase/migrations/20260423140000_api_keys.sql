-- API keys for clients calling /v1/chat/completions etc.
-- We store only SHA-256 hashes; the plaintext is shown once at creation.
create table public.api_keys (
  id           uuid primary key default gen_random_uuid(),
  key_hash     text unique not null,
  prefix       text not null,
  label        text not null,
  created_by   uuid references auth.users(id),
  created_at   timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at   timestamptz
);

create index api_keys_active_idx on public.api_keys (key_hash) where revoked_at is null;

alter table public.api_keys enable row level security;

create policy staff_read_api_keys on public.api_keys
  for select to authenticated
  using (public.is_staff((auth.jwt() ->> 'email')));
