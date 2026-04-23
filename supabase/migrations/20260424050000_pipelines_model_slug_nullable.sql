-- model_slug was required in v1 (single-model presets). As of v3 the step
-- chain is the source of truth, so the legacy column becomes optional.
alter table public.pipelines alter column model_slug drop not null;
