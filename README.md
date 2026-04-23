# serverless-core

Private "RunPod-on-vast.ai" — a control plane that rents vast.ai GPU instances,
runs vLLM on them, and proxies OpenAI-compatible inference requests through a
single API we control. Internal use only.

See `C:\Users\admin\.claude\plans\right-time-for-us-refactored-lagoon.md` for
the full plan and milestone breakdown.

## Status

**Current milestone: M1** — repo scaffold, Supabase schema, offer search CLI.
No instances are rented yet; that starts at M2.

## Layout

```
apps/api       FastAPI control plane
apps/web       Next.js dashboard (M4)
apps/agent     On-instance registration/heartbeat process (M2)
cli            Typer CLI — `sc`
docker         Custom vLLM+agent image (M2)
supabase       Schema migrations + seed
```

## Setup (M1)

Prereqs: Python 3.12, [uv](https://docs.astral.sh/uv/), the
[Supabase CLI](https://supabase.com/docs/guides/cli), a Supabase Cloud project,
and a vast.ai API key.

```bash
# 1. Env vars
cp .env.example .env
# Fill in SUPABASE_*, VAST_API_KEY, generate AGENT_SHARED_SECRET.

# 2. Supabase schema
supabase link --project-ref <your-project-ref>
supabase db push
# Edit supabase/seed.sql to use your email, then:
# In Supabase dashboard > SQL Editor, paste seed.sql and run.

# 3. Python deps
uv sync

# 4. Run the API
uv run --package serverless-core-api uvicorn serverless_core_api.main:app --reload
# http://localhost:8000/healthz should return {"ok": true, "vast_ok": true}

# 5. CLI
# In another terminal, once you have a Supabase JWT for your user:
uv run sc login
uv run sc offers search --gpu RTX_4090 --max-dph 0.5
```

### Getting a Supabase JWT (temporary M1 hack)

Until the dashboard exists (M4), grab a JWT manually:

1. Supabase dashboard → Authentication → Users → add a user with your email.
2. Use the built-in "magic link" or set a password for yourself.
3. Easiest path: run this snippet locally against your Supabase project to
   get a fresh JWT:

```python
from supabase import create_client
sb = create_client("<SUPABASE_URL>", "<SUPABASE_ANON_KEY>")
res = sb.auth.sign_in_with_password({"email": "you@example.com", "password": "..."})
print(res.session.access_token)
```

Paste the token into `uv run sc login` when prompted.

## M1 verification checklist

Run from the repo root in Git Bash.

### 1. Supabase schema is live

```bash
supabase link --project-ref <ref>
supabase db push
```

Then, in the **Supabase dashboard → SQL Editor**, paste `supabase/seed.sql`
(after editing `YOUR_EMAIL@example.com` to your real email) and run it.

Confirm in Table Editor:
- `staff_allowlist` has one row with your email
- `models` has one row with slug `qwen2.5-7b-instruct`
- `instances` exists and is empty
- Each table shows "RLS enabled"

### 2. FastAPI boots and sees vast.ai

```bash
uv sync
uv run --package serverless-core-api uvicorn serverless_core_api.main:app --reload
```

```bash
curl http://localhost:8000/healthz
# → {"ok":true,"vast_ok":true}
```

If `vast_ok` is `false`, your `VAST_API_KEY` is wrong — double-check
[cloud.vast.ai/account/](https://cloud.vast.ai/account/).

### 3. Auth works

```bash
# No token → 401
curl -i http://localhost:8000/admin/offers

# With a valid staff JWT → 200 and a JSON list
curl -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/admin/offers?gpu=RTX_4090&max_dph=0.5"

# With a JWT whose email is NOT in staff_allowlist → 403
```

### 4. CLI works end-to-end

```bash
uv run sc login           # paste the JWT
uv run sc whoami          # shows API URL + "JWT: set"
uv run sc offers search --gpu RTX_4090 --max-dph 0.5
uv run sc offers search --gpu RTX_3060 --max-dph 0.15 --json
```

A successful run prints a Rich table with real vast.ai offers sorted by price.

## Known pitfalls

- **Supabase JWT algorithm**: this project assumes HS256 (the classic
  `JWT_SECRET` flow). If your Supabase project uses the newer asymmetric
  key rotation (RS256/ES256), check **Project Settings → API → JWT Keys**
  — either fetch `SUPABASE_JWT_SECRET` from "Legacy JWT Secret", or we
  need to switch `jwt.decode` to use the JWKS endpoint. Flag this when
  you hit it and we'll update `deps.py`.
- **`SUPABASE_JWT_SECRET` ≠ anon key ≠ service role key** — three distinct
  values, all from Project Settings → API.
- **Env not loaded**: pydantic-settings reads `.env` from CWD. Always run
  uvicorn from the repo root (not from `apps/api/`).
- **Windows path quirks**: CLI config lands at
  `C:\Users\<you>\.config\serverless-core\config.toml` — `Path.home()`
  handles this correctly; `chmod 0600` is a best-effort no-op on Windows.

