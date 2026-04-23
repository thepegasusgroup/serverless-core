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
