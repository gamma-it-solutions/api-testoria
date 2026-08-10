"""Manage API keys.

Every command here uses the **JWT** client (`ctx.obj["jwt_client"]`), never the
API key: `/api-keys` is JWT-only server-side, because a key that could mint or
revoke keys would turn a leak from a revocable credential into a persistent
foothold.
"""

from typing import Any

import typer

from testoria_cli import output
from testoria_cli.client import TestoriaClient
from testoria_cli.errors import UsageError, handle_errors

app = typer.Typer(help="Create and revoke API keys for CI.")

# Server-side the cap is API_KEY_MAX_ROLE (default `tester`); this is only a
# fast local check so an obvious typo does not cost a round trip.
_ROLES = ("read_only", "tester", "lead", "admin")


@app.command("create")
@handle_errors
def create(
    ctx: typer.Context,
    name: str = typer.Option(
        ..., "--name", help="Label, e.g. 'github-actions-nightly'"
    ),
    project: int | None = typer.Option(
        None, "--project", help="Scope the key to one project"
    ),
    role: str = typer.Option("tester", "--role", help=f"One of: {', '.join(_ROLES)}"),
    expires_in_days: int | None = typer.Option(
        None, "--expires-in-days", help="Defaults to the server's TTL (90 days)"
    ),
    never_expires: bool = typer.Option(
        False, "--never-expires", help="Opt out of expiry — prefer a TTL"
    ),
    for_user: int | None = typer.Option(
        None, "--for-user", help="Mint on behalf of another user (lead/admin)"
    ),
    output_format: str = typer.Option("text", "--output", "-o", help="text | json"),
) -> None:
    """Create an API key. The secret is shown once and never again."""
    if role not in _ROLES:
        raise UsageError(f"Unknown role '{role}'. One of: {', '.join(_ROLES)}")
    if never_expires and expires_in_days is not None:
        raise UsageError("--never-expires and --expires-in-days are mutually exclusive")

    client: TestoriaClient = ctx.obj["jwt_client"]
    body: dict[str, object] = {"name": name, "role": role}
    if project is not None:
        body["project_id"] = project
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    if never_expires:
        body["never_expires"] = True
    if for_user is not None:
        body["user_id"] = for_user

    created = client.post("/api-keys", json=body)

    if output_format == "json":
        # Scriptable: `testoria key create … -o json | jq -r .key`
        output.emit_json(created)
        return

    output.console.print(
        f"[green]✓[/green] Created API key [bold]{created['name']}[/bold]"
    )
    output.console.print(f"\n  [bold]{created['key']}[/bold]\n")
    output.console.print(
        "  [yellow]This is the only time the key is shown.[/yellow] "
        "Store it in your CI secrets now."
    )
    scope = created.get("project_id")
    output.console.print(
        f"  role {created['role']} · "
        f"scope {'project ' + str(scope) if scope else 'all projects'} · "
        f"expires {_fmt(created.get('expires_at')) or 'never'}"
    )
    output.console.print(
        "\n  Use it with:\n"
        f"    export TESTORIA_URL={ctx.obj['url']}\n"
        f"    export TESTORIA_API_KEY={created['key']}"
    )


@app.command("list")
@handle_errors
def list_keys(
    ctx: typer.Context,
    user: int | None = typer.Option(
        None, "--user", help="Another user's keys (admin only)"
    ),
    include_revoked: bool = typer.Option(False, "--include-revoked"),
    output_format: str = typer.Option("text", "--output", "-o"),
) -> None:
    """List API keys. Secrets are never shown — only the prefix."""
    client: TestoriaClient = ctx.obj["jwt_client"]
    params: dict[str, object] = {}
    if user is not None:
        params["user_id"] = user
    if include_revoked:
        params["include_revoked"] = "true"
    keys = client.get("/api-keys", params=params)

    if output_format == "json":
        output.emit_json(keys)
        return
    if not keys:
        output.console.print(
            "No API keys. Create one with `testoria key create --name <label>`."
        )
        return

    # Six columns, dates trimmed to the day: eight columns with full timestamps
    # overflowed an 80-column terminal and rich truncated the *name* — the one
    # field a person actually scans for.
    output.table(
        "API keys",
        ["ID", "Name", "Prefix", "Access", "Last used", "Status"],
        [
            [
                str(k["id"]),
                k["name"],
                k["key_prefix"],
                _access(k),
                _fmt(k.get("last_used_at")) or "never",
                _status(k),
            ]
            for k in keys
        ],
    )


@app.command("revoke")
@handle_errors
def revoke(
    ctx: typer.Context,
    key_id: int = typer.Option(..., "--id", help="Key id from `testoria key list`"),
) -> None:
    """Revoke a key. It stops working on the next request."""
    client: TestoriaClient = ctx.obj["jwt_client"]
    client.delete(f"/api-keys/{key_id}")
    output.console.print(f"[green]✓[/green] Key #{key_id} revoked.")


def _access(key: dict[str, Any]) -> str:
    scope = key.get("project_id")
    return f"{key['role']} · {'project ' + str(scope) if scope else 'all'}"


def _status(key: dict[str, Any]) -> str:
    if key.get("revoked_at"):
        return "[red]revoked[/red]"
    expires = _fmt(key.get("expires_at"))
    return f"[green]live[/green] to {expires}" if expires else "[green]live[/green]"


def _fmt(value: str | None) -> str | None:
    """Trim an ISO timestamp to the day — the table has no room for more."""
    return value[:10] if value else None
