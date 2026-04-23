from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from supabase import Client

from serverless_core_api.config import Settings, get_settings
from serverless_core_api.deps import get_service_client, get_staff_user
from serverless_core_api.models.offer import Offer
from serverless_core_api.services.rental import destroy_instance, rent_instance
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
    region: str | None = Query(default=None, description="'eu' | 'us' | 'na' | none"),
    include_blocked: str | None = Query(
        default=None,
        description="Comma-separated country codes to allow back in, e.g. 'CN,RU'",
    ),
    limit: int = Query(default=50, ge=1, le=200),
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
