import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from supabase import Client

from serverless_core_api.deps import get_service_client, verify_agent_secret
from serverless_core_api.vast import VastClient

logger = logging.getLogger("serverless_core_api.internal")

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_agent_secret)],
)


class RegisterRequest(BaseModel):
    instance_id: str
    ip: str | None = None
    port: int | None = None
    model_slug: str
    agent_version: str


class HeartbeatRequest(BaseModel):
    instance_id: str
    vllm_healthy: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/instances/register")
async def register(
    body: RegisterRequest,
    request: Request,
    sb: Client = Depends(get_service_client),
) -> dict:
    now = _now()

    # If the agent didn't know its public IP (vast env vars missing), look it
    # up ourselves from vast.ai. This gives us the external ip:port mapping.
    ip = body.ip or None
    port = body.port or None
    if not ip:
        vast: VastClient = request.app.state.vast
        # Find our row first for the contract id.
        row_res = (
            sb.table("instances")
            .select("vast_contract_id")
            .eq("id", body.instance_id)
            .limit(1)
            .execute()
        )
        contract_id = (
            row_res.data[0].get("vast_contract_id") if row_res.data else None
        )
        if contract_id:
            try:
                info = await vast.show_instance(int(contract_id))
                vi = info.get("instances", info) if isinstance(info, dict) else {}
                ip = vi.get("public_ipaddr") or ip
                ports = vi.get("ports") or {}
                mapping = ports.get("8000/tcp") if isinstance(ports, dict) else None
                if mapping and isinstance(mapping, list) and mapping:
                    host_port = mapping[0].get("HostPort")
                    if host_port:
                        port = int(host_port)
            except Exception as e:
                logger.warning("Could not fetch IP from vast: %s", e)

    res = (
        sb.table("instances")
        .update({
            "ip": ip,
            "port": port,
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
    logger.info("Instance %s registered at %s:%s", body.instance_id, ip, port)
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
