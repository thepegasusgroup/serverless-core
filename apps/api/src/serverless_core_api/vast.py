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

    async def show_instance(self, contract_id: int) -> dict[str, Any]:
        r = await self._client.get(f"/instances/{contract_id}/")
        r.raise_for_status()
        return r.json()
