from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from sc.config import Config

console = Console()


def _authed_request(method: str, path: str, **kwargs) -> httpx.Response:
    cfg = Config.load()
    if not cfg.jwt:
        console.print("[red]No JWT stored. Run `scx login` first.[/red]")
        raise typer.Exit(code=1)
    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {cfg.jwt}"
    url = cfg.api_url.rstrip("/") + path
    return httpx.request(method, url, headers=headers, timeout=120.0, **kwargs)


def _check(r: httpx.Response) -> None:
    if r.status_code == 401:
        console.print("[red]Unauthorized.[/red] Run `scx login` (token may have expired).")
        raise typer.Exit(code=3)
    if r.status_code == 403:
        console.print("[red]Forbidden.[/red] Your email isn't in the staff allowlist.")
        raise typer.Exit(code=3)
    if r.status_code >= 400:
        console.print(f"[red]API error {r.status_code}:[/red] {r.text}")
        raise typer.Exit(code=4)


def rent(
    offer_id: int = typer.Argument(..., help="vast.ai offer id (from `scx offers search`)"),
    model: str = typer.Option(..., "--model", "-m", help="model slug, e.g. qwen2.5-7b-instruct"),
) -> None:
    """Rent a vast.ai offer and spin up vLLM for a model."""
    r = _authed_request(
        "POST", "/admin/instances/rent",
        json={"offer_id": offer_id, "model_slug": model},
    )
    _check(r)
    row = r.json()
    console.print(
        f"[green]Rented.[/green] instance_id=[cyan]{row['id']}[/cyan] "
        f"vast_contract=[cyan]{row['vast_contract_id']}[/cyan]"
    )
    console.print(
        f"Status: [yellow]{row['status']}[/yellow] — first boot downloads "
        "the model, can take 5-15 min."
    )
    console.print(f"Watch: [dim]scx instance show {row['id']}[/dim]")


def list_cmd(
    status_filter: Optional[str] = typer.Option(None, "--status", help="filter by status"),
    limit: int = typer.Option(20, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List instances."""
    params: dict = {"limit": limit}
    if status_filter:
        params["status"] = status_filter
    r = _authed_request("GET", "/admin/instances", params=params)
    _check(r)
    rows = r.json()
    if json_output:
        console.print_json(data=rows)
        return
    if not rows:
        console.print("[yellow]No instances.[/yellow]")
        return

    status_color = {
        "ready": "green",
        "provisioning": "yellow",
        "booting": "yellow",
        "unhealthy": "red",
        "destroyed": "dim",
    }
    table = Table(title=f"{len(rows)} instances", header_style="bold")
    table.add_column("id")
    table.add_column("status")
    table.add_column("vast")
    table.add_column("ip:port")
    table.add_column("heartbeat")
    table.add_column("created")

    for row in rows:
        color = status_color.get(row["status"], "white")
        endpoint = (
            f"{row.get('ip')}:{row.get('port')}" if row.get("ip") else "-"
        )
        table.add_row(
            row["id"],
            f"[{color}]{row['status']}[/{color}]",
            str(row.get("vast_contract_id", "-")),
            endpoint,
            str(row.get("last_heartbeat_at") or "-")[:19],
            str(row.get("created_at") or "-")[:19],
        )
    console.print(table)


def destroy(
    instance_id: str = typer.Argument(..., help="full instance UUID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip confirmation"),
) -> None:
    """Destroy a vast.ai instance (stops billing on vast)."""
    if not yes:
        typer.confirm(
            f"Destroy instance {instance_id}? Billing on vast.ai will stop.",
            abort=True,
        )
    r = _authed_request("DELETE", f"/admin/instances/{instance_id}")
    _check(r)
    body = r.json()
    if body.get("already_destroyed"):
        console.print("[yellow]Already destroyed.[/yellow]")
    else:
        console.print("[green]Destroyed.[/green]")


def show(
    instance_id: str = typer.Argument(..., help="instance UUID"),
) -> None:
    """Show full details of an instance."""
    r = _authed_request("GET", f"/admin/instances/{instance_id}")
    _check(r)
    console.print_json(data=r.json())
