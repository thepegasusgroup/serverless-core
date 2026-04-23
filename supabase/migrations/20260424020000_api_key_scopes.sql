-- Per-key scopes: which models + pipelines can this key call?
-- null = all (backwards compatible); empty array = none; otherwise whitelist of slugs.
alter table public.api_keys
  add column if not exists allowed_models text[],
  add column if not exists allowed_pipelines text[];

comment on column public.api_keys.allowed_models is
  'null = all models allowed; empty array = none; otherwise whitelist of slugs';
comment on column public.api_keys.allowed_pipelines is
  'null = all pipelines allowed; empty array = none; otherwise whitelist of slugs';
