-- Per-instance vLLM API key. Generated at rent time, passed to the
-- container as --api-key, and injected into proxy forwards so the vast
-- public IP can't be abused by port scanners.
alter table public.instances add column if not exists vllm_api_key text;
