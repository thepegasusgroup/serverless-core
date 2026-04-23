from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from sc.config import Config

console = Console()


def search(
    gpu: Optional[str] = typer.Option(None, "--gpu", help="GPU name, e.g. RTX_5090"),
    max_dph: Optional[float] = typer.Option(None, "--max-dph", help="Max $/hr"),
    min_vram: Optional[int] = typer.Option(
        None, "--min-vram", help="Min VRAM per GPU in GB"
    ),
    num_gpus: int = typer.Option(1, "--num-gpus"),
    min_reliability: float = typer.Option(0.95, "--min-reliability"),
    min_cpu: Optional[int] = typer.Option(
        None, "--min-cpu", help="Minimum effective CPU cores"
    ),
    min_ghz: Optional[float] = typer.Option(
        None, "--min-ghz", help="Minimum CPU clock (GHz)"
    ),
    min_bandwidth: Optional[int] = typer.Option(
        None, "--min-bandwidth", help="Minimum download bandwidth (Mbps)"
    ),
    datacenter_only: bool = typer.Option(
        False, "--datacenter-only/--no-datacenter-only",
        help="Restrict to professional datacenter hosts",
    ),
    region: Optional[str] = typer.Option(
        None, "--region", help="'eu' | 'us' | 'na'"
    ),
    include_blocked: Optional[str] = typer.Option(
        None,
        "--include-blocked",
        help="Comma-separated country codes to allow past the default block list",
    ),
    limit: int = typer.Option(30, "--limit", help="Rows to display (sorted cheapest first)"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Search vast.ai offers via the serverless-core control plane."""
    cfg = Config.load()
    if not cfg.jwt:
        console.print("[red]No JWT stored. Run `sc login` first.[/red]")
        raise typer.Exit(code=1)

    params: dict[str, object] = {
        "num_gpus": num_gpus,
        "min_reliability": min_reliability,
        "limit": limit,
    }
    if gpu:
        params["gpu"] = gpu
    if max_dph is not None:
        params["max_dph"] = max_dph
    if min_vram is not None:
        params["min_vram"] = min_vram
    if min_cpu is not None:
        params["min_cpu_cores"] = min_cpu
    if min_ghz is not None:
        params["min_cpu_ghz"] = min_ghz
    if min_bandwidth is not None:
        params["min_bandwidth"] = min_bandwidth
    if datacenter_only:
        params["datacenter_only"] = "true"
    if region:
        params["region"] = region
    if include_blocked:
        params["include_blocked"] = include_blocked

    url = cfg.api_url.rstrip("/") + "/admin/offers"
    headers = {"Authorization": f"Bearer {cfg.jwt}"}

    try:
        r = httpx.get(url, params=params, headers=headers, timeout=60.0)
    except httpx.HTTPError as e:
        console.print(f"[red]Request failed:[/red] {e}")
        raise typer.Exit(code=2) from e

    if r.status_code == 401:
        console.print(
            "[red]Unauthorized.[/red] Your JWT may be expired — run `sc login` again."
        )
        raise typer.Exit(code=3)
    if r.status_code == 403:
        console.print("[red]Forbidden.[/red] Your email isn't in the staff allowlist.")
        raise typer.Exit(code=3)
    if r.status_code >= 400:
        console.print(f"[red]API error {r.status_code}:[/red] {r.text}")
        raise typer.Exit(code=4)

    offers = r.json()
    if json_output:
        console.print_json(data=offers)
        return

    if not offers:
        console.print("[yellow]No offers matched.[/yellow]")
        return

    table = Table(title=f"{len(offers)} offers", header_style="bold")
    table.add_column("id", justify="right")
    table.add_column("gpu")
    table.add_column("vram", justify="right")
    table.add_column("$/hr", justify="right")
    table.add_column("rel", justify="right")
    table.add_column("cpu", justify="right")
    table.add_column("ghz", justify="right")
    table.add_column("down", justify="right")
    table.add_column("dc")

    for o in offers:
        cpu_eff = o.get("cpu_cores_effective")
        cpu_total = o.get("cpu_cores")
        cpu_str = (
            f"{cpu_eff:.0f}/{cpu_total}" if cpu_eff is not None and cpu_total
            else str(cpu_total or "-")
        )
        down = o.get("inet_down_mbps")
        table.add_row(
            str(o["id"]),
            str(o.get("gpu_name", "-")),
            f"{o.get('gpu_ram_gb', 0)}GB",
            f"${o.get('dph', 0):.3f}",
            f"{(o.get('reliability', 0) or 0) * 100:.1f}%",
            cpu_str,
            f"{o.get('cpu_ghz') or 0:.1f}" if o.get("cpu_ghz") else "-",
            f"{down:.0f}" if down else "-",
            str(o.get("datacenter") or "-"),
        )
    console.print(table)
