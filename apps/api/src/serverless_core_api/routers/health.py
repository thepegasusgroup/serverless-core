from fastapi import APIRouter, Request

from serverless_core_api.vast import VastClient

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    vast: VastClient = request.app.state.vast
    vast_ok = await vast.ping()
    return {"ok": True, "vast_ok": vast_ok}
