from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from sc.config import Config

console = Console()


def _authed(method: str, path: str, **kwargs) -> httpx.Response:
    cfg = Config.load()
    if not cfg.jwt:
        console.print("[red]No JWT stored. Run `scx login` first.[/red]")
        raise typer.Exit(code=1)
    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {cfg.jwt}"
    return httpx.request(
        method, cfg.api_url.rstrip("/") + path, headers=headers, timeout=30.0, **kwargs
    )


def create(
    label: str = typer.Argument(..., help="Human-readable label for this key"),
) -> None:
    """Create a new API key. The plaintext is shown ONCE — save it immediately."""
    r = _authed("POST", "/admin/api-keys", json={"label": label})
    if r.status_code >= 400:
        console.print(f"[red]API error {r.status_code}:[/red] {r.text}")
        raise typer.Exit(code=2)
    data = r.json()
    console.print(f"[green]Created[/green] {data['label']} (id={data['id']})")
    console.print()
    console.print("[yellow]⚠ Save this key now — it's only shown once:[/yellow]")
    console.print(f"  [bold cyan]{data['key']}[/bold cyan]")


def list_cmd(json_output: bool = typer.Option(False, "--json")) -> None:
    """List all API keys (hashes only, never plaintext)."""
    r = _authed("GET", "/admin/api-keys")
    if r.status_code >= 400:
        console.print(f"[red]API error {r.status_code}:[/red] {r.text}")
        raise typer.Exit(code=2)
    rows = r.json()
    if json_output:
        console.print_json(data=rows)
        return
    if not rows:
        console.print("[yellow]No API keys yet. Run `scx key create <label>`.[/yellow]")
        return
    table = Table(title=f"{len(rows)} API keys", header_style="bold")
    table.add_column("id")
    table.add_column("label")
    table.add_column("prefix")
    table.add_column("created")
    table.add_column("last used")
    table.add_column("status")
    for row in rows:
        status_col = "[red]revoked[/red]" if row.get("revoked_at") else "[green]active[/green]"
        table.add_row(
            str(row["id"])[:8],
            row["label"],
            row["prefix"] + "…",
            str(row.get("created_at", "-"))[:19],
            str(row.get("last_used_at") or "never")[:19],
            status_col,
        )
    console.print(table)


def revoke(
    key_id: str = typer.Argument(..., help="Key UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Revoke an API key. Cannot be undone."""
    if not yes:
        typer.confirm(f"Revoke key {key_id[:8]}? Cannot be undone.", abort=True)
    r = _authed("DELETE", f"/admin/api-keys/{key_id}")
    if r.status_code >= 400:
        console.print(f"[red]API error {r.status_code}:[/red] {r.text}")
        raise typer.Exit(code=2)
    console.print("[green]Revoked.[/green]")
