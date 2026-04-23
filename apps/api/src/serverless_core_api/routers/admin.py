import hashlib
import secrets as _secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from supabase import Client

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


class ModelPatch(BaseModel):
    slug: str | None = None
    hf_repo: str | None = None
    vllm_args: dict | None = None
    min_vram_gb: int | None = None
    docker_image: str | None = None
    enabled: bool | None = None
    auto_pause_minutes: int | None = None


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
    model_slug: str
    system_prompt: str | None = None
    enabled: bool = True


class PipelinePatch(BaseModel):
    slug: str | None = None
    label: str | None = None
    model_slug: str | None = None
    system_prompt: str | None = None
    enabled: bool | None = None


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
