-- Seed data. Run once against your Supabase project after `supabase db push`.
-- Safe to re-run: uses ON CONFLICT DO NOTHING.
--
-- IMPORTANT: replace YOUR_EMAIL below with the Supabase auth user's email
-- that should have staff access, then paste this file into the Supabase
-- dashboard's SQL Editor and run.

-- 1. Grant yourself staff access.
insert into public.staff_allowlist (email, role)
values ('YOUR_EMAIL@example.com', 'admin')
on conflict (email) do nothing;

-- 2. Seed the first model. Docker image is a placeholder for M1 — we'll
--    replace it with the real GHCR tag in M2 once the image is built.
insert into public.models (slug, hf_repo, vllm_args, min_vram_gb, docker_image)
values (
  'qwen2.5-7b-instruct',
  'Qwen/Qwen2.5-7B-Instruct',
  '{"max_model_len": 8192, "gpu_memory_utilization": 0.9, "dtype": "bfloat16"}'::jsonb,
  16,
  'ghcr.io/REPLACE_ME/sc-vllm-agent:latest'
)
on conflict (slug) do nothing;

-- 3. Coder v2 — Qwen3-32B base + QLoRA adapter (trained on 1,318 plugin rows).
--    LoRA adapter is baked into the Docker image; the entrypoint reads
--    /opt/sc-lora-path and injects --enable-lora + --lora-modules automatically.
--    Needs ≥48GB VRAM (80GB recommended for 32K context).
insert into public.models (slug, hf_repo, vllm_args, min_vram_gb, docker_image)
values (
  'qwen3-32b-coder-v2',
  'Qwen/Qwen3-32B',
  '{
    "max_model_len": 32768,
    "gpu_memory_utilization": 0.92,
    "dtype": "bfloat16",
    "max_lora_rank": 64
  }'::jsonb,
  48,
  'ghcr.io/REPLACE_ME/sc-vllm-agent-qwen3-32b-coder-v2:latest'
)
on conflict (slug) do nothing;
