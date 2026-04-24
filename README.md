# serverless-core

A private, OpenAI-compatible LLM API — rent vast.ai GPUs on demand, run vLLM
on them, and proxy requests through a single endpoint **you** control. Think
"RunPod Serverless on vast.ai", hosted by you.

> Send `POST /v1/chat/completions` to your own URL with your own API key →
> response streams back from a GPU that may have woken up (or been rented
> from scratch) just for you.

---

## What's shipped

### Core platform
- 🖥️  **Next.js 15 dashboard** — login, instances list, rent new, per-instance live logs + debug panel, API keys, request logs, model catalogue, in-app API tester (`/run`)
- 🔌 **FastAPI control plane** — OpenAI-compatible `/v1/chat/completions` + `/v1/completions` + `/v1/models`, SSE streaming passthrough
- 🔑 **API key auth** (`sc_live_...`) with per-key rate limiting + per-key model/pipeline scopes, **Supabase Auth** for staff
- 🤖 **Agent process** bundled with vLLM in a custom Docker image — registers with the control plane, heartbeats every 20 s
- 🔒 **vLLM locked** with a per-instance `--api-key` — only our proxy can talk to the rented box
- 📝 **Request logs** — every `/v1/*` call tracked (api_key, model, tokens, latency, status)
- 📡 **Realtime dashboard** — Supabase Realtime pushes status changes to open tabs

### Fleet management (Phase A)
- 🎛️  **Per-model rental policy** — GPU allowlist, region filter, CPU+bandwidth minimums, price ceiling, interruptible bidding, multi-GPU — all configurable per model row, all defaulted to sensible values
- 🔄 **Auto-replicator** — background task maintains `desired_replicas` per model; evictions auto-heal within 30 s
- 💰 **Scale-to-zero** — auto-pause idle instances (default 10 min), **wake-on-request** when a chat request arrives for a paused model
- 🧭 **Host quality filters** — `bad_machines` (bad physical hosts), `bad_cpus` (bad CPU models like Xeon Phi), country blocklist, `min_inet_down_mbps`, `min_cpu_ghz`, `min_cpu_cores`

### Pipelines (v3)
- 🧩 **Step-chain pipelines** — mix `model` steps and deterministic `transform` steps (trim, extract_json, regex_replace, strip_markdown_fences, etc.) with `{{input}} / {{prev}} / {{step_N}}` templating
- 🪝 **Webhook / JSON-only output modes** — fire-and-forget downstream workflows or return parsed JSON directly
- 🎮 **Playground** — test pipelines from `/run` with staff JWT (no API key needed)

### Baked-weights images (Phase B, optional)
- 🏗️  **Per-model Docker images** — GHA matrix workflow bakes HF weights into the image; cold boot drops from ~10 min → ~4 min on a 500 Mbps host
- 🔁 **Fallback** — default still uses the shared `sc-vllm-agent` image with runtime HF download; baking is opt-in per model

### Datasets (synthetic data generation)
- 📚 **`/datasets` page** — generate fine-tuning datasets via Claude Batch API (50% off standard pricing) with automatic prompt caching on shared system prompts
- 📦 **JSONL / CSV export** — OpenAI-chat-messages format ready for axolotl / LoRA / QLoRA training
- ⏱️  **Background poller** imports results as batches complete; Supabase Realtime pushes progress to the UI

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
       routing + proxy          rent/pause/destroy/dataset ops
              │                        │
       pick ready or wake       vast.ai REST API  ◄── replicator
              │                        │                  (auto-maintains
              │                        │                   desired_replicas)
              │                        ▼
              │               Anthropic Batches API  ◄── dataset poller
              │                   (for /datasets)
              ▼
       http+Bearer sc_inst_…
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

 background services (FastAPI lifespan):
   • status_poller  — vast.ai side drift → DB
   • idle_pauser    — auto-pause idle rentals
   • replicator     — maintain desired_replicas per model (auto_replicate=true)
   • dataset_poller — import completed Anthropic batches into dataset_rows

 state of the world: Supabase Postgres with RLS (instances / models /
   api_keys / request_logs / pipelines / datasets / dataset_rows /
   bad_machines / bad_cpus / staff_allowlist).
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for data model, request flows, and security notes.
See [`docs/RENTAL_POLICIES.md`](docs/RENTAL_POLICIES.md) for the per-model rental config reference.
See [`docs/BAKED_IMAGES.md`](docs/BAKED_IMAGES.md) for the Phase B baked-weights pipeline.
See [`docs/DATASETS.md`](docs/DATASETS.md) for the synthetic data generation workflow.

## Tech stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, httpx, pydantic v2, PyJWT (JWKS), Anthropic SDK |
| Web | Next.js 15, React 19, Tailwind 4, `@supabase/ssr`, sonner, lucide |
| CLI | Typer + Rich (`scx`) |
| Data / Auth | Supabase (Postgres + Auth + Realtime) |
| Hosting | Fly.io (API + web apps, `min_machines_running=1` so pollers stay alive) |
| GPU rentals | vast.ai |
| Agent image | `vllm/vllm-openai` + our agent, published to GHCR |
| Synthetic data | Anthropic Claude Batch API (50% off, prompt caching) |

## Repo layout

```
apps/api          FastAPI control plane — rent/pause/proxy/logs/datasets
  services/       background tasks: status_poller, idle_pauser, replicator,
                  dataset_poller, offer_picker, pipeline_exec, rental
apps/web          Next.js dashboard
apps/agent        On-instance agent (runs alongside vLLM on rented boxes)
cli               Typer CLI (`scx offers search`, `scx instance rent`, ...)
docker/vllm-agent Base + baked-weights Dockerfiles + entrypoint
.github/workflows
  build-agent.yml         → builds base sc-vllm-agent on push
  build-model-images.yml  → matrix-builds per-model baked images
supabase          Schema migrations (one per feature) + seed
docs              ARCHITECTURE, RENTAL_POLICIES, BAKED_IMAGES, DATASETS
dev-scripts       One-off helpers (e.g., get-jwt.py)
```

## Quick links (this deployment)

- Dashboard: https://sc-web-thepegasus.fly.dev
- API: https://sc-api-thepegasus.fly.dev
- Healthcheck: https://sc-api-thepegasus.fly.dev/healthz
- Repo: https://github.com/thepegasusgroup/serverless-core

## Running a fork

> Not yet one-command; plan a couple of hours for the first end-to-end bring-up.
> Most of that time is Supabase project setup + GHA secret plumbing.

Prereqs: Python 3.12, `uv`, Node 22 + `pnpm`, `flyctl`, a Supabase project, a vast.ai API key. Optional: Anthropic API key (for `/datasets`), HuggingFace token (for faster model downloads).

1. **Supabase** — create a project, apply the migrations:
   ```bash
   supabase link --project-ref <your-ref>
   supabase db push
   ```
   Add yourself to `staff_allowlist` (see `supabase/seed.sql`). Configure Auth (magic link) and set Site URL + redirect URLs to your planned web URL.

2. **Env vars** — copy `.env.example` → `.env` and fill:
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`
   - `VAST_API_KEY`
   - `AGENT_SHARED_SECRET` (any random 32+ char string)
   - `HF_TOKEN` (optional — 3-5× faster model downloads)
   - `ANTHROPIC_API_KEY` (optional — enables `/datasets`)

3. **Push your Docker image** — enable GitHub Actions in your fork. The `build-agent.yml` workflow builds and pushes to `ghcr.io/<your-org>/sc-vllm-agent:sha-<commit>`. Update the `docker_image` column of the `models` table (or use the dashboard's Models page once the API is deployed).

4. **Deploy the API** to Fly (from repo root):
   ```bash
   fly apps create <your-api-name>
   fly secrets set ... --app <your-api-name>   # all the SUPABASE_/VAST_/HF_/ANTHROPIC_ vars
   fly deploy
   ```

5. **Deploy the dashboard** (from `apps/web/`):
   ```bash
   # edit fly.toml to rename the app and patch build.args for your Supabase
   fly apps create <your-web-name>
   fly deploy
   ```

6. **Log in** at your web URL → `/models` → add a model (start with `Qwen/Qwen2.5-7B-Instruct`) → `/keys` → create an API key → test via `/run`.

7. **(Optional) bake weights** — if you run one model often, add it to `.github/workflows/build-model-images.yml` matrix and point `models.docker_image` at the baked tag. See [`docs/BAKED_IMAGES.md`](docs/BAKED_IMAGES.md).

8. **(Optional) generate fine-tune data** — add `ANTHROPIC_API_KEY` as a Fly secret and use `/datasets` to kick off Claude Batch jobs. See [`docs/DATASETS.md`](docs/DATASETS.md).

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
uv run scx offers search --gpu RTX_3090 --region eu --datacenter-only
uv run scx instance rent <offer_id> --model qwen2.5-7b-instruct
uv run scx instance pause <uuid>
uv run scx key create production-backend
```

## Typical cost profile (for reference)

For a single model (e.g., Qwen 2.5 7B) with 2 interruptible replicas on a mix of RTX 3090/4090 hosts via our per-model rental policy, 3 hr/day active:

- **~$40-65/month** per model (interruptible median $0.11-0.20/hr × 6 active-hour-equivalents/day × 2 replicas, plus ~$0.03/hr paused-disk cost)
- Add ~$25-35 one-off to generate a 500-row fine-tuning dataset via Claude Batch

## Roadmap

Done:
- [x] M1 scaffold + offer search + Supabase schema
- [x] M2 rent / register / heartbeat
- [x] M2.5 OpenAI-compatible streaming proxy
- [x] M3 scale-to-zero (auto-pause + wake-on-request)
- [x] M4 dashboard (instances, rent, keys, logs, run, models, pipelines, datasets)
- [x] M5 API keys with scopes + rate limiting
- [x] Pipelines v3 (step chains with model + transform steps)
- [x] Per-model rental policy + auto-replicator (Phase A)
- [x] Baked-weights Docker images (Phase B)
- [x] `bad_machines` + `bad_cpus` host quality filters
- [x] Request logs + realtime dashboard status
- [x] Datasets — Claude Batch API synthetic data generation

In progress / next:
- [ ] **Stuck-instance reaper** — auto-destroy instances stuck in waking/provisioning for too long
- [ ] **Smarter routing** — race-wake multiple paused instances, fail over on stuck wake
- [ ] **Store `dph` + `gpu_name` at rent time** so the Instances page can show real costs without a live vast API call
- [ ] **Host-id blocklist** — block all machines on a known-broken host (currently blocks per-machine)
- [ ] **Slim custom Docker image** — avoid the 8GB `vllm/vllm-openai` base where possible
- [ ] **Docker-compose quickstart** for one-command self-hosting
- [ ] **Parametrize fork identifiers** (app names, GHCR path) for easy OSS use
- [ ] **Multi-provider support** — optional fallback to clore.ai, RunPod, or self-hosted hardware
- [ ] **Embedding / reranker endpoints** — extend the OpenAI-compat proxy beyond chat
- [ ] **LoRA adapter serving** — one base model with swappable adapters for coder+fixer+... variants

## License

Internal / unpublished. To be decided before open-sourcing.
