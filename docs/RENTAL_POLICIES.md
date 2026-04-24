# Rental policies

Each model row in `public.models` carries a **rental policy** — a small set
of optional fields that control what vast.ai offers are rented for it and how
the fleet is managed.

Every field has a default that preserves the single-instance, manual-rental
behaviour of M3. You only need to touch these if you want auto-replication,
interruptible bidding, or multi-GPU.

## The fields

| Field | Default | Purpose |
|---|---|---|
| `desired_replicas` | `1` | How many live instances the **replicator** keeps running for this model. Live = `provisioning/booting/ready/paused/waking`. |
| `rental_mode` | `'on_demand'` | `'on_demand'` (guaranteed) or `'interruptible'` (bid, evictable). |
| `max_bid_dph` | `null` | Bid ceiling ($/hr) for interruptible rentals. `null` = bid at current spot price. |
| `max_dph` | `null` | Absolute price ceiling ($/hr) for any rental of this model. `null` = no ceiling. |
| `num_gpus` | `1` | GPUs per instance. Drives the vast offer filter and — when >1 — injects `--tensor-parallel-size N` into the vLLM args. |
| `gpu_name` | `null` | Preferred GPU (e.g., `RTX 5090`). `null` = any that meets `min_vram_gb`. |
| `offer_filters` | `{}` | Free-form per-model vast query overrides (see below). |
| `auto_replicate` | `false` | Master switch. When `false` (default), the replicator skips this model entirely — no auto-rentals, no surprise charges. |

## `offer_filters` reference

A JSON object on the model row. All keys are optional.

| Key | Example | Effect |
|---|---|---|
| `allowed_gpus` | `["RTX 3090","RTX 3090 Ti","RTX 4090"]` | Accept ANY GPU in the list (takes precedence over `gpu_name`). Good for widening the spot pool. |
| `datacenter_only` | `true` | Pin to professional datacenter hosts (vast `hosting_type=1`). |
| `regions` | `["eu","us"]` | Accept only countries in these region sets (`eu`, `us`, `na`). |
| `block_countries` | `["PL"]` | Add to the default country blocklist (`CN/RU/BY/IR/KP/SY`). |
| `allow_countries` | `["CN"]` | Remove from the default blocklist. |
| `min_cpu_cores` | `8` | Minimum effective CPU cores. |
| `min_cpu_ghz` | `3.0` | Minimum CPU clock. |
| `min_inet_down_mbps` | `500` | Minimum download bandwidth. |
| `min_reliability` | `0.97` | Minimum vast reliability score (default `0.95`). |
| `verified` | `true` | Restrict to vast-verified hosts. |

## Recipes

### 1. Fresh install, default behaviour (no configuration)
A new fork seeds one model with all defaults. You rent manually via the
dashboard; nothing auto-happens. `replicator` is running but skips every
model because `auto_replicate=false` everywhere.

### 2. Single always-on instance of one model
```
desired_replicas: 1
rental_mode: on_demand
auto_replicate: true
```
Replicator keeps one guaranteed instance alive. If you also want it to
hibernate when idle, set `auto_pause_minutes` to e.g. `10` — the instance
will pause after 10 min idle and wake on the next request. Replicator still
counts paused instances as alive.

### 3. Cheap resilient pair (interruptible, widened GPU pool)
```
desired_replicas: 2
rental_mode: interruptible
max_bid_dph: 0.10
auto_replicate: true
gpu_name: null
offer_filters: { "allowed_gpus": ["RTX 3090","RTX 3090 Ti","RTX 4090","RTX 4090 D"] }
```
Using `allowed_gpus` instead of a single `gpu_name` triples the candidate
pool on vast, which dramatically reduces eviction frequency and lets the
replicator pick the cheapest offer across cards that all hit the same
VRAM / bandwidth class.
Replicator keeps 2 interruptible instances running with a bid of ≤$0.10/hr.
When vast evicts one, `status_poller` flips the row to `destroyed` within
15s, replicator notices the gap, and rents a replacement within another 30s.

### 4. One 70B model across two GPUs
```
num_gpus: 2
gpu_name: RTX 5090
vllm_args: {"max_model_len": 16384, "dtype": "bfloat16"}
offer_filters: {"datacenter_only": true}
desired_replicas: 1
auto_replicate: true
```
vLLM starts with `--tensor-parallel-size 2` automatically.

### 5. Dev / staging — hand-rent only
```
auto_replicate: false   # default
desired_replicas: 1     # default
```
Nothing changes vs. M3. You drive everything from the Rent page.

## How it works

- **Replicator** runs every 30s. For each model with `auto_replicate=true`
  it counts live instances (`status ∈ {provisioning, booting, ready, paused, waking}`).
  If count < `desired_replicas` it picks the cheapest compliant offer for the
  model's policy and rents one. If count > desired, it destroys the oldest
  **auto-rented** instance (never touches hand-rented rows).
- **Cooldown**: after a rent attempt, the replicator waits 60 s before
  trying again for the same model — protects against flapping when a policy
  is unsatisfiable.
- **Audit**: the `instances.auto_replicated` boolean records how each row
  was created. The Instances page shows an `auto` badge for replicator rows.
- **Per-model sanity**: the replicator only ever acts on `enabled=true`
  models. Disabling a model stops replication without deleting the row.

## Turning it off

- Flip `auto_replicate` to `false` on the model → replicator stops managing
  it. Existing instances keep running; nothing is destroyed.
- Set `desired_replicas` to `0` → replicator will destroy auto-rented
  instances down to zero. Hand-rented instances are preserved.
