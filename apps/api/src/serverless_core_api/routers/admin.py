from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from supabase import Client

from serverless_core_api.config import Settings, get_settings
from serverless_core_api.deps import get_service_client, get_staff_user
from serverless_core_api.models.offer import Offer
from serverless_core_api.services.rental import destroy_instance, rent_instance
from serverless_core_api.vast import VastClient, build_offer_query

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_staff_user)],
)


@router.get("/offers", response_model=list[Offer])
async def list_offers(
    request: Request,
    gpu: str | None = Query(default=None, description="vast.ai gpu_name, e.g. RTX_4090"),
    max_dph: float | None = Query(default=None),
    min_vram: int | None = Query(default=None, description="Min VRAM per GPU in GB"),
    num_gpus: int = Query(default=1, ge=1, le=8),
    min_reliability: float = Query(default=0.95, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Offer]:
    vast: VastClient = request.app.state.vast
    query = build_offer_query(
        gpu=gpu,
        max_dph=max_dph,
        min_vram_gb=min_vram,
        num_gpus=num_gpus,
        min_reliability=min_reliability,
    )
    try:
        raw = await vast.search_offers(query)
    except Exception as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"vast.ai search failed: {e}"
        ) from e

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
