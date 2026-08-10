import typer

from testoria_cli import config as config_module
from testoria_cli import output
from testoria_cli.client import TestoriaClient
from testoria_cli.config import Config, Credentials
from testoria_cli.errors import AuthError, handle_errors

app = typer.Typer(help="Interactive login for humans. CI should use TESTORIA_API_KEY.")


@app.command()
@handle_errors
def login(
    url: str = typer.Option(..., prompt=True, help="Testoria API base URL"),
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
) -> None:
    """Log in and store the resulting tokens in ~/.testoria/config.yaml."""
    import httpx

    base = url.rstrip("/")
    try:
        response = httpx.post(
            f"{base}/api/v1/auth/login",
            data={"username": username, "password": password},
            timeout=30.0,
        )
    except httpx.TransportError as exc:
        raise AuthError(f"Could not reach {base}: {exc}") from exc

    if response.status_code != 200:
        raise AuthError("Login failed — check the URL, username and password.")

    tokens = response.json()
    config_module.save(
        Config(
            url=base,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            username=username,
        )
    )
    output.console.print(f"[green]✓[/green] Logged in to {base} as {username}")


@app.command()
@handle_errors
def logout() -> None:
    """Forget the stored tokens."""
    config_module.clear()
    output.console.print("Logged out.")


@app.command()
@handle_errors
def status() -> None:
    """Show which credentials the CLI would use, without calling the API."""
    credentials: Credentials = config_module.resolve()
    if not credentials.url:
        output.err_console.print("Not configured. Run `testoria auth login`.")
        raise typer.Exit(1)
    if not credentials.api_key and not credentials.access_token:
        output.err_console.print(
            f"No credentials for {credentials.url}. "
            "Set TESTORIA_API_KEY or run `testoria auth login`."
        )
        raise typer.Exit(1)
    source = "TESTORIA_API_KEY" if credentials.api_key else "~/.testoria/config.yaml"
    output.console.print(f"{credentials.url} — via {credentials.via} (from {source})")


@handle_errors
def whoami(ctx: typer.Context) -> None:
    """Show what the current credentials are actually allowed to do."""
    client: TestoriaClient = ctx.obj["client"]
    me = client.get("/auth/principal")

    output.console.print(f"{me['username']} (id {me['user_id']}) via {me['via']}")
    effective, account = me["effective_role"], me["account_role"]
    if effective == account:
        output.console.print(f"  role: {effective}")
    else:
        # The gap matters: an API key owned by a lead is not a lead.
        output.console.print(
            f"  role: [bold]{effective}[/bold] "
            f"[dim](capped below the account role '{account}')[/dim]"
        )
    scope = me.get("project_id")
    output.console.print(
        f"  scope: project {scope}" if scope else "  scope: all projects"
    )
