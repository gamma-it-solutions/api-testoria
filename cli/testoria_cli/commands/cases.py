import typer

from testoria_cli import output
from testoria_cli.client import TestoriaClient
from testoria_cli.errors import handle_errors

app = typer.Typer(help="Inspect test cases.")


@app.command("list")
@handle_errors
def list_cases(
    ctx: typer.Context,
    project: int = typer.Option(..., "--project"),
    unmapped: bool = typer.Option(
        False, "--unmapped", help="Only cases with no automation_id"
    ),
    page_size: int = typer.Option(100, "--page-size", min=1, max=100),
    output_format: str = typer.Option("text", "--output", "-o"),
) -> None:
    """List test cases — `--unmapped` shows the ones no automated run can link to."""
    client: TestoriaClient = ctx.obj["client"]
    params: dict[str, object] = {"page_size": page_size}
    if unmapped:
        params["has_automation_id"] = "false"
    payload = client.get(f"/projects/{project}/test-cases", params=params)

    if output_format == "json":
        output.emit_json(payload)
        return

    items = payload.get("items", [])
    if not items:
        message = (
            "Every test case has an automation_id."
            if unmapped
            else "No test cases."
        )
        output.console.print(message)
        return

    output.table(
        "Unmapped test cases" if unmapped else "Test cases",
        ["ID", "Title", "automation_id"],
        [
            [str(c["id"]), c["title"], c.get("automation_id") or "[dim]—[/dim]"]
            for c in items
        ],
    )
    total = payload.get("total", len(items))
    if total > len(items):
        output.console.print(f"[dim]showing {len(items)} of {total}[/dim]")
