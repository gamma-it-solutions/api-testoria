import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

_MAX_LISTED_UNMATCHED = 10


def emit_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    rendered = Table(*columns, title=title or None)
    for row in rows:
        rendered.add_row(*row)
    console.print(rendered)


def import_report(report: dict[str, Any], run_url: str | None = None) -> None:
    """Human summary of an import.

    Says how it matched, not just how many — a suite silently matching on
    `title` instead of `automation_id` is one rename away from reporting nothing.
    """
    run_id = report.get("run_id")
    submitted = report.get("submitted", 0)
    location = f"run #{run_id}" if run_id else "the run"
    console.print(f"[green]✓[/green] {submitted} results submitted to {location}")
    if run_url:
        console.print(f"  {run_url}")

    matched_by = report.get("matched_by") or {}
    if matched_by:
        detail = ", ".join(f"{count} by {rule}" for rule, count in matched_by.items())
        console.print(f"  matched: {detail}")

    counts = report.get("status_counts") or {}
    if counts:
        console.print(
            "  " + "  ".join(f"{status} {count}" for status, count in counts.items())
        )

    unmatched = report.get("unmatched", 0)
    if not unmatched:
        return

    console.print(
        f"\n[yellow]⚠[/yellow] {unmatched} test case(s) had no match in Testoria:"
    )
    listed = report.get("unmatched_cases") or []
    for case in listed[:_MAX_LISTED_UNMATCHED]:
        console.print(
            f"    {case.get('identifier')}  "
            f"([dim]{case.get('status')}, {case.get('reason')}[/dim])"
        )
    remaining = unmatched - min(len(listed), _MAX_LISTED_UNMATCHED)
    if remaining > 0:
        console.print(
            f"    ... {remaining} more — rerun with --output json for the full list"
        )
    console.print(
        "\n  Fix by setting `automation_id` on the missing cases. Find them with:\n"
        "    testoria case list --project <id> --unmapped"
    )
