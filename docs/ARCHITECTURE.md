# Architecture

## Components

| Component | Lives on | Language | Role |
|---|---|---|---|
| **FastAPI control plane** | Fly.io (`sc-api-thepegasus.fly.dev`) | Python 3.12 | Auth, rent/pause/destroy orchestration, OpenAI-compat reverse proxy, background status poller + idle auto-pauser |
| **Next.js dashboard** | Fly.io (`sc-web-thepegasus.fly.dev`) | TypeScript | Staff-only UI |
| **Supabase** | Supabase Cloud | Postgres | Durable state + Auth + Realtime |
| **Agent** | Inside each rented vast.ai container | Python 3.12 | Registers the box, sends heartbeats |
| **vLLM** | Inside each rented vast.ai container | CUDA + PyTorch | OpenAI-compatible inference server |
| **CLI (`scx`)** | Developer laptops | Python (Typer) | Shell access to the same FastAPI endpoints |

## Data model (Supabase, `public` schema)

```
staff_allowlist(email, role, created_at)
    └─ read gate for every other table via is_staff() SECURITY DEFINER fn

models(id, slug, hf_repo, vllm_args jsonb, min_vram_gb,
       docker_image, enabled, auto_pause_minutes, created_at)

instances(id uuid, vast_contract_id bigint, model_id → models,
          status in (provisioning, booting, ready, unhealthy,
                     paused, waking, destroyed),
          ip inet, port int, gpu_name, dph numeric,
          vllm_api_key text,     -- per-instance Bearer token
          stage_msg text,        -- latest vast status_msg
          vast_actual_status text,
          last_request_at, last_heartbeat_at, registered_at,
          paused_at, destroyed_at, created_at,
          rent_args jsonb)

api_keys(id, key_hash text, prefix, label, created_by → auth.users,
         created_at, last_used_at, revoked_at)
    └─ key_hash = sha256(plaintext); plaintext shown once at creation

request_logs(id, api_key_id → api_keys, instance_id → instances,
             model_slug, path, streaming bool, status_code, latency_ms,
             prompt_tokens, completion_tokens, total_tokens, error, created_at)

bad_machines(machine_id bigint pk, reason, added_at)
    └─ vast.ai machines known to fail our rents
```

All tables have **RLS enabled**. SELECT policies check `is_staff(auth.jwt()
->> 'email')`. All writes go through the FastAPI service role key, which
bypasses RLS.

## Request flow — `/v1/chat/completions`

1. **Client** POSTs to `https://sc-api-thepegasus.fly.dev/v1/chat/completions`
   with `Authorization: Bearer sc_live_...`.
2. **`require_api_key` dependency** sha256-hashes the token, looks it up in
   `api_keys`, checks not revoked, updates `last_used_at`. → `api_key_id`.
3. **Routing** — `services/routing.pick_instance(model, sb)`:
   - Resolves `model` (slug or HF repo) to a `models` row.
   - Finds one `instances` row with `status='ready'` and fresh
     `last_heartbeat_at` (< 90s). None? → try `find_dormant_instance`.
4. **Wake-on-request** (if only a `paused`/`waking` instance exists):
   - Call `vast.resume_instance(contract_id)`, flip DB status to `waking`.
   - Poll the DB every 2s (up to 4 min) until agent re-registers → `ready`.
5. **Forward** — httpx POSTs to the vast instance's public `ip:port`, adding
   `Authorization: Bearer sc_inst_...` (stored in `instances.vllm_api_key`).
   Without this header the vast-side vLLM returns 401 — so no one who stumbles
   on the vast IP can abuse the GPU.
6. **Response**:
   - Non-streaming: proxy reads JSON, logs token counts, returns it.
   - Streaming: proxy `StreamingResponse`s the upstream SSE bytes through
     unchanged (`aiter_raw`, `text/event-stream`, `X-Accel-Buffering: no`).
7. **`last_request_at`** is stamped on the instance so the idle auto-pauser
   knows the box is in use.
8. **`request_logs`** gets one row (status, latency, token counts).

## Instance lifecycle

```
       ┌─────────┐          rent
       │  none   │ ──────────────────────► provisioning
       └─────────┘                              │
                                                │ agent boots + registers
                                                ▼
                                             ready ◄──────┐
                                          │     ▲         │
                      (last_request_at)   │     │         │
                      idle > 10 min       │     │ agent   │ wake-on-request
                                          │     │ heartbeat
                                          ▼     │         │
                                         paused ┘         │
                                          │               │
                                          └─── resume ────► waking ──► ready
     destroy (anywhere)                  ──►  destroyed
```

## Background services

- **`status_poller`** (every 15s) — pulls vast.ai state for every
  non-terminal instance; updates `stage_msg` and `vast_actual_status` so the
  dashboard shows live progress. Also marks instances `destroyed` if vast
  returns 404 or terminal `actual_status`.
- **`idle_pauser`** (every 60s) — finds `ready` instances idle longer than
  `models.auto_pause_minutes`; calls `vast.pause_instance` and flips status
  to `paused`. Per-model configurable; `null` disables.

Both run as `asyncio.create_task(...)` from FastAPI's lifespan.

## Security layers

| Surface | Gate |
|---|---|
| Dashboard pages | Next.js middleware checks Supabase session; `staff_allowlist` is the truth |
| `/admin/*` | Supabase JWT decoded via JWKS (ES256/RS256) + email in `staff_allowlist` |
| `/internal/*` (agent → API) | `X-Agent-Secret` header, compared against `AGENT_SHARED_SECRET` |
| `/v1/*` | `sc_live_...` API key → sha256 → `api_keys` row |
| vast.ai rented box | per-instance `sc_inst_...` passed as `--api-key` to vLLM; only our proxy knows it |
| Supabase DB | RLS with `is_staff()` SECURITY DEFINER helper |
| Host selection | Blocks `CN/RU/BY/IR/KP/SY` by default + `bad_machines` table for individually broken hosts |

## Known edge cases

- **Fly proxy `Host` header** — `NextResponse.redirect` from `/auth/callback`
  uses `x-forwarded-host` (else you hit `https://0.0.0.0:3000`).
- **Supabase inet column** — empty string IPs rejected; register endpoint
  coerces empty → null, falls back to `vast.show_instance.public_ipaddr`.
- **Vast log URL** — S3 blob returns 403 before the file is uploaded; we
  retry with backoff (`vast.get_instance_logs`).
- **Windows `sc` collision** — CLI entry point is `scx` because `sc.exe` is
  a Windows Service Control built-in.
- **JWT algorithm** — Supabase's newer projects use ES256 via JWKS. Our
  `deps.py` tries JWKS first, falls back to HS256 with the legacy
  `SUPABASE_JWT_SECRET`.
- **vLLM image size** — `vllm/vllm-openai:latest` is ~8GB compressed, ~35GB
  extracted; future "slim image" work will cut this.

## Environment variables

```
# API (sc-api-thepegasus)
SUPABASE_URL                  # https://<ref>.supabase.co
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_JWT_SECRET           # or use JWKS for ES256 (automatic)
VAST_API_KEY
AGENT_SHARED_SECRET           # any long random string
HF_TOKEN                      # optional; 3-5x faster HF downloads
PUBLIC_API_URL                # https://sc-api-thepegasus.fly.dev
CORS_ALLOWED_ORIGINS          # comma-separated

# Web (sc-web-thepegasus) — all baked in at build time
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_ANON_KEY
NEXT_PUBLIC_API_URL
```

## CI

`.github/workflows/build-agent.yml` builds and pushes
`ghcr.io/<owner>/sc-vllm-agent:sha-<sha>` on push to `main`.
