import csv
import hashlib
import io
import json
import re
import secrets as _secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from supabase import Client

from serverless_core_api.anthropic_client import SUPPORTED_MODELS
from serverless_core_api.config import Settings, get_settings
from serverless_core_api.deps import get_service_client, get_staff_user
from serverless_core_api.models.offer import Offer
from serverless_core_api.services.rental import (
    destroy_instance,
    pause_instance,
    rent_instance,
    resume_instance,
)
from serverless_core_api.vast import VastClient, build_offer_query

_REGION_SETS: dict[str, set[str]] = {
    "eu": {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
        "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
        "RO", "SK", "SI", "ES", "SE", "IS", "NO", "LI", "CH", "GB", "UA",
    },
    "us": {"US"},
    "na": {"US", "CA"},
}

# Countries blocked by default. Reasons:
#   CN — Great Firewall: GHCR pulls frequently blocked/throttled, HF slow.
#   RU, BY, IR, KP, SY — sanctions + inconsistent access to HF/GitHub.
# Override per-request via ?include_blocked=CN,RU etc.
_BLOCKED_COUNTRIES: set[str] = {"CN", "RU", "BY", "IR", "KP", "SY"}


def _country_code(offer: dict) -> str:
    g = offer.get("geolocation") or ""
    if "," in g:
        return g.rsplit(",", 1)[-1].strip().upper()
    return g.strip().upper()

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_staff_user)],
)


@router.get("/offers", response_model=list[Offer])
async def list_offers(
    request: Request,
    gpu: str | None = Query(default=None, description="vast.ai gpu_name, e.g. RTX_5090"),
    max_dph: float | None = Query(default=None),
    min_vram: int | None = Query(default=None, description="Min VRAM per GPU in GB"),
    num_gpus: int = Query(default=1, ge=1, le=8),
    min_reliability: float = Query(default=0.95, ge=0.0, le=1.0),
    min_cpu_cores: int | None = Query(default=None, description="Min effective CPU cores"),
    min_cpu_ghz: float | None = Query(default=None, description="Min CPU clock in GHz"),
    min_bandwidth: int | None = Query(
        default=None, description="Min download bandwidth in Mbps"
    ),
    datacenter_only: bool = Query(
        default=False, description="Restrict to professional datacenter hosts"
    ),
    region: str | None = Query(default=None, description="'eu' | 'us' | 'na' | none"),
    include_blocked: str | None = Query(
        default=None,
        description="Comma-separated country codes to allow back in, e.g. 'CN,RU'",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    sb: Client = Depends(get_service_client),
) -> list[Offer]:
    vast: VastClient = request.app.state.vast
    query = build_offer_query(
        gpu=gpu,
        max_dph=max_dph,
        min_vram_gb=min_vram,
        num_gpus=num_gpus,
        min_reliability=min_reliability,
        min_cpu_cores=min_cpu_cores,
        min_cpu_ghz=min_cpu_ghz,
        min_inet_down_mbps=min_bandwidth,
        datacenter_only=datacenter_only,
    )
    try:
        raw = await vast.search_offers(query)
    except Exception as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"vast.ai search failed: {e}"
        ) from e

    if region:
        target = _REGION_SETS.get(region.lower())
        if target is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown region '{region}' (use eu, us, na, or omit)",
            )
        raw = [o for o in raw if _country_code(o) in target]

    allowlist: set[str] = set()
    if include_blocked:
        allowlist = {c.strip().upper() for c in include_blocked.split(",") if c.strip()}
    effective_blocklist = _BLOCKED_COUNTRIES - allowlist
    raw = [o for o in raw if _country_code(o) not in effective_blocklist]

    # Known-bad machines we've rented before that failed with host-level bugs.
    bad_rows = (
        sb.table("bad_machines").select("machine_id").execute().data or []
    )
    bad_ids = {int(r["machine_id"]) for r in bad_rows}
    if bad_ids:
        raw = [o for o in raw if o.get("machine_id") not in bad_ids]

    # Known-bad CPUs (Xeon Phi, weak Broadwell Xeons, etc) — substring match
    # on cpu_name, case-insensitive.
    bad_cpu_rows = (
        sb.table("bad_cpus").select("cpu_name").execute().data or []
    )
    bad_cpu_patterns = [
        (r.get("cpu_name") or "").lower()
        for r in bad_cpu_rows
        if r.get("cpu_name")
    ]
    if bad_cpu_patterns:
        def _cpu_ok(o: dict) -> bool:
            name = (o.get("cpu_name") or "").lower()
            return not name or not any(p in name for p in bad_cpu_patterns)
        raw = [o for o in raw if _cpu_ok(o)]

    offers = [Offer.from_vast(o) for o in raw]
    offers.sort(key=lambda o: o.dph)
    return offers[:limit]


class RentRequest(BaseModel):
    offer_id: int
    model_id: str | None = None
    model_slug: str | None = None


@router.post("/instances/rent", status_code=status.HTTP_201_CREATED)
async def rent(
    body: RentRequest,
    request: Request,
    sb: Client = Depends(get_service_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not (body.model_id or body.model_slug):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Provide model_id or model_slug"
        )
    vast: VastClient = request.app.state.vast
    try:
        return await rent_instance(
            offer_id=body.offer_id,
            model_id=body.model_id,
            model_slug=body.model_slug,
            vast=vast,
            sb=sb,
            settings=settings,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Rent failed: {e}") from e


@router.get("/instances")
def list_instances(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    sb: Client = Depends(get_service_client),
) -> list[dict]:
    q = (
        sb.table("instances")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if status_filter:
        q = q.eq("status", status_filter)
    return q.execute().data or []


@router.get("/instances/{instance_id}")
def get_instance(
    instance_id: str,
    sb: Client = Depends(get_service_client),
) -> dict:
    res = sb.table("instances").select("*").eq("id", instance_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instance not found")
    return res.data[0]


# -----------------------------------------------------------------------------
# Models catalogue
# -----------------------------------------------------------------------------


class ModelIn(BaseModel):
    slug: str
    hf_repo: str
    vllm_args: dict = {}
    min_vram_gb: int = 16
    docker_image: str
    enabled: bool = True
    auto_pause_minutes: int | None = 10
    # --- Phase A rental policy (all optional; defaults preserve M3 behaviour) ---
    desired_replicas: int = 1
    rental_mode: str = "on_demand"  # "on_demand" | "interruptible"
    max_bid_dph: float | None = None
    max_dph: float | None = None
    num_gpus: int = 1
    gpu_name: str | None = None
    offer_filters: dict = {}
    auto_replicate: bool = False


class ModelPatch(BaseModel):
    slug: str | None = None
    hf_repo: str | None = None
    vllm_args: dict | None = None
    min_vram_gb: int | None = None
    docker_image: str | None = None
    enabled: bool | None = None
    auto_pause_minutes: int | None = None
    desired_replicas: int | None = None
    rental_mode: str | None = None
    max_bid_dph: float | None = None
    max_dph: float | None = None
    num_gpus: int | None = None
    gpu_name: str | None = None
    offer_filters: dict | None = None
    auto_replicate: bool | None = None


@router.get("/models")
def list_models(sb: Client = Depends(get_service_client)) -> list[dict]:
    return (
        sb.table("models")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


@router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model(
    body: ModelIn, sb: Client = Depends(get_service_client)
) -> dict:
    try:
        res = sb.table("models").insert(body.model_dump()).execute()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not res.data:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Insert returned nothing")
    return res.data[0]


@router.patch("/models/{model_id}")
def update_model(
    model_id: str,
    body: ModelPatch,
    sb: Client = Depends(get_service_client),
) -> dict:
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    res = sb.table("models").update(patch).eq("id", model_id).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    return res.data[0]


@router.delete("/models/{model_id}")
def delete_model(
    model_id: str, sb: Client = Depends(get_service_client)
) -> dict:
    sb.table("models").delete().eq("id", model_id).execute()
    return {"ok": True}


# -----------------------------------------------------------------------------
# Pipelines
# -----------------------------------------------------------------------------


class PipelineIn(BaseModel):
    slug: str
    label: str
    # v3: step chain. Each step: {kind, ...config}
    steps: list[dict] = []
    # output stage (applied to final step's text)
    output_mode: str = "return"
    webhook_url: str | None = None
    webhook_headers: dict = {}
    timeout_seconds: int = 120
    enabled: bool = True
    # Legacy single-step fields (kept for backwards compatibility with v2 DB
    # rows; new pipelines should use `steps`).
    model_slug: str | None = None
    system_prompt: str | None = None
    user_template: str | None = None
    vllm_overrides: dict = {}
    response_format: str = "text"
    response_schema: dict | None = None


class PipelinePatch(BaseModel):
    slug: str | None = None
    label: str | None = None
    steps: list[dict] | None = None
    output_mode: str | None = None
    webhook_url: str | None = None
    webhook_headers: dict | None = None
    timeout_seconds: int | None = None
    enabled: bool | None = None
    model_slug: str | None = None
    system_prompt: str | None = None
    user_template: str | None = None
    vllm_overrides: dict | None = None
    response_format: str | None = None
    response_schema: dict | None = None


@router.get("/pipelines")
def list_pipelines(sb: Client = Depends(get_service_client)) -> list[dict]:
    return (
        sb.table("pipelines")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


@router.post("/pipelines", status_code=status.HTTP_201_CREATED)
def create_pipeline(
    body: PipelineIn, sb: Client = Depends(get_service_client)
) -> dict:
    res = sb.table("pipelines").insert(body.model_dump()).execute()
    if not res.data:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Insert returned nothing")
    return res.data[0]


@router.patch("/pipelines/{pid}")
def update_pipeline(
    pid: str, body: PipelinePatch, sb: Client = Depends(get_service_client)
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    res = sb.table("pipelines").update(patch).eq("id", pid).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pipeline not found")
    return res.data[0]


@router.delete("/pipelines/{pid}")
def delete_pipeline(pid: str, sb: Client = Depends(get_service_client)) -> dict:
    sb.table("pipelines").delete().eq("id", pid).execute()
    return {"ok": True}


# -----------------------------------------------------------------------------
# API keys
# -----------------------------------------------------------------------------


class CreateKeyRequest(BaseModel):
    label: str
    requests_per_minute: int | None = None
    allowed_models: list[str] | None = None  # None = all; [] = none
    allowed_pipelines: list[str] | None = None


class PatchKeyRequest(BaseModel):
    requests_per_minute: int | None = None
    allowed_models: list[str] | None = None
    allowed_pipelines: list[str] | None = None


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: CreateKeyRequest,
    sb: Client = Depends(get_service_client),
    user: dict = Depends(get_staff_user),
) -> dict:
    token = "sc_live_" + _secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    prefix = token[:12]
    row = (
        sb.table("api_keys")
        .insert(
            {
                "key_hash": key_hash,
                "prefix": prefix,
                "label": body.label,
                "requests_per_minute": body.requests_per_minute,
                "allowed_models": body.allowed_models,
                "allowed_pipelines": body.allowed_pipelines,
            }
        )
        .execute()
        .data[0]
    )
    return {
        "id": row["id"],
        "label": row["label"],
        "prefix": prefix,
        "key": token,  # plaintext — shown ONCE, never returned again
        "requests_per_minute": row["requests_per_minute"],
        "allowed_models": row["allowed_models"],
        "allowed_pipelines": row["allowed_pipelines"],
        "created_at": row["created_at"],
    }


@router.patch("/api-keys/{key_id}")
def patch_api_key(
    key_id: str,
    body: PatchKeyRequest,
    sb: Client = Depends(get_service_client),
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    res = sb.table("api_keys").update(patch).eq("id", key_id).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    return res.data[0]


@router.get("/api-keys")
def list_api_keys(
    sb: Client = Depends(get_service_client),
) -> list[dict]:
    rows = (
        sb.table("api_keys")
        .select("id,label,prefix,created_at,last_used_at,revoked_at")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    return rows


@router.delete("/api-keys/{key_id}")
def revoke_api_key(
    key_id: str, sb: Client = Depends(get_service_client)
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    res = (
        sb.table("api_keys")
        .update({"revoked_at": now})
        .eq("id", key_id)
        .is_("revoked_at", "null")
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found or already revoked")
    return {"ok": True}


# -----------------------------------------------------------------------------


@router.delete("/instances/{instance_id}")
async def destroy(
    instance_id: str,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    vast: VastClient = request.app.state.vast
    try:
        return await destroy_instance(
            instance_id=instance_id, vast=vast, sb=sb
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.post("/instances/{instance_id}/pause")
async def pause(
    instance_id: str,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    vast: VastClient = request.app.state.vast
    try:
        return await pause_instance(instance_id=instance_id, vast=vast, sb=sb)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.get("/request-logs")
def list_request_logs(
    limit: int = Query(default=100, ge=1, le=500),
    api_key_id: str | None = Query(default=None),
    sb: Client = Depends(get_service_client),
) -> list[dict]:
    q = (
        sb.table("request_logs")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if api_key_id:
        q = q.eq("api_key_id", api_key_id)
    return q.execute().data or []


@router.post("/instances/{instance_id}/resume")
async def resume(
    instance_id: str,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    vast: VastClient = request.app.state.vast
    try:
        return await resume_instance(instance_id=instance_id, vast=vast, sb=sb)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/instances/{instance_id}/debug")
async def instance_debug(
    instance_id: str,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    """Fetch everything we know about an instance — DB row + live vast state."""
    res = sb.table("instances").select("*").eq("id", instance_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instance not found")
    row = res.data[0]

    # Strip the vLLM key from public output — it's a secret.
    row = {k: ("<redacted>" if k == "vllm_api_key" and v else v) for k, v in row.items()}

    vast_info: dict = {}
    contract_id = row.get("vast_contract_id")
    if contract_id:
        vast: VastClient = request.app.state.vast
        try:
            info = await vast.show_instance(int(contract_id))
            full = info.get("instances", info) if isinstance(info, dict) else {}
            # Pick a useful subset — full response has ~120 fields.
            keep = [
                "actual_status", "cur_state", "next_state", "status_msg",
                "machine_id", "host_id", "hosting_type",
                "cpu_name", "cpu_util", "cpu_cores", "cpu_ghz",
                "gpu_name", "gpu_util", "gpu_temp", "gpu_ram", "num_gpus",
                "mem_usage", "disk_usage",
                "inet_down", "inet_up", "geolocation",
                "dph_total", "start_date", "end_date",
                "driver_version", "cuda_max_good",
                "public_ipaddr", "ssh_host", "ssh_port",
            ]
            vast_info = {k: full.get(k) for k in keep if k in full}
        except Exception as e:  # noqa: BLE001
            vast_info = {"_error": str(e)}

    return {"row": row, "vast": vast_info}


@router.get("/instances/{instance_id}/logs")
async def instance_logs(
    instance_id: str,
    request: Request,
    tail: int = Query(default=200, ge=1, le=2000),
    sb: Client = Depends(get_service_client),
) -> dict:
    res = sb.table("instances").select("vast_contract_id").eq("id", instance_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instance not found")
    contract_id = res.data[0].get("vast_contract_id")
    if not contract_id:
        return {"logs": "", "note": "Instance has no vast contract ID yet."}

    vast: VastClient = request.app.state.vast
    try:
        text = await vast.get_instance_logs(int(contract_id), tail=tail)
    except Exception as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"vast.ai logs failed: {e}"
        ) from e
    return {"logs": text}


# -----------------------------------------------------------------------------
# Datasets — Claude Batch API–backed synthetic data generation
# -----------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _require_anthropic(request: Request):
    """Ensure the Anthropic client was wired at startup. Returns the client."""
    ac = getattr(request.app.state, "anthropic", None)
    if ac is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Anthropic integration not configured. Set ANTHROPIC_API_KEY as a Fly secret.",
        )
    return ac


class DatasetCreate(BaseModel):
    slug: str
    label: str
    # 'synthesis' (default) submits prompts to Claude Batch; 'eval' creates
    # an empty dataset for manual row entry — used to track fine-tuned-model
    # outputs + compile/runtime results while iterating v1 → v2.
    kind: str = "synthesis"
    model: str = "claude-opus-4-7"
    system: str = ""
    prompts: list[str] = []
    # 8192 is a safer default than 4096 for code/long-JSON generation. We
    # observed 35% truncation rate at 4K on Paper plugin outputs; at 8K
    # it'd be <5%. For very long outputs (70B-spec code, multi-file projects)
    # bump to 16384 on the client side.
    max_tokens: int = 8192
    cache_system: bool = True
    submit_now: bool = True  # if False, saves as draft


class DatasetPatch(BaseModel):
    label: str | None = None


class DatasetRowCreate(BaseModel):
    """Manual row entry for eval-kind datasets."""
    system: str = ""
    user: str
    output: str | None = None
    meta: dict = {}


class DatasetRowPatch(BaseModel):
    """Update output and/or per-row eval metadata after testing the plugin."""
    output: str | None = None
    meta: dict | None = None


@router.get("/datasets")
def list_datasets(sb: Client = Depends(get_service_client)) -> list[dict]:
    return (
        sb.table("datasets")
        .select(
            "id,slug,label,kind,status,provider,progress_completed,progress_total,"
            "progress_errored,usage,error,submitted_at,completed_at,created_at"
        )
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
async def create_dataset(
    body: DatasetCreate,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    if not _SLUG_RE.match(body.slug):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Slug must be lowercase alphanumeric with dashes (1-63 chars)",
        )
    if body.kind not in ("synthesis", "eval"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "kind must be 'synthesis' or 'eval'"
        )

    if body.kind == "synthesis":
        if body.model not in SUPPORTED_MODELS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Model must be one of: {', '.join(SUPPORTED_MODELS)}",
            )
        if not body.prompts:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "prompts cannot be empty"
            )
        if len(body.prompts) > 5000:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Up to 5000 prompts per dataset in this build",
            )
        row = {
            "slug": body.slug,
            "label": body.label,
            "kind": "synthesis",
            "provider": "claude_batch",
            "status": "draft",
            "progress_total": len(body.prompts),
            "config": {
                "model": body.model,
                "system": body.system,
                "prompts": body.prompts,
                "max_tokens": body.max_tokens,
                "cache_system": body.cache_system,
            },
        }
    else:
        # Eval kind: no batch, no prompts. Starts at 'completed' so the UI
        # always shows the export buttons; rows are added manually via
        # POST /datasets/{id}/rows as you test plugins locally.
        row = {
            "slug": body.slug,
            "label": body.label,
            "kind": "eval",
            "provider": "local_model",
            "status": "completed",
            "progress_total": 0,
            "config": {
                # Default system prompt to apply when emitting JSONL for
                # training, in case the caller wants to paste only the user
                # spec per row. Can be empty.
                "system": body.system,
            },
        }

    try:
        res = sb.table("datasets").insert(row).execute()
    except Exception as e:  # noqa: BLE001 (likely unique-violation on slug)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not res.data:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "insert returned nothing"
        )
    created = res.data[0]

    if body.kind == "synthesis" and body.submit_now:
        created = await _submit_dataset(created, request, sb)
    return created


async def _submit_dataset(
    dataset: dict, request: Request, sb: Client
) -> dict:
    """Submit a draft dataset to Anthropic. Idempotent on external_batch_id."""
    if dataset["status"] not in ("draft",):
        return dataset  # already submitted
    ac = _require_anthropic(request)
    cfg = dataset["config"]

    # Flip to submitting so a concurrent caller doesn't double-submit.
    sb.table("datasets").update({"status": "submitting"}).eq(
        "id", dataset["id"]
    ).execute()
    try:
        batch = await ac.submit_batch(
            model=cfg["model"],
            system_prompt=cfg.get("system", ""),
            user_prompts=cfg["prompts"],
            max_tokens=cfg.get("max_tokens", 4096),
            cache_system=cfg.get("cache_system", True),
        )
    except Exception as e:  # noqa: BLE001
        sb.table("datasets").update(
            {"status": "failed", "error": f"submit failed: {e}"}
        ).eq("id", dataset["id"]).execute()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Anthropic submit failed: {e}"
        ) from e

    now = datetime.now(timezone.utc).isoformat()
    upd = (
        sb.table("datasets")
        .update(
            {
                "status": "running",
                "external_batch_id": batch["id"],
                "submitted_at": now,
            }
        )
        .eq("id", dataset["id"])
        .execute()
    )
    return upd.data[0] if upd.data else dataset


@router.post("/datasets/{dataset_id}/submit")
async def submit_dataset(
    dataset_id: str,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    res = sb.table("datasets").select("*").eq("id", dataset_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    return await _submit_dataset(res.data[0], request, sb)


@router.post("/datasets/{dataset_id}/cancel")
async def cancel_dataset(
    dataset_id: str,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    res = sb.table("datasets").select("*").eq("id", dataset_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    d = res.data[0]
    if d["status"] not in ("submitting", "running"):
        return {"ok": True, "already": d["status"]}
    ac = _require_anthropic(request)
    if d.get("external_batch_id"):
        try:
            await ac.cancel_batch(d["external_batch_id"])
        except Exception as e:  # noqa: BLE001
            # Cancel is idempotent on our side even if Anthropic errors —
            # the poller will eventually mark it terminal.
            pass  # noqa: S110
    sb.table("datasets").update(
        {"status": "canceled", "completed_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", dataset_id).execute()
    return {"ok": True}


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str, sb: Client = Depends(get_service_client)
) -> dict:
    res = sb.table("datasets").select("*").eq("id", dataset_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    return res.data[0]


@router.get("/datasets/{dataset_id}/rows")
def list_dataset_rows(
    dataset_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sb: Client = Depends(get_service_client),
) -> dict:
    total = (
        sb.table("dataset_rows")
        .select("id", count="exact")
        .eq("dataset_id", dataset_id)
        .execute()
        .count
        or 0
    )
    rows = (
        sb.table("dataset_rows")
        .select("id,row_index,input,output,usage,error,meta,created_at")
        .eq("dataset_id", dataset_id)
        .order("row_index")
        .range(offset, offset + limit - 1)
        .execute()
        .data
        or []
    )
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


@router.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: str, sb: Client = Depends(get_service_client)
) -> dict:
    sb.table("datasets").delete().eq("id", dataset_id).execute()
    return {"ok": True}


@router.get("/datasets/{dataset_id}/export")
def export_dataset(
    dataset_id: str,
    fmt: str = Query(default="jsonl", pattern="^(jsonl|csv)$"),
    compile: bool | None = Query(
        default=None,
        description="Eval filter: only include rows where meta.compile == this value",
    ),
    runtime: bool | None = Query(
        default=None,
        description="Eval filter: only include rows where meta.runtime == this value",
    ),
    sb: Client = Depends(get_service_client),
):
    """Stream the dataset as JSONL (OpenAI-compat messages format) or CSV.

    JSONL emits {"messages": [{role:system,...}, {role:user,...}, {role:assistant,...}]}
    per line — ready for fine-tuning.

    For eval-kind datasets, pass `compile=true&runtime=true` to produce a
    clean training set containing only rows that both compiled AND ran
    without errors. Errored/unset rows are always dropped automatically.
    """
    ds_res = sb.table("datasets").select("*").eq("id", dataset_id).limit(1).execute()
    if not ds_res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    ds = ds_res.data[0]
    slug = ds["slug"]

    def _meta_ok(meta: dict | None) -> bool:
        m = meta or {}
        if compile is not None and bool(m.get("compile")) != compile:
            return False
        if runtime is not None and bool(m.get("runtime")) != runtime:
            return False
        return True

    def _iter_rows():
        # Paginated fetch — 500 at a time to avoid huge payloads.
        step = 500
        start = 0
        while True:
            r = (
                sb.table("dataset_rows")
                .select("row_index,input,output,error,meta")
                .eq("dataset_id", dataset_id)
                .order("row_index")
                .range(start, start + step - 1)
                .execute()
            )
            chunk = r.data or []
            if not chunk:
                return
            for row in chunk:
                if not _meta_ok(row.get("meta")):
                    continue
                yield row
            if len(chunk) < step:
                return
            start += step

    if fmt == "jsonl":
        def _gen():
            for row in _iter_rows():
                if row.get("error") or not row.get("output"):
                    continue  # skip errored rows in export
                inp = row.get("input") or {}
                messages = []
                if inp.get("system"):
                    messages.append({"role": "system", "content": inp["system"]})
                if inp.get("user"):
                    messages.append({"role": "user", "content": inp["user"]})
                messages.append({"role": "assistant", "content": row["output"]})
                yield json.dumps({"messages": messages}, ensure_ascii=False) + "\n"

        return StreamingResponse(
            _gen(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}.jsonl"'
            },
        )

    # CSV: row_index, system, user, assistant, error, meta(json)
    def _csv_gen():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["row_index", "system", "user", "assistant", "error", "meta"]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for row in _iter_rows():
            inp = row.get("input") or {}
            writer.writerow(
                [
                    row.get("row_index"),
                    inp.get("system", ""),
                    inp.get("user", ""),
                    row.get("output", "") or "",
                    row.get("error", "") or "",
                    json.dumps(row.get("meta") or {}, ensure_ascii=False),
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _csv_gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{slug}.csv"'},
    )


# -----------------------------------------------------------------------------
# Eval-kind dataset_rows: manual add / edit / delete
# -----------------------------------------------------------------------------


@router.post("/datasets/{dataset_id}/rows", status_code=status.HTTP_201_CREATED)
def create_dataset_row(
    dataset_id: str,
    body: DatasetRowCreate,
    sb: Client = Depends(get_service_client),
) -> dict:
    """Manually add a row to an eval dataset.

    Used while iterating on the fine-tuned model: paste the spec you sent,
    the raw model output, and per-row eval metadata (compile/runtime/notes).
    Auto-assigns the next row_index.
    """
    ds_res = sb.table("datasets").select("id,kind").eq("id", dataset_id).limit(1).execute()
    if not ds_res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found")
    if ds_res.data[0]["kind"] != "eval":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Rows can only be added manually to eval-kind datasets",
        )

    # Auto-increment row_index. Small race here is fine — the (dataset_id,
    # row_index) unique constraint will make a colliding insert fail, at
    # which point the client retries.
    last = (
        sb.table("dataset_rows")
        .select("row_index")
        .eq("dataset_id", dataset_id)
        .order("row_index", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    next_idx = (last[0]["row_index"] + 1) if last else 0

    row = {
        "dataset_id": dataset_id,
        "row_index": next_idx,
        "input": {"system": body.system, "user": body.user},
        "output": body.output,
        "meta": body.meta or {},
    }
    try:
        res = sb.table("dataset_rows").insert(row).execute()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not res.data:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "insert returned nothing"
        )

    # Bump progress_total so the detail page progress readout looks right.
    sb.table("datasets").update(
        {"progress_total": next_idx + 1, "progress_completed": next_idx + 1}
    ).eq("id", dataset_id).execute()

    return res.data[0]


@router.patch("/datasets/{dataset_id}/rows/{row_id}")
def patch_dataset_row(
    dataset_id: str,
    row_id: str,
    body: DatasetRowPatch,
    sb: Client = Depends(get_service_client),
) -> dict:
    """Update a row's output or eval metadata (typical use: flip compile/
    runtime flags after testing the generated plugin locally)."""
    upd: dict = {}
    if body.output is not None:
        upd["output"] = body.output
    if body.meta is not None:
        upd["meta"] = body.meta
    if not upd:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    res = (
        sb.table("dataset_rows")
        .update(upd)
        .eq("id", row_id)
        .eq("dataset_id", dataset_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Row not found")
    return res.data[0]


@router.delete("/datasets/{dataset_id}/rows/{row_id}")
def delete_dataset_row(
    dataset_id: str,
    row_id: str,
    sb: Client = Depends(get_service_client),
) -> dict:
    sb.table("dataset_rows").delete().eq("id", row_id).eq(
        "dataset_id", dataset_id
    ).execute()
    # Refresh progress counters based on remaining row count.
    remaining = (
        sb.table("dataset_rows")
        .select("id", count="exact")
        .eq("dataset_id", dataset_id)
        .execute()
        .count
        or 0
    )
    sb.table("datasets").update(
        {"progress_total": remaining, "progress_completed": remaining}
    ).eq("id", dataset_id).execute()
    return {"ok": True}
