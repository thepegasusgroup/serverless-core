import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from serverless_core_api.deps import get_service_client, verify_agent_secret

logger = logging.getLogger("serverless_core_api.internal")

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_agent_secret)],
)


class RegisterRequest(BaseModel):
    instance_id: str
    ip: str
    port: int
    model_slug: str
    agent_version: str


class HeartbeatRequest(BaseModel):
    instance_id: str
    vllm_healthy: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/instances/register")
def register(
    body: RegisterRequest,
    sb: Client = Depends(get_service_client),
) -> dict:
    now = _now()
    res = (
        sb.table("instances")
        .update({
            "ip": body.ip,
            "port": body.port,
            "status": "ready",
            "registered_at": now,
            "last_heartbeat_at": now,
        })
        .eq("id", body.instance_id)
        .neq("status", "destroyed")
        .execute()
    )
    if not res.data:
        logger.warning("Register for unknown/destroyed instance_id=%s", body.instance_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown or destroyed instance")
    logger.info("Instance %s registered at %s:%s", body.instance_id, body.ip, body.port)
    return {"ok": True}


@router.post("/instances/heartbeat")
def heartbeat(
    body: HeartbeatRequest,
    sb: Client = Depends(get_service_client),
) -> dict:
    now = _now()
    new_status = "ready" if body.vllm_healthy else "unhealthy"
    res = (
        sb.table("instances")
        .update({"last_heartbeat_at": now, "status": new_status})
        .eq("id", body.instance_id)
        .neq("status", "destroyed")
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown or destroyed instance")
    return {"ok": True}
