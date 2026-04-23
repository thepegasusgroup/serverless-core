# serverless-core

A private, OpenAI-compatible LLM API — rent vast.ai GPUs on demand, run vLLM
on them, and proxy requests through a single endpoint **you** control. Think
"RunPod Serverless on vast.ai", hosted by you.

> Send `POST /v1/chat/completions` to your own URL with your own API key →
> response streams back from a GPU that may have woken up just for you.

---

## What's shipped

- 🖥️  **Next.js dashboard** — login, instances list, rent new, live pipeline log, API keys, request logs, model catalogue, in-app API tester (`/run`)
- 🔌 **FastAPI control plane** — OpenAI-compatible `/v1/chat/completions` + `/v1/completions` + `/v1/models`, SSE streaming passthrough
- 🔑 **API key auth** (`sc_live_...`) for external clients, **Supabase Auth** for staff
- 🤖 **Agent process** bundled with vLLM in a custom Docker image — registers with the control plane, heartbeats every 20s
- 💰 **Scale-to-zero** — auto-pause idle instances (default 10 min), **wake-on-request** when a chat request arrives for a paused model
- 🔒 **vLLM locked** with a per-instance `--api-key` — only our proxy can talk to the rented box
- 📝 **Request logs** — every `/v1/*` call tracked (api_key, model, tokens, latency, status)
- 🧭 **Country + datacenter + CPU + bandwidth filters** on offer search, plus a `bad_machines` blocklist for hosts that misbehave
- 📡 **Realtime dashboard** — Supabase Realtime pushes status changes to open tabs

## Architecture at a glance

```
 staff browser ──► sc-web-thepegasus.fly.dev (Next.js)
                            │
                            │ Supabase JWT
                            ▼
 clients ──► sc-api-thepegasus.fly.dev (FastAPI)
                            │
   ┌────────────────────────┼────────────────────────┐
   │ /v1/*   sc_live_ key   │ /admin/*    staff JWT  │
   └──────────┬─────────────┴──────────┬─────────────┘
              │                        │
              ▼                        ▼
       routing + proxy          rent/pause/destroy
              │                        │
     look up `ready` instance      vast.ai REST API
              │
              ▼   (http, with per-instance Bearer sc_inst_…)
       ┌─────────────────────┐
       │ vast.ai rented GPU  │
       │  ┌───────────────┐  │
       │  │ vLLM :8000    │  │ ◄── per-instance sc_inst_ key
       │  │ (--api-key ..)│  │     enforces no direct access
       │  └───────────────┘  │
       │  ┌───────────────┐  │
       │  │ sc-agent      │──┼──► POST /internal/instances/register
       │  │ heartbeat 20s │  │     + /heartbeat
       │  └───────────────┘  │
       └─────────────────────┘

 state of the world: Supabase Postgres (instances / models / api_keys /
   request_logs / staff_allowlist / bad_machines) with Row-Level Security.
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for data model, request
flows, and security notes.

## Tech stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, httpx, pydantic v2, PyJWT (JWKS) |
| Web | Next.js 15, React 19, Tailwind 4, `@supabase/ssr`, sonner, lucide |
| CLI | Typer + Rich (`scx`) |
| Data / Auth | Supabase (Postgres + Auth + Realtime) |
| Hosting | Fly.io (api + web apps) |
| GPU rentals | vast.ai |
| Agent image | `vllm/vllm-openai` + our Python agent, built via GHCR |

## Repo layout

```
apps/api          FastAPI control plane (rent/pause/proxy/logs)
apps/web          Next.js dashboard
apps/agent        On-instance agent (runs alongside vLLM)
cli               Typer CLI (`scx offers search`, `scx instance rent`, ...)
docker/vllm-agent Dockerfile + entrypoint for the rented box
supabase          Schema migrations + seed
dev-scripts       One-off helpers (e.g., get-jwt.py)
```

## Quick links (this deployment)

- Dashboard: https://sc-web-thepegasus.fly.dev
- API: https://sc-api-thepegasus.fly.dev
- Healthcheck: https://sc-api-thepegasus.fly.dev/healthz
- Repo: https://github.com/thepegasusgroup/serverless-core

## Running a fork

> Not yet one-command; see "Open-source roadmap" below. Plan a couple of
> hours for the first end-to-end bring-up.

Prereqs: Python 3.12, `uv`, Node 22 + `pnpm`, `flyctl`, a Supabase project,
a vast.ai API key.

1. **Supabase** — create a project, apply the migrations:
   ```bash
   supabase link --project-ref <your-ref>
   supabase db push
   ```
   Then insert yourself into `staff_allowlist` (see `supabase/seed.sql`).

2. **Env vars** — copy `.env.example` → `.env` and fill:
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`
   - `VAST_API_KEY`
   - `AGENT_SHARED_SECRET` (any random 32+ char string)
   - `HF_TOKEN` (optional — 3-5× faster model downloads)

3. **Push your Docker image** — enable GitHub Actions in your fork. The
   `build-agent.yml` workflow builds and pushes to
   `ghcr.io/<your-org>/sc-vllm-agent:sha-<commit>`. Update the `docker_image`
   column of the `models` table (or use the dashboard's Models page once the
   API is deployed).

4. **Deploy the API** to Fly (from repo root):
   ```bash
   fly apps create <your-api-name>
   fly secrets set ... --app <your-api-name>   # all the SUPABASE_/VAST_/HF_ vars
   fly deploy
   ```

5. **Deploy the dashboard** (from `apps/web/`):
   ```bash
   # edit fly.toml to rename the app and patch build.args for your Supabase
   fly apps create <your-web-name>
   fly deploy
   ```

6. **Supabase URL config** — in the Supabase dashboard, set Site URL and
   allowed redirect URLs to your deployed web URL.

7. **Log in** at your web URL → create an API key → test via `/run`.

## Development

```bash
# Python / CLI / API
uv sync
uv run --package serverless-core-api uvicorn serverless_core_api.main:app --reload

# Dashboard
cd apps/web
pnpm install
pnpm dev   # → http://localhost:3000
```

`scx` CLI:
```bash
uv run scx login
uv run scx offers search --gpu RTX_5090 --region eu --datacenter-only
uv run scx instance rent <offer_id> --model qwen2.5-7b-instruct
uv run scx instance pause <uuid>
uv run scx key create production-backend
```

## Roadmap

Done:
- [x] M1 repo scaffold + offer search
- [x] M2 rent / register / heartbeat
- [x] M2.5 OpenAI-compatible streaming proxy
- [x] M3 scale-to-zero (manual + auto-pause + wake-on-request)
- [x] M4 dashboard (instances, rent, keys, logs, run, models)
- [x] M5 API keys

In progress / next:
- [ ] Slim custom Docker image (avoid 8GB `vllm/vllm-openai` base)
- [ ] Pipelines (chain models under a single client URL)
- [ ] Rate limiting per API key
- [ ] Docker-compose quickstart for one-command self-hosting
- [ ] Parametrize fork identifiers (app names, GHCR path) for easy OSS use

## License

Internal / unpublished. To be decided before open-sourcing.
