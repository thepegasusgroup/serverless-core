-- Add disk_gb (override per model) and lora_name (LoRA adapter alias for vLLM)
-- to the models table. Supports larger base models (32B+) and baked LoRA adapters.

alter table public.models
  add column if not exists disk_gb int,
  add column if not exists lora_name text;

comment on column public.models.disk_gb is
  'Override disk allocation (GB) for vast.ai rental. NULL = use default (80GB).';
comment on column public.models.lora_name is
  'LoRA adapter name exposed to vLLM --lora-modules. NULL = use model slug.';
