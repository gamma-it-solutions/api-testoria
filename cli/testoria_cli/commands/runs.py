import typer

from testoria_cli import output
from testoria_cli.client import TestoriaClient
from testoria_cli.errors import handle_errors

app = typer.Typer(help="Manage test runs.")


@app.command("create")
@handle_errors
def create(
    ctx: typer.Context,
    project: int = typer.Option(..., "--project", help="Project id"),
    name: str = typer.Option(..., "--name", help="Run name"),
    suite: int | None = typer.Option(None, "--suite", help="Restrict to one suite"),
    output_format: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Create a test run and print its id."""
    client: TestoriaClient = ctx.obj["client"]
    body: dict[str, object] = {"name": name}
    if suite is not None:
        body["suite_id"] = suite
    run = client.post(f"/projects/{project}/test-runs", json=body)
    if output_format == "json":
        output.emit_json(run)
    else:
        output.console.print(f"Created run #{run['id']}: {run['name']}")


@app.command("list")
@handle_errors
def list_runs(
    ctx: typer.Context,
    project: int = typer.Option(..., "--project"),
    status: str | None = typer.Option(None, "--status"),
    output_format: str = typer.Option("text", "--output", "-o"),
) -> None:
    """List runs in a project."""
    client: TestoriaClient = ctx.obj["client"]
    params: dict[str, object] = {}
    if status:
        params["status"] = status
    payload = client.get(f"/projects/{project}/test-runs", params=params)
    items = payload.get("items", [])
    if output_format == "json":
        output.emit_json(payload)
        return
    output.table(
        f"Runs in project {project}",
        ["ID", "Name", "Status", "Created"],
        [
            [str(r["id"]), r["name"], r["status"], str(r.get("created_at", ""))[:19]]
            for r in items
        ],
    )


@app.command("show")
@handle_errors
def show(
    ctx: typer.Context,
    run: int = typer.Option(..., "--run"),
    output_format: str = typer.Option("text", "--output", "-o"),
) -> None:
    """Show one run and its progress."""
    client: TestoriaClient = ctx.obj["client"]
    payload = client.get(f"/test-runs/{run}")
    if output_format == "json":
        output.emit_json(payload)
        return
    output.console.print(f"#{payload['id']} {payload['name']} — {payload['status']}")
    progress = payload.get("progress") or {}
    if progress:
        output.console.print(
            "  "
            + "  ".join(
                f"{key} {value}"
                for key, value in progress.items()
                if key != "pass_rate"
            )
        )


@app.command("close")
@handle_errors
def close(
    ctx: typer.Context,
    run: int = typer.Option(..., "--run"),
) -> None:
    """Close a run."""
    client: TestoriaClient = ctx.obj["client"]
    client.post(f"/test-runs/{run}/close")
    output.console.print(f"Run #{run} closed.")
