# Execution Plan: 019 — CLI Phase 5: Integration Tests, CI Examples & Documentation

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: MEDIUM
**Dependency**: Plans 006–010 must be complete (all CLI code in place)

---

## Goal

Complete CLI Phase 5: write integration tests that verify the CLI against a real running backend, produce CI integration example files (GitHub Actions, GitLab CI, Jenkins), and write the troubleshooting guide and per-command reference docs.

---

## Context

Plans 006–010 produce all CLI code. Each plan includes unit tests (mocked). This plan adds the integration test layer (real backend, real HTTP) and the example files and docs that CI/CD users need to adopt the tool in their pipelines. Without this phase the CLI is functional but unsupported.

---

## Scope

### In scope
- `tests/integration/test_auth_flow.py` — real login/logout/status against backend
- `tests/integration/test_result_submission.py` — submit result, bulk-submit (JSON and JUnit XML) against backend
- `cli/examples/github_actions.yml` — GitHub Actions workflow that runs pytest + submits to Testoria
- `cli/examples/gitlab_ci.yml` — GitLab CI equivalent
- `cli/examples/jenkins_pipeline.groovy` — Jenkins declarative pipeline equivalent
- `cli/docs/INSTALLATION.md`
- `cli/docs/USAGE.md`
- `cli/docs/CI_INTEGRATION.md`
- `cli/docs/TROUBLESHOOTING.md`

### Out of scope
- Backend changes of any kind
- New CLI commands

---

## Technical approach

### Integration test setup

Integration tests require a real Testoria backend. They are gated by an environment variable:

```python
# tests/conftest.py
import os
import pytest

INTEGRATION = os.getenv("TESTORIA_INTEGRATION_URL")

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as requiring a live backend")

skip_if_no_backend = pytest.mark.skipif(
    not INTEGRATION,
    reason="Set TESTORIA_INTEGRATION_URL to run integration tests"
)
```

Run with:
```bash
TESTORIA_INTEGRATION_URL=http://localhost:8000 pytest tests/integration/ -m integration
```

### `tests/integration/test_auth_flow.py`

```python
@pytest.mark.integration
def test_login_logout_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTORIA_CONFIG_DIR", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, [
        "login",
        "--url", INTEGRATION,
        "--username", "test_admin",
        "--password", "testpassword",
    ])
    assert result.exit_code == 0
    assert "Logged in" in result.output

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert INTEGRATION in result.output

    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1   # not logged in

@pytest.mark.integration
def test_unauthenticated_command_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTORIA_CONFIG_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["project", "list"])
    assert result.exit_code == 1
    assert "login" in result.output.lower()
```

### `tests/integration/test_result_submission.py`

```python
@pytest.mark.integration
def test_submit_single_result(logged_in_client, test_run_id, test_case_id):
    runner = CliRunner()
    result = runner.invoke(app, [
        "result", "submit",
        "--run-id", str(test_run_id),
        "--case-id", str(test_case_id),
        "--status", "passed",
    ])
    assert result.exit_code == 0
    assert "recorded" in result.output

@pytest.mark.integration
def test_bulk_submit_json(logged_in_client, test_run_id, test_case_id, tmp_path):
    payload = tmp_path / "results.json"
    payload.write_text(json.dumps([{"test_case_id": test_case_id, "status": "passed"}]))
    runner = CliRunner()
    result = runner.invoke(app, [
        "result", "bulk-submit",
        "--run-id", str(test_run_id),
        str(payload),
    ])
    assert result.exit_code == 0
    assert "1 submitted" in result.output

@pytest.mark.integration
def test_bulk_submit_junit_xml(logged_in_client, test_run_id, test_case_id, tmp_path):
    xml = tmp_path / "results.xml"
    xml.write_text(f"""<testsuite>
        <testcase classname="MyTest" name="test_foo"/>
    </testsuite>""")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"MyTest.test_foo": test_case_id}))
    runner = CliRunner()
    result = runner.invoke(app, [
        "result", "bulk-submit",
        "--run-id", str(test_run_id),
        str(xml),
        "--case-map", str(mapping),
    ])
    assert result.exit_code == 0
```

### `examples/github_actions.yml`

```yaml
name: Tests with Testoria

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install testoria-cli pytest

      # Create a test run for this CI build
      - name: Create Testoria run
        id: testoria
        run: |
          RUN_ID=$(testoria run create \
            --project-id ${{ vars.TESTORIA_PROJECT_ID }} \
            --name "CI - ${{ github.ref_name }} - ${{ github.run_number }}" \
            | grep "^Created run #" | grep -oP '\d+')
          echo "run_id=$RUN_ID" >> $GITHUB_OUTPUT
        env:
          TESTORIA_URL: ${{ secrets.TESTORIA_URL }}
          TESTORIA_TOKEN: ${{ secrets.TESTORIA_TOKEN }}

      # Run tests and push results to Testoria in real time
      - name: Run tests
        run: |
          pytest \
            --testoria-run-id=${{ steps.testoria.outputs.run_id }} \
            --testoria-case-map=testoria_map.json
        env:
          TESTORIA_URL: ${{ secrets.TESTORIA_URL }}
          TESTORIA_TOKEN: ${{ secrets.TESTORIA_TOKEN }}

      # Close the run when done
      - name: Close Testoria run
        if: always()
        run: testoria run close --run-id ${{ steps.testoria.outputs.run_id }}
        env:
          TESTORIA_URL: ${{ secrets.TESTORIA_URL }}
          TESTORIA_TOKEN: ${{ secrets.TESTORIA_TOKEN }}
```

### `examples/gitlab_ci.yml`

```yaml
stages: [test]

test:
  stage: test
  image: python:3.11
  before_script:
    - pip install testoria-cli pytest
    - testoria login --url $TESTORIA_URL --username $TESTORIA_USER --password $TESTORIA_PASS
  script:
    - |
      RUN_ID=$(testoria run create --project-id $TESTORIA_PROJECT_ID --name "CI - $CI_COMMIT_REF_NAME - $CI_PIPELINE_ID" | grep -oP '(?<=run #)\d+')
      pytest --testoria-run-id=$RUN_ID --testoria-case-map=testoria_map.json || true
      testoria run close --run-id $RUN_ID
```

### `examples/jenkins_pipeline.groovy`

```groovy
pipeline {
    agent any
    environment {
        TESTORIA_URL = credentials('testoria-url')
        TESTORIA_TOKEN = credentials('testoria-token')
    }
    stages {
        stage('Install') {
            steps { sh 'pip install testoria-cli pytest' }
        }
        stage('Test') {
            steps {
                script {
                    def runId = sh(
                        script: "testoria run create --project-id ${TESTORIA_PROJECT_ID} --name 'CI - ${env.BUILD_NUMBER}' | grep -oP '(?<=run #)\\d+'",
                        returnStdout: true
                    ).trim()
                    sh "pytest --testoria-run-id=${runId} --testoria-case-map=testoria_map.json || true"
                    sh "testoria run close --run-id ${runId}"
                }
            }
        }
    }
}
```

### `docs/TROUBLESHOOTING.md` topics

- "Not logged in" error after token expiry (re-run `testoria login`)
- SSL certificate errors (`--no-verify` flag or install cert)
- `testoria_map.json` format reference
- Bulk submit: what happens when a case_id mapping is missing
- pytest plugin not loading (check `pip show testoria-cli` entry points)
- Proxy configuration via `HTTPS_PROXY` env var

---

## Tasks

### Integration tests
- [ ] Add `skip_if_no_backend` fixture and `integration` mark to `tests/conftest.py`
- [ ] Write `tests/integration/test_auth_flow.py` — login/logout/status cycle; unauthenticated commands fail
- [ ] Write `tests/integration/test_result_submission.py` — single submit, bulk JSON, bulk JUnit XML

### CI examples
- [ ] Write `cli/examples/github_actions.yml`
- [ ] Write `cli/examples/gitlab_ci.yml`
- [ ] Write `cli/examples/jenkins_pipeline.groovy`

### Documentation
- [ ] Write `cli/docs/INSTALLATION.md` — pip install, venv setup, verify install
- [ ] Write `cli/docs/USAGE.md` — complete command reference with flags and examples
- [ ] Write `cli/docs/CI_INTEGRATION.md` — overview of the pytest plugin and bulk-submit approaches
- [ ] Write `cli/docs/TROUBLESHOOTING.md` — common errors and fixes

### Quality check
- [ ] `TESTORIA_INTEGRATION_URL=http://localhost:8000 pytest tests/integration/ -m integration` passes against a local dev backend
- [ ] `ruff check testoria_cli tests` clean

### Docs
- [ ] Update `api/cli-tool.md` Phase 5 success criteria checkboxes
- [ ] Move to `completed/`

---

## Definition of done

- [ ] Integration tests pass against local backend (`TESTORIA_INTEGRATION_URL` set)
- [ ] Integration tests are skipped automatically when `TESTORIA_INTEGRATION_URL` is not set
- [ ] `cli/examples/` contains working GitHub Actions, GitLab CI, and Jenkins examples
- [ ] `cli/docs/` contains INSTALLATION, USAGE, CI_INTEGRATION, and TROUBLESHOOTING guides
- [ ] Overall CLI unit test coverage remains >80%
