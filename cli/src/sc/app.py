import typer
from rich.console import Console

from sc.commands import offers as offers_commands
from sc.config import Config

app = typer.Typer(help="serverless-core CLI", no_args_is_help=True)
console = Console()

offers_app = typer.Typer(help="Browse vast.ai offers through your control plane.")
offers_app.command("search")(offers_commands.search)
app.add_typer(offers_app, name="offers")


@app.command()
def login(
    api_url: str = typer.Option(
        None, "--api-url", help="Override stored API URL (e.g. https://sc-api.fly.dev)"
    ),
) -> None:
    """Store a Supabase JWT locally so `sc` can call /admin/* endpoints."""
    cfg = Config.load()
    if api_url:
        cfg.api_url = api_url
    console.print(f"API URL: [cyan]{cfg.api_url}[/cyan]")
    jwt = typer.prompt("Paste your Supabase access token", hide_input=True).strip()
    if not jwt:
        console.print("[red]No token entered, aborting.[/red]")
        raise typer.Exit(code=1)
    cfg.jwt = jwt
    cfg.save()
    from sc.config import CONFIG_PATH
    console.print(f"[green]Saved JWT to[/green] {CONFIG_PATH}")


@app.command()
def whoami() -> None:
    """Show currently stored API URL and whether a JWT is set."""
    cfg = Config.load()
    console.print(f"API URL: [cyan]{cfg.api_url}[/cyan]")
    console.print(f"JWT: {'[green]set[/green]' if cfg.jwt else '[red]missing[/red]'}")
