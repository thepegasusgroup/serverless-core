from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from serverless_core_api.deps import get_staff_user
from serverless_core_api.models.offer import Offer
from serverless_core_api.vast import VastClient, build_offer_query

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/offers", response_model=list[Offer])
async def list_offers(
    request: Request,
    gpu: str | None = Query(default=None, description="vast.ai gpu_name, e.g. RTX_4090"),
    max_dph: float | None = Query(default=None, description="Max $/hour total"),
    min_vram: int | None = Query(default=None, description="Min VRAM per GPU in GB"),
    num_gpus: int = Query(default=1, ge=1, le=8),
    min_reliability: float = Query(default=0.95, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=200),
    _user: dict = Depends(get_staff_user),
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
