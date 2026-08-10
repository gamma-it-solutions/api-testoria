# Execution Plan: 017 — CLI Integrations (JUnit XML + pytest Plugin)

> **SUPERSEDED (2026-08-10) by `050-be-cli-result-upload-and-api-keys`.**
> This plan predates `automation_id` (plan 024), the `in_progress`→`active`
> rename (plan 039) and `no_run` (plan 032), and it specifies a hand-maintained
> `case_map.json` that plan 050 removes in favour of `automation_id` matching.
> Kept for provenance only — do not execute.

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: MEDIUM
**Dependency**: 007-cli-core-commands must be complete

---

## Goal

Add JUnit XML import support to `result bulk-submit` and implement the `testoria-pytest` pytest plugin so CI/CD pipelines can push results automatically.

---

## Context

Plan 007 adds `result bulk-submit` with JSON-only input. This plan adds a JUnit XML parser so automation frameworks can emit standard XML and pipe it straight into Testoria. The pytest plugin goes further — it hooks into pytest collection/reporting to submit results in real time with zero configuration in the test command.

---

## Scope

### In scope
- `testoria_cli/parsers/junit.py` — parse JUnit XML into `list[ResultPayload]`
- Update `result bulk-submit` to auto-detect JUnit XML vs JSON by file extension / content
- `testoria_cli/plugin.py` — pytest plugin (`testoria_pytest`)
- Plugin config: `testoria_run_id`, `testoria_case_map` (pytest nodeid → case_id) from `pytest.ini` / `pyproject.toml`
- `tests/unit/test_junit_parser.py`
- `tests/unit/test_plugin.py`

### Out of scope
- TestNG / NUnit XML formats (future)
- Automatic test case creation from XML (future)
- pytest-xdist compatibility (future)

---

## Technical approach

### JUnit XML format

```xml
<testsuite name="..." tests="3" failures="1" errors="0">
  <testcase classname="test_login" name="test_valid_credentials" time="0.12">
    <!-- pass: no child elements -->
  </testcase>
  <testcase classname="test_login" name="test_bad_password" time="0.08">
    <failure message="AssertionError: expected 401">stack trace here</failure>
  </testcase>
  <testcase classname="test_login" name="test_locked_account" time="0.05">
    <skipped/>
  </testcase>
</testsuite>
```

### Parser (`parsers/junit.py`)

```python
from dataclasses import dataclass
from xml.etree import ElementTree as ET

@dataclass
class ResultPayload:
    case_id: int
    status: str          # passed | failed | skipped | blocked
    message: str | None
    stack_trace: str | None

def parse_junit(xml_path: Path, case_map: dict[str, int]) -> list[ResultPayload]:
    """
    case_map: {"classname.testname" -> case_id}
    XML elements with no <failure>/<error>/<skipped> = passed
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    suites = root.findall("testsuite") or [root]

    results = []
    for suite in suites:
        for tc in suite.findall("testcase"):
            key = f"{tc.get('classname')}.{tc.get('name')}"
            case_id = case_map.get(key)
            if case_id is None:
                continue   # unmapped test case — skip silently

            failure = tc.find("failure") or tc.find("error")
            skipped = tc.find("skipped")

            if failure is not None:
                status = "failed"
                message = failure.get("message")
                stack_trace = failure.text
            elif skipped is not None:
                status = "skipped"
                message = skipped.get("message")
                stack_trace = None
            else:
                status = "passed"
                message = None
                stack_trace = None

            results.append(ResultPayload(case_id, status, message, stack_trace))

    return results
```

### Updated `result bulk-submit`

```python
@app.command("bulk-submit")
def result_bulk_submit(
    run_id: int,
    file: Path = typer.Argument(...),
    case_map: Optional[Path] = typer.Option(None, help="JSON mapping classname.testname → case_id (JUnit only)"),
):
    if not file.exists():
        typer.echo(f"File not found: {file}", err=True)
        raise typer.Exit(1)

    if file.suffix in (".xml",):
        if not case_map:
            typer.echo("--case-map required for JUnit XML input", err=True)
            raise typer.Exit(1)
        mapping = json.loads(case_map.read_text())
        payloads = parse_junit(file, mapping)
        entries = [asdict(p) for p in payloads]
    else:
        entries = json.loads(file.read_text())

    client = get_client()
    ok = fail = 0
    for entry in entries:
        try:
            client.post(f"/test-runs/{run_id}/results", json=entry)
            ok += 1
        except Exception as e:
            typer.echo(f"  FAIL case #{entry.get('case_id')}: {e}", err=True)
            fail += 1
    typer.echo(f"Done: {ok} submitted, {fail} failed.")
```

### pytest plugin (`plugin.py`)

Entry point registered in `pyproject.toml`:

```toml
[project.entry-points."pytest11"]
testoria = "testoria_cli.plugin"
```

Plugin implementation:

```python
import pytest
from testoria_cli.utils import get_client
from testoria_cli.exceptions import TestoriaCLIError

def pytest_addoption(parser):
    group = parser.getgroup("testoria")
    group.addoption("--testoria-run-id", type=int, help="Testoria run ID to push results to")
    group.addoption("--testoria-case-map", help="Path to JSON file mapping nodeid → case_id")

def pytest_configure(config):
    run_id = config.getoption("--testoria-run-id", default=None)
    if run_id:
        map_path = config.getoption("--testoria-case-map")
        case_map = json.loads(Path(map_path).read_text()) if map_path else {}
        config._testoria_run_id = run_id
        config._testoria_case_map = case_map
        config._testoria_client = get_client()

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when != "call":
        return

    config = item.config
    run_id = getattr(config, "_testoria_run_id", None)
    if not run_id:
        return

    case_map = getattr(config, "_testoria_case_map", {})
    case_id = case_map.get(item.nodeid)
    if not case_id:
        return

    status_map = {"passed": "passed", "failed": "failed", "skipped": "skipped"}
    status = status_map.get(report.outcome, "blocked")
    message = str(report.longrepr) if report.failed else None

    try:
        config._testoria_client.post(
            f"/test-runs/{run_id}/results",
            json={"test_case_id": case_id, "status": status, "message": message},
        )
    except TestoriaCLIError as e:
        item.warn(pytest.PytestWarning(f"Testoria submit failed: {e}"))
```

Usage:

```bash
pytest --testoria-run-id=42 --testoria-case-map=case_map.json
```

---

## Tasks

### Implementation
- [ ] Create `testoria_cli/parsers/__init__.py`
- [ ] Write `testoria_cli/parsers/junit.py` — `parse_junit(xml_path, case_map)` returning `list[ResultPayload]`
- [ ] Update `testoria_cli/commands/results.py` `bulk-submit` to detect `.xml` and invoke parser
- [ ] Write `testoria_cli/plugin.py` — pytest plugin with `pytest_addoption`, `pytest_configure`, `pytest_runtest_makereport`
- [ ] Register plugin entry point in `pyproject.toml` under `[project.entry-points."pytest11"]`

### Tests
- [ ] `tests/unit/test_junit_parser.py`:
  - Parse XML with pass / fail / skipped cases
  - Unmapped test cases are silently skipped
  - `<error>` element maps to `failed`
  - Missing `case_map` file raises clear error
- [ ] `tests/unit/test_plugin.py`:
  - Plugin skips submit when `--testoria-run-id` not provided
  - Plugin calls API with correct payload on test failure
  - Submit failure emits `PytestWarning`, does not fail the test

### Quality check
- [ ] `pytest tests/unit/` passes
- [ ] `ruff check testoria_cli tests` clean
- [ ] `mypy testoria_cli` clean
- [ ] Manual test: run pytest with `--testoria-run-id` and verify results appear in Testoria UI

### Docs
- [ ] Update `api/cli-tool.md` Phase 3 success criteria checkboxes
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `result bulk-submit --run-id 1 results.xml --case-map map.json` parses JUnit XML and submits results
- [ ] Unmapped test cases are silently skipped (no error)
- [ ] `pytest --testoria-run-id=42 --testoria-case-map=map.json` submits results in real time
- [ ] Testoria submit failure in pytest emits a warning but does not fail the test suite
- [ ] Unit tests for parser and plugin pass
- [ ] `pip install -e .[dev]` in `cli/` exposes `testoria` entry point in pytest plugins list
