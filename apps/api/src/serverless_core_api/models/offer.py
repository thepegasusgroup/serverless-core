from typing import Any

from pydantic import BaseModel, Field


class Offer(BaseModel):
    id: int
    gpu_name: str
    num_gpus: int
    gpu_ram_gb: float = Field(description="VRAM per GPU in GB")
    dph: float = Field(description="Dollars per hour total")
    reliability: float
    cpu_name: str | None = None
    cpu_cores: int | None = None
    cpu_cores_effective: float | None = None
    cpu_ghz: float | None = None
    disk_gb: float | None = None
    inet_down_mbps: float | None = None
    inet_up_mbps: float | None = None
    cuda_max: float | None = None
    datacenter: str | None = None

    @classmethod
    def from_vast(cls, raw: dict[str, Any]) -> "Offer":
        gpu_ram_mb = raw.get("gpu_ram") or 0
        return cls(
            id=int(raw["id"]),
            gpu_name=raw.get("gpu_name") or "unknown",
            num_gpus=int(raw.get("num_gpus") or 1),
            gpu_ram_gb=round(gpu_ram_mb / 1024, 1),
            dph=float(raw.get("dph_total") or 0.0),
            reliability=float(raw.get("reliability2") or 0.0),
            cpu_name=raw.get("cpu_name"),
            cpu_cores=raw.get("cpu_cores"),
            cpu_cores_effective=raw.get("cpu_cores_effective"),
            cpu_ghz=raw.get("cpu_ghz"),
            disk_gb=raw.get("disk_space"),
            inet_down_mbps=raw.get("inet_down"),
            inet_up_mbps=raw.get("inet_up"),
            cuda_max=raw.get("cuda_max_good"),
            datacenter=raw.get("geolocation"),
        )
