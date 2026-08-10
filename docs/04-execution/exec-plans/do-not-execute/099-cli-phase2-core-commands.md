# Execution Plan: 015 — CLI Core Commands

> **SUPERSEDED (2026-08-10) by `050-be-cli-result-upload-and-api-keys`.**
> This plan predates `automation_id` (plan 024), the `in_progress`→`active`
> rename (plan 039) and `no_run` (plan 032), and it specifies a hand-maintained
> `case_map.json` that plan 050 removes in favour of `automation_id` matching.
> Kept for provenance only — do not execute.

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: HIGH
**Dependency**: 006-cli-foundation must be complete (config, api_client, exceptions in place)

---

## Goal

Implement all user-facing CLI commands: authentication (login/logout/status), project listing, test run management (create/list/close), and test result submission (submit/bulk-submit).

---

## Context

Plan 006 sets up the CLI plumbing — no commands are usable yet. This plan wires every command the `cli-tool.md` specifies for Phases 2 and 2.5. After this plan the CLI is fully functional for a tester recording results manually or via script.

---

## Scope

### In scope
- `testoria_cli/commands/auth.py` — `login`, `logout`, `status`
- `testoria_cli/commands/projects.py` — `project list`
- `testoria_cli/commands/runs.py` — `run create`, `run list`, `run close`
- `testoria_cli/commands/results.py` — `result submit`, `result bulk-submit`
- Wire all command groups into `testoria_cli/main.py`
- `tests/unit/test_commands_auth.py`, `test_commands_runs.py`, `test_commands_results.py`

### Out of scope
- JUnit XML parsing (plan 008)
- pytest plugin (plan 008)
- PyPI packaging (plan 009)

---

## Technical approach

### Command tree

```
testoria
├── login      --url --username --password
├── logout
├── status
├── project
│   └── list   [--output table|json]
├── run
│   ├── create --project-id INT --name TEXT [--suite-id INT]
│   ├── list   --project-id INT [--status open|closed|all]
│   └── close  --run-id INT
└── result
    ├── submit     --run-id INT --case-id INT --status passed|failed|blocked|skipped [--message TEXT] [--stack-trace TEXT]
    └── bulk-submit --run-id INT --file PATH  (JUnit XML or JSON)
```

### Auth commands (`commands/auth.py`)

```python
@app.command()
def login(
    url: str = typer.Option(..., prompt=True),
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
):
    client = TestoriaClient(base_url=url)
    tokens = client.login(username, password)   # POST /auth/token
    config = ConfigManager()
    config.save(TestoriaConfig(
        url=url,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    ))
    typer.echo("Logged in successfully.")

@app.command()
def logout():
    ConfigManager().clear()
    typer.echo("Logged out.")

@app.command()
def status():
    cfg = ConfigManager().load()
    if not cfg:
        typer.echo("Not logged in.")
        raise typer.Exit(1)
    typer.echo(f"Logged in to {cfg.url}")
```

### Project commands (`commands/projects.py`)

```python
@app.command("list")
def project_list(output: str = typer.Option("table", "--output", "-o")):
    client = get_client()   # helper: loads config, builds TestoriaClient
    projects = client.get("/projects")
    if output == "json":
        typer.echo(json.dumps(projects, indent=2))
    else:
        # Rich table
        table = Table("ID", "Name", "Description")
        for p in projects:
            table.add_row(str(p["id"]), p["name"], p.get("description", ""))
        Console().print(table)
```

### Run commands (`commands/runs.py`)

```python
@app.command("create")
def run_create(project_id: int, name: str, suite_id: Optional[int] = None):
    client = get_client()
    body = {"name": name, "suite_id": suite_id}
    run = client.post(f"/projects/{project_id}/test-runs", json=body)
    typer.echo(f"Created run #{run['id']}: {run['name']}")

@app.command("list")
def run_list(project_id: int, status: str = "open"):
    client = get_client()
    params = {} if status == "all" else {"status": status}
    runs = client.get(f"/projects/{project_id}/test-runs", params=params)
    # Rich table: ID, Name, Status, Created

@app.command("close")
def run_close(run_id: int):
    client = get_client()
    client.put(f"/test-runs/{run_id}", json={"status": "closed"})
    typer.echo(f"Run #{run_id} closed.")
```

### Result commands (`commands/results.py`)

```python
VALID_STATUSES = ["passed", "failed", "blocked", "skipped"]

@app.command("submit")
def result_submit(
    run_id: int,
    case_id: int,
    status: str = typer.Option(..., help=f"One of: {', '.join(VALID_STATUSES)}"),
    message: Optional[str] = None,
    stack_trace: Optional[str] = None,
):
    if status not in VALID_STATUSES:
        typer.echo(f"Invalid status. Choose from: {', '.join(VALID_STATUSES)}", err=True)
        raise typer.Exit(1)
    client = get_client()
    body = {"test_case_id": case_id, "status": status, "message": message, "stack_trace": stack_trace}
    result = client.post(f"/test-runs/{run_id}/results", json=body)
    typer.echo(f"Result recorded: case #{case_id} → {status} (result #{result['id']})")

@app.command("bulk-submit")
def result_bulk_submit(run_id: int, file: Path = typer.Argument(...)):
    """Submit results from a JSON file (list of {case_id, status, message?, stack_trace?})."""
    if not file.exists():
        typer.echo(f"File not found: {file}", err=True)
        raise typer.Exit(1)
    with open(file) as f:
        results = json.load(f)
    client = get_client()
    ok = fail = 0
    for r in results:
        try:
            client.post(f"/test-runs/{run_id}/results", json=r)
            ok += 1
        except Exception as e:
            typer.echo(f"  FAIL case #{r.get('case_id')}: {e}", err=True)
            fail += 1
    typer.echo(f"Done: {ok} submitted, {fail} failed.")
```

### `get_client()` helper

Lives in `testoria_cli/utils.py`:

```python
def get_client() -> TestoriaClient:
    cfg = ConfigManager().load()
    if not cfg or not cfg.access_token:
        typer.echo("Not logged in. Run `testoria login` first.", err=True)
        raise typer.Exit(1)
    return TestoriaClient(base_url=cfg.url, access_token=cfg.access_token)
```

### Wiring into `main.py`

```python
from testoria_cli.commands import auth, projects, runs, results

app.add_typer(projects.app, name="project")
app.add_typer(runs.app, name="run")
app.add_typer(results.app, name="result")
# login / logout / status registered directly on app (not sub-typer)
```

---

## Tasks

### Implementation
- [ ] Create `testoria_cli/commands/__init__.py`
- [ ] Write `testoria_cli/commands/auth.py` — `login`, `logout`, `status`
- [ ] Write `testoria_cli/utils.py` — `get_client()` helper
- [ ] Write `testoria_cli/commands/projects.py` — `project list`
- [ ] Write `testoria_cli/commands/runs.py` — `run create`, `run list`, `run close`
- [ ] Write `testoria_cli/commands/results.py` — `result submit`, `result bulk-submit`
- [ ] Wire all command groups in `testoria_cli/main.py`

### Tests
- [ ] `tests/unit/test_commands_auth.py` — login saves config; logout clears config; status prints URL when logged in; status exits 1 when not logged in
- [ ] `tests/unit/test_commands_runs.py` — create calls POST with correct body; close calls PUT status=closed; list calls GET with status param
- [ ] `tests/unit/test_commands_results.py` — submit calls POST with all fields; invalid status exits 1; bulk-submit iterates file entries; missing file exits 1

### Quality check
- [ ] `pytest tests/unit/` passes
- [ ] `ruff check testoria_cli tests` clean
- [ ] `mypy testoria_cli` clean
- [ ] Manual smoke test: `testoria login`, `testoria project list`, `testoria result submit --run-id 1 --case-id 1 --status passed`

### Docs
- [ ] Update `api/cli-tool.md` Phase 2 success criteria checkboxes
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `testoria login` prompts for URL/user/pass and writes `~/.testoria/config.yaml`
- [ ] `testoria logout` clears config and confirms
- [ ] `testoria status` prints logged-in URL or exits 1
- [ ] `testoria project list` renders a Rich table of projects
- [ ] `testoria run create --project-id 1 --name "Sprint 42"` creates a run and prints its ID
- [ ] `testoria run list --project-id 1` lists open runs
- [ ] `testoria run close --run-id 7` closes the run
- [ ] `testoria result submit --run-id 1 --case-id 5 --status failed --message "NPE on line 42"` submits and prints confirmation
- [ ] `testoria result bulk-submit --run-id 1 results.json` submits all entries and prints ok/fail counts
- [ ] Not-logged-in state prints a clear error and exits 1 for every authenticated command
- [ ] Unit tests pass
