# Execution Plan: 016 — CLI Report Command & Utilities

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: LOW
**Dependency**: 007-cli-core-commands must be complete

---

## Goal

Implement the `report generate` CLI command that fetches dashboard metrics from the backend reporting endpoints and renders them in the terminal or exports to a file. Also implement the `testoria_cli/utils/` package (formatters, validators, helpers) that is referenced by other command modules.

---

## Context

The CLI project structure in `cli-tool.md` includes `commands/reports.py` and the full `utils/` package (formatters, validators, helpers). Plan 007 wired auth/project/run/result commands but omitted the report command and utils modules. Both are needed before the CLI package can be considered complete.

The backend Phase 5 (Reporting & Analytics) is already complete, so all report endpoints (`GET /projects/{id}/dashboard`, `GET /projects/{id}/metrics`) are available.

---

## Scope

### In scope
- `testoria_cli/utils/__init__.py`
- `testoria_cli/utils/formatters.py` — Rich table/JSON/YAML output helpers
- `testoria_cli/utils/validators.py` — input validation helpers (status values, date ranges)
- `testoria_cli/utils/helpers.py` — shared helpers (resolve project key → id, date formatting)
- `testoria_cli/commands/reports.py` — `report generate` command
- Wire `reports.app` into `testoria_cli/main.py`
- `tests/unit/test_commands_reports.py`
- `tests/unit/test_utils.py`

### Out of scope
- PDF/Excel export from CLI (backend generates these; CLI can open the URL)
- Email report delivery

---

## Technical approach

### `utils/formatters.py`

```python
from rich.table import Table
from rich.console import Console
import json

console = Console()

def print_table(title: str, columns: list[str], rows: list[list]) -> None:
    table = Table(title=title)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)

def print_json(data: dict | list) -> None:
    console.print_json(json.dumps(data, default=str))

def print_key_value(data: dict) -> None:
    for key, value in data.items():
        console.print(f"[bold]{key}[/bold]: {value}")
```

### `utils/validators.py`

```python
VALID_STATUSES = {"passed", "failed", "blocked", "skipped"}
VALID_OUTPUT_FORMATS = {"table", "json"}

def validate_status(status: str) -> str:
    normalized = status.lower()
    if normalized not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Choose from: {', '.join(sorted(VALID_STATUSES))}")
    return normalized

def validate_output_format(fmt: str) -> str:
    if fmt not in VALID_OUTPUT_FORMATS:
        raise ValueError(f"Invalid output format '{fmt}'. Choose from: {', '.join(VALID_OUTPUT_FORMATS)}")
    return fmt
```

### `utils/helpers.py`

```python
from testoria_cli.api_client import TestoriaClient
from testoria_cli.exceptions import APIError
import typer

def resolve_project_id(client: TestoriaClient, project_key: str) -> int:
    """Resolve a project key (e.g. 'PROJ1') to its numeric ID."""
    projects = client.get("/projects")
    match = next((p for p in projects if p["key"] == project_key), None)
    if not match:
        typer.echo(f"Project '{project_key}' not found.", err=True)
        raise typer.Exit(1)
    return match["id"]

def format_date(dt_str: str | None) -> str:
    if not dt_str:
        return "—"
    return dt_str[:10]   # ISO 8601 → YYYY-MM-DD
```

### `commands/reports.py`

```python
import typer
from typing import Optional
from testoria_cli.utils.helpers import get_client, resolve_project_id
from testoria_cli.utils.formatters import print_table, print_json, print_key_value

app = typer.Typer()

@app.command("generate")
def report_generate(
    project_key: str = typer.Option(..., "--project", "-p", help="Project key"),
    output: str = typer.Option("table", "--output", "-o", help="table|json"),
):
    """Show dashboard metrics for a project."""
    client = get_client()
    project_id = resolve_project_id(client, project_key)
    data = client.get(f"/projects/{project_id}/dashboard")

    if output == "json":
        print_json(data)
    else:
        # Top-level metrics as key-value
        summary = {
            "Total test cases":  data.get("total_test_cases", 0),
            "Total test runs":   data.get("total_test_runs", 0),
            "Pass rate":         f"{data.get('pass_rate', 0):.1f}%",
            "Active runs":       data.get("active_runs", 0),
        }
        print_key_value(summary)

        # Recent run breakdown
        recent = data.get("recent_runs", [])
        if recent:
            print_table(
                "Recent Runs",
                ["Run", "Status", "Passed", "Failed", "Blocked"],
                [[r["name"], r["status"], r.get("passed", 0), r.get("failed", 0), r.get("blocked", 0)]
                 for r in recent],
            )
```

### Wire into `main.py`

```python
from testoria_cli.commands import auth, projects, runs, results, reports

app.add_typer(reports.app, name="report", help="Report generation")
```

---

## Tasks

### Implementation
- [ ] Create `testoria_cli/utils/__init__.py`
- [ ] Write `testoria_cli/utils/formatters.py` — `print_table`, `print_json`, `print_key_value`
- [ ] Write `testoria_cli/utils/validators.py` — `validate_status`, `validate_output_format`
- [ ] Write `testoria_cli/utils/helpers.py` — `resolve_project_id`, `format_date`
- [ ] Write `testoria_cli/commands/reports.py` — `report generate` command
- [ ] Wire `reports.app` into `testoria_cli/main.py`
- [ ] Refactor existing commands (007) to use shared utils where applicable (e.g., `validate_status` in `results.py`, `resolve_project_id` in `runs.py`)

### Tests
- [ ] `tests/unit/test_utils.py`:
  - `validate_status` raises `ValueError` on invalid input
  - `validate_output_format` raises on unknown format
  - `resolve_project_id` returns correct ID from list; raises Exit(1) when not found
- [ ] `tests/unit/test_commands_reports.py`:
  - `report generate` calls `/projects/{id}/dashboard`
  - JSON output flag prints valid JSON
  - Unknown project key exits 1

### Quality check
- [ ] `pytest tests/unit/` passes
- [ ] `ruff check testoria_cli tests` clean
- [ ] `mypy testoria_cli` clean

### Docs
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `testoria report generate --project PROJ1` prints a Rich dashboard table
- [ ] `testoria report generate --project PROJ1 --output json` prints raw JSON
- [ ] `validate_status("PASSED")` normalizes to `"passed"` and raises on unknown values
- [ ] `resolve_project_id` resolves key to ID and exits cleanly on unknown key
- [ ] Unit tests pass
