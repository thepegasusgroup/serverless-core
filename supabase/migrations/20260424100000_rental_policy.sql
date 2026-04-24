-- Phase A: per-model rental policy.
-- Every new column defaults to today's behaviour so fresh installs are
-- unaffected. Fleet-level auto-replication / interruptible bidding / multi-GPU
-- are strictly opt-in per model via the Models UI.
--
-- Out-of-the-box: auto_replicate=false for every model — the `replicator`
-- background task is therefore a no-op. Operators rent manually via the
-- dashboard exactly like M3. Flip a model to auto_replicate=true and the
-- replicator starts maintaining its desired_replicas count.

alter table public.models
  add column if not exists desired_replicas int not null default 1
    check (desired_replicas >= 0 and desired_replicas <= 20),
  add column if not exists rental_mode text not null default 'on_demand'
    check (rental_mode in ('on_demand', 'interruptible')),
  add column if not exists max_bid_dph numeric(10,4),
  add column if not exists max_dph numeric(10,4),
  add column if not exists num_gpus int not null default 1
    check (num_gpus >= 1 and num_gpus <= 8),
  add column if not exists gpu_name text,
  add column if not exists offer_filters jsonb not null default '{}'::jsonb,
  add column if not exists auto_replicate boolean not null default false;

comment on column public.models.desired_replicas is
  'How many live instances the replicator keeps running for this model. Default 1.';
comment on column public.models.rental_mode is
  '"on_demand" (guaranteed, default) or "interruptible" (cheap, evictable).';
comment on column public.models.max_bid_dph is
  'Bid ceiling in $/hr for interruptible rentals. NULL = bid at market.';
comment on column public.models.max_dph is
  'Price ceiling in $/hr for any rental of this model. NULL = no ceiling.';
comment on column public.models.num_gpus is
  'GPUs per instance. Drives offer filter and vLLM --tensor-parallel-size.';
comment on column public.models.gpu_name is
  'Preferred GPU name (e.g., "RTX 5090"). NULL = any that meets min_vram_gb.';
comment on column public.models.offer_filters is
  'Per-model vast.ai query overrides: {min_cpu_cores, min_inet_down_mbps, datacenter_only, regions:["eu","us"], block_countries:[...], min_reliability, verified}.';
comment on column public.models.auto_replicate is
  'Master switch. When false (default), replicator skips this model.';

-- Trace which instances were auto-rented vs hand-rented in the dashboard.
alter table public.instances
  add column if not exists auto_replicated boolean not null default false;

comment on column public.instances.auto_replicated is
  'True if the replicator rented this instance; false if rented manually via dashboard.';
