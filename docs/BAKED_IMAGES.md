# Baked-weights model images (Phase B)

Default behaviour: when a vast host is rented, it pulls the shared
`sc-vllm-agent:<sha>` image from GHCR, then downloads the model's weights
from HuggingFace at runtime. Weights are 15 GB (7B) to 140 GB (70B), and
HF's CDN commonly throttles to 100–300 Mbps per host — so the cold-boot
path is **~6–10 min**, often the slowest thing in the whole fleet.

**Baked images move the weights into the Docker image.** One transfer from
GHCR, no runtime HF call. Typical cold boot drops to **~2–3 min** on a
500 Mbps host, ~90 s on a 1 Gbps datacenter host.

## When to use

- You run the same model often enough that repeated HF downloads waste
  money (every replicator re-rent after an eviction pays the full cost
  again).
- You care about faster recovery after interruptible evictions.
- You want to decouple from HF's rate limits and occasional 503s.

Fresh installs / dev forks: **you don't need this.** The default
download-at-boot image works identically; Phase B is strictly an opt-in
optimisation.

## How it works

1. `.github/workflows/build-model-images.yml` has a matrix of `{slug, hf_repo, hf_revision}`.
2. On push (or manual `workflow_dispatch`), each matrix entry builds a
   Docker image: base `sc-vllm-agent` + `snapshot_download(hf_repo)` into
   the vLLM cache path.
3. Image is published to `ghcr.io/<owner>/sc-vllm-agent-<slug>:sha-<git-sha>`.
4. Operator updates `public.models.docker_image` to the new tag.
5. Next time the replicator rents an instance for that model, the rented
   host pulls the baked image from GHCR. vLLM finds the weights already
   cached, skips the HF call entirely.

## Adding a new model to the matrix

Edit the `model` strategy block in
`.github/workflows/build-model-images.yml`:

```yaml
matrix:
  model:
    - slug: qwen25-7b
      hf_repo: Qwen/Qwen2.5-7B-Instruct
      hf_revision: main
    # Your addition:
    - slug: llama-3.1-8b
      hf_repo: meta-llama/Llama-3.1-8B-Instruct
      hf_revision: main
```

Commit and push to `main`. The workflow runs automatically on changes to
the Dockerfile.baked or this workflow file; for adds without a change to
those paths, kick it off manually from the **Actions** tab in GitHub.

## Build times & sizes

- **7B model**: ~20–30 min build, ~23 GB image.
- **13B model**: ~30–45 min build, ~35 GB image.
- **70B model**: 1–2 hours build, ~140 GB image — probably too big for GHA
  free runners (14 GB disk). Options:
  1. Use larger runners (`runs-on: ubuntu-latest-16-cores` if on a paid
     plan) or self-hosted runners.
  2. Use quantized weights (AWQ/GPTQ ~40 GB for 70B).
  3. Fall back to HF download at runtime for that one model.

## Gated repos (Llama, Gemma, Mistral v0.3+)

1. Create a HuggingFace access token with read permission for the gated repo.
2. Add it as a repo secret named `HF_TOKEN` in the GitHub repo settings
   (Settings → Secrets and variables → Actions → New repository secret).
3. The workflow passes it to the build via `--secret`. Ungated repos
   ignore it; gated repos use it.

## Updating the model row

After the workflow finishes, grab the image tag from the run summary
("Baked image pushed") and update the `models` row:

```sql
update public.models
set docker_image =
  'ghcr.io/thepegasusgroup/sc-vllm-agent-qwen25-7b:sha-<paste-git-sha>'
where slug = 'qwen2.5-7b-instruct';
```

(Or via the Models page UI — edit the model, change the Docker image
field.) Existing running instances keep the old image; **new rentals**
pick up the new one.

## Why SHA tags, not `:latest`

Vast hosts cache Docker pulls locally. A host that's already served your
`:latest` image for a different tenant won't re-pull it when you rent
them again — even if you've pushed a new version. SHA-tagged images are
immutable, so a rebuild produces a new tag, and every rental pulls fresh.

## Cost impact on GHCR

- Public packages: unlimited storage + egress. No extra cost.
- Private packages: 500 MB storage free, then $0.50/GB storage + $0.50/GB
  egress. At ~23 GB per model × 5 models = 115 GB storage ≈ $57/mo
  storage + egress scales with rental frequency. **Recommendation: keep
  packages public.**

## Verifying a baked image works

After the workflow finishes and you've updated the model row, destroy any
existing instances of that model. The replicator will re-rent using the
new image. Watch `/instances/<id>`:

- Stage messages should show "Pulling image" and NOT "Downloading from HF".
- `last_heartbeat_at` should appear within ~2–3 min of the rental
  (vs. ~8–10 min with the download-at-boot image).
- Debug panel will show `SC_BAKED_HF_REPO` env var if the baked image is
  really in use.

## Rolling back

Flip `models.docker_image` back to the old shared base image
(`ghcr.io/<owner>/sc-vllm-agent:sha-<base-sha>`). New rentals use the
base again; everything falls back to HF download at boot. No schema
changes, no code changes.
