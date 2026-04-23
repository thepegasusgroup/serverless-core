import json
from typing import Any

import httpx

VAST_API_BASE = "https://console.vast.ai/api/v0"


def build_offer_query(
    *,
    gpu: str | None = None,
    max_dph: float | None = None,
    min_vram_gb: int | None = None,
    num_gpus: int = 1,
    min_reliability: float = 0.95,
    verified: bool | None = None,
    rentable: bool = True,
    min_cpu_cores: int | None = None,
    min_cpu_ghz: float | None = None,
    min_inet_down_mbps: int | None = None,
    datacenter_only: bool = False,
) -> dict[str, Any]:
    # `limit` + `order` go INSIDE the q dict — they're part of vast's query DSL.
    # Default server-side page cap is ~64; bumping it returns the full slice
    # so our region/country filters see every candidate before we truncate.
    q: dict[str, Any] = {
        "num_gpus": {"eq": num_gpus},
        "reliability2": {"gte": min_reliability},
        "rentable": {"eq": rentable},
        "limit": 1000,
        "order": [["dph_total", "asc"]],
    }
    if verified is not None:
        q["verified"] = {"eq": verified}
    if gpu:
        # vast.ai stores GPU names with spaces (e.g. "RTX 5090"); underscores
        # are more shell-friendly so we accept either form.
        q["gpu_name"] = {"eq": gpu.replace("_", " ")}
    if max_dph is not None:
        q["dph_total"] = {"lt": max_dph}
    if min_vram_gb is not None:
        q["gpu_ram"] = {"gte": min_vram_gb * 1000}
    if min_cpu_cores is not None:
        q["cpu_cores_effective"] = {"gte": min_cpu_cores}
    if min_cpu_ghz is not None:
        q["cpu_ghz"] = {"gte": min_cpu_ghz}
    if min_inet_down_mbps is not None:
        q["inet_down"] = {"gte": min_inet_down_mbps}
    if datacenter_only:
        # vast.ai uses `hosting_type` int: 0 = consumer/rig, 1 = datacenter.
        q["hosting_type"] = {"eq": 1}
    return q


class VastClient:
    def __init__(self, api_key: str, base_url: str = VAST_API_BASE) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            r = await self._client.get("/users/current/")
        except httpx.HTTPError:
            return False
        return r.status_code == 200

    async def search_offers(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        r = await self._client.get("/bundles/", params={"q": json.dumps(query)})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data.get("offers", [])
        return []

    async def create_instance(
        self,
        *,
        offer_id: int,
        image: str,
        env: dict[str, str],
        disk_gb: int,
        label: str,
        onstart: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_id": "me",
            "image": image,
            "env": env,
            "disk": disk_gb,
            "label": label,
            "runtype": "args",
        }
        if onstart:
            payload["onstart"] = onstart
        r = await self._client.put(f"/asks/{offer_id}/", json=payload)
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("msg") or body.get("error") or r.text
            except Exception:
                msg = r.text
            raise RuntimeError(f"vast.ai create {r.status_code}: {msg}")
        return r.json()

    async def destroy_instance(self, contract_id: int) -> dict[str, Any]:
        r = await self._client.delete(f"/instances/{contract_id}/")
        r.raise_for_status()
        return r.json() if r.content else {}

    async def pause_instance(self, contract_id: int) -> dict[str, Any]:
        """Stop the container but keep the disk (model cache survives)."""
        r = await self._client.put(
            f"/instances/{contract_id}/", json={"state": "stopped"}
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    async def resume_instance(self, contract_id: int) -> dict[str, Any]:
        """Restart a previously paused instance."""
        r = await self._client.put(
            f"/instances/{contract_id}/", json={"state": "running"}
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    async def show_instance(self, contract_id: int) -> dict[str, Any]:
        r = await self._client.get(f"/instances/{contract_id}/")
        r.raise_for_status()
        return r.json()

    async def get_instance_logs(
        self, contract_id: int, tail: int = 200
    ) -> str:
        """Fetch the container logs from vast.ai.

        Two-step flow: ask vast to materialise a log blob, then HTTP-GET it.
        """
        # 1. Request log generation.
        r = await self._client.put(
            f"/instances/request_logs/{contract_id}/",
            json={"tail": str(tail)},
        )
        r.raise_for_status()
        data = r.json()
        log_url = data.get("result_url") or data.get("url")
        if not log_url:
            return ""

        # 2. Fetch from the signed URL (different host, no auth header).
        import httpx as _httpx  # local import to avoid top-level churn

        import asyncio as _asyncio

        async with _httpx.AsyncClient(timeout=30.0) as anon:
            # S3 returns 403 (and sometimes 404) while the blob is being
            # uploaded. Retry a handful of times with backoff.
            for attempt in range(10):
                resp = await anon.get(log_url)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code in (403, 404):
                    await _asyncio.sleep(0.5 + attempt * 0.3)
                    continue
                resp.raise_for_status()
        return "(logs not yet available — try again in a few seconds)"
