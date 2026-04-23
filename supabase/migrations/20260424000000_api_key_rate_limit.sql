-- Per-key rate limit (requests per minute). NULL = unlimited.
alter table public.api_keys add column if not exists requests_per_minute int;
