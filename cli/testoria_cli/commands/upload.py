import glob
import mimetypes
from pathlib import Path
from typing import Any

import typer

from testoria_cli import output
from testoria_cli.client import TestoriaClient
from testoria_cli.errors import UnmatchedError, UsageError, handle_errors
from testoria_cli.parsers.junit import count_testcases


def _resolve_run(
    client: TestoriaClient,
    run: int | None,
    project: int | None,
    create_run: str | None,
) -> tuple[int, bool]:
    """Return `(run_id, created)`.

    Exactly one of `--run` or `--project` + `--create-run` — guessing between
    "add to this run" and "start a new one" would be guessing about data.
    """
    if run is not None and (project is not None or create_run is not None):
        raise UsageError(
            "--run cannot be combined with --project/--create-run. "
            "Either upload into an existing run or create a new one."
        )
    if run is not None:
        return run, False
    if project is None or create_run is None:
        raise UsageError(
            "Specify --run <id>, or --project <id> together with --create-run <name>."
        )

    created = client.post(
        f"/projects/{project}/test-runs", json={"name": create_run}
    )
    run_id = int(created["id"])
    # Printed before the upload so a later failure is still traceable to a run.
    output.console.print(f"[dim]created run #{run_id}: {create_run}[/dim]")
    return run_id, True


def _attach(
    client: TestoriaClient, run_id: int, patterns: list[str]
) -> tuple[int, list[str]]:
    """Attach files whose stem matches a case's automation_id.

    Deterministic by construction: a pytest fixture that names artefacts after
    the node ID needs no extra configuration. Files that match nothing are
    reported, never silently dropped.
    """
    results = client.get(f"/test-runs/{run_id}/results")
    cases = client.get(f"/test-runs/{run_id}/cases")
    by_case = {r["test_case_id"]: r["id"] for r in results}

    automation_to_result: dict[str, int] = {}
    for case in cases.get("cases", []):
        automation_id = case.get("automation_id")
        result_id = by_case.get(case["id"])
        if automation_id and result_id:
            automation_to_result[automation_id] = result_id

    uploaded = 0
    skipped: list[str] = []
    for pattern in patterns:
        for match in sorted(glob.glob(pattern)):
            path = Path(match)
            if not path.is_file():
                continue
            result_id = automation_to_result.get(path.stem)
            if result_id is None:
                skipped.append(str(path))
                continue
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            client.upload(
                f"/test-results/{result_id}/attachments",
                path,
                path.read_bytes(),
                content_type=mime,
            )
            uploaded += 1
    return uploaded, skipped


@handle_errors
def upload(
    ctx: typer.Context,
    report: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True, help="JUnit XML or JSON report"
    ),
    run: int | None = typer.Option(None, "--run", help="Upload into this run id"),
    project: int | None = typer.Option(
        None, "--project", help="Project to create a run in"
    ),
    create_run: str | None = typer.Option(
        None, "--create-run", help="Name for a new run (requires --project)"
    ),
    close_on_finish: bool = typer.Option(
        False, "--close-on-finish", help="Close the run after a successful upload"
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Exit 2 if any test had no matching case"
    ),
    attach: list[str] = typer.Option(
        [], "--attach", help="Glob of files to attach; stem must equal an automation_id"
    ),
    fmt: str = typer.Option("auto", "--format", help="auto | junit | json"),
    output_format: str = typer.Option(
        "text", "--output", "-o", help="text | json"
    ),
) -> None:
    """Upload test results from a JUnit XML or JSON report."""
    client: TestoriaClient = ctx.obj["client"]
    base_url: str = ctx.obj["url"]

    # Fail before touching the network if the file is not a report at all.
    local_total = count_testcases(report)
    if local_total == 0:
        output.err_console.print(
            f"[yellow]⚠[/yellow] {report} contains no test cases — nothing to upload."
        )

    run_id, _created = _resolve_run(client, run, project, create_run)

    result: dict[str, Any] = client.upload(
        f"/test-runs/{run_id}/results/import",
        report,
        report.read_bytes(),
        data={"format": fmt},
        content_type="application/xml" if fmt != "json" else "application/json",
    )

    run_url = f"{base_url}/test-runs/{run_id}"
    if output_format == "json":
        output.emit_json(result)
    else:
        output.import_report(result, run_url=run_url)

    if attach:
        uploaded, skipped = _attach(client, run_id, attach)
        if output_format != "json":
            output.console.print(f"  {uploaded} attachment(s) uploaded")
            for path in skipped:
                output.err_console.print(
                    f"[yellow]⚠[/yellow] {path}: no case whose automation_id "
                    f"is '{Path(path).stem}' — not attached"
                )

    unmatched = int(result.get("unmatched", 0))
    if strict and unmatched:
        # Deliberately before the close: a run that failed its mapping check
        # should stay open for inspection.
        raise UnmatchedError(
            f"{unmatched} test case(s) had no match in Testoria (--strict)"
        )

    if close_on_finish:
        client.post(f"/test-runs/{run_id}/close")
        if output_format != "json":
            output.console.print(f"  run #{run_id} closed")
