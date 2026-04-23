from __future__ import annotations

import logging
import secrets
from typing import Any
from uuid import uuid4

from supabase import Client

from serverless_core_api.config import Settings
from serverless_core_api.vast import VastClient

logger = logging.getLogger("serverless_core_api.rental")


def _build_vllm_args(hf_repo: str, vllm_args: dict[str, Any]) -> str:
    parts = [f"--model {hf_repo}"]
    for key, value in (vllm_args or {}).items():
        flag = f"--{key.replace('_', '-')}"
        parts.append(f"{flag} {value}")
    return " ".join(parts)


async def rent_instance(
    *,
    offer_id: int,
    model_id: str | None,
    model_slug: str | None,
    vast: VastClient,
    sb: Client,
    settings: Settings,
) -> dict[str, Any]:
    # Resolve model.
    q = sb.table("models").select("*").eq("enabled", True).limit(1)
    if model_id:
        q = q.eq("id", model_id)
    elif model_slug:
        q = q.eq("slug", model_slug)
    else:
        raise ValueError("model_id or model_slug required")

    res = q.execute()
    if not res.data:
        raise ValueError("model not found or disabled")
    model = res.data[0]

    instance_id = str(uuid4())
    # Per-instance vLLM API key. vLLM enforces `Authorization: Bearer <key>`
    # on every /v1/* call when --api-key is set, so only our proxy (which
    # stores the key) can reach the box — scanning the public IP gets 401.
    vllm_api_key = "sc_inst_" + secrets.token_urlsafe(24)
    vllm_args = (
        _build_vllm_args(model["hf_repo"], model.get("vllm_args") or {})
        + f" --api-key {vllm_api_key}"
    )
    # vast.ai packs docker-run flags into the env dict — port mappings are
    # represented as keys like "-p 8000:8000". Without this, vLLM's HTTP
    # server on :8000 isn't reachable from outside the rented box.
    env = {
        "-p 8000:8000": "1",
        "SC_CONTROL_URL": settings.public_api_url,
        "SC_AGENT_SECRET": settings.agent_shared_secret,
        "SC_INSTANCE_ID": instance_id,
        "SC_MODEL_SLUG": model["slug"],
        "VLLM_ARGS": vllm_args,
    }
    if settings.hf_token:
        # vLLM + huggingface_hub auto-pick this up; gives 3-5x faster downloads.
        env["HF_TOKEN"] = settings.hf_token

    label = f"sc-{model['slug']}-{instance_id[:8]}"
    # vLLM's image unpacks to ~35-40GB (CUDA + PyTorch + compiled kernels)
    # + weights (15GB for 7B, more for bigger) + HF cache + tmp. 60GB was
    # consistently hitting "no space left on device" during extraction.
    disk_gb = 80
    logger.info("Creating vast instance offer=%s label=%s disk=%sGB",
                offer_id, label, disk_gb)
    vast_res = await vast.create_instance(
        offer_id=offer_id,
        image=model["docker_image"],
        env=env,
        disk_gb=disk_gb,
        label=label,
    )
    if not vast_res.get("success", False):
        raise RuntimeError(f"vast.ai rejected create: {vast_res}")

    contract_id = vast_res.get("new_contract")
    if not contract_id:
        raise RuntimeError(f"vast.ai response missing new_contract: {vast_res}")

    row = {
        "id": instance_id,
        "vast_contract_id": contract_id,
        "model_id": model["id"],
        "status": "provisioning",
        "vllm_api_key": vllm_api_key,
        "rent_args": {"offer_id": offer_id, "vast_response": vast_res, "label": label},
    }
    insert = sb.table("instances").insert(row).execute()
    logger.info("Instance row created id=%s contract=%s", instance_id, contract_id)
    return insert.data[0] if insert.data else row


async def destroy_instance(
    *,
    instance_id: str,
    vast: VastClient,
    sb: Client,
) -> dict[str, Any]:
    res = sb.table("instances").select("*").eq("id", instance_id).limit(1).execute()
    if not res.data:
        raise ValueError("instance not found")
    row = res.data[0]

    if row["status"] == "destroyed":
        return {"ok": True, "already_destroyed": True}

    contract_id = row.get("vast_contract_id")
    if contract_id:
        try:
            await vast.destroy_instance(int(contract_id))
        except Exception as e:
            logger.warning("Vast destroy for contract=%s errored: %s", contract_id, e)

    update = (
        sb.table("instances")
        .update({"status": "destroyed", "destroyed_at": "now()"})
        .eq("id", instance_id)
        .execute()
    )
    return {"ok": True, "row": update.data[0] if update.data else None}
