"""Exit codes are the pipeline contract — assert them directly."""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from testoria_cli.main import app

runner = CliRunner()

JUNIT = b"""<testsuites><testsuite name="pytest" tests="2">
<testcase classname="tests.auth.test_auth.TestAuth" name="test_ok"/>
<testcase classname="tests.auth.test_auth.TestAuth" name="test_bad">
<failure message="boom">trace</failure></testcase>
</testsuite></testsuites>"""


class FakeClient:
    """Stands in for TestoriaClient; records calls so intent can be asserted."""

    def __init__(self, report: dict[str, Any] | None = None) -> None:
        self.report = report or {
            "run_id": 42,
            "total": 2,
            "matched": 2,
            "submitted": 2,
            "unmatched": 0,
            "unmatched_cases": [],
            "matched_by": {"automation_id_dotted": 2},
            "status_counts": {"passed": 1, "failed": 1},
        }
        self.posts: list[str] = []
        self.uploads: list[str] = []

    def upload(self, path: str, *args: Any, **kwargs: Any) -> Any:
        self.uploads.append(path)
        return self.report

    def post(self, path: str, json: Any = None) -> Any:
        self.posts.append(path)
        return {"id": 99, "name": (json or {}).get("name", "")}

    def get(self, path: str, params: Any = None) -> Any:
        return {}


@pytest.fixture
def report_file(tmp_path: Path) -> Path:
    path = tmp_path / "junit.xml"
    path.write_bytes(JUNIT)
    return path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()

    def build(_credentials: Any, **_kwargs: Any) -> FakeClient:
        # **_kwargs absorbs prefer_jwt, which main passes for key commands.
        return client

    monkeypatch.setattr("testoria_cli.main.TestoriaClient", build)
    monkeypatch.setenv("TESTORIA_URL", "https://api.test")
    monkeypatch.setenv("TESTORIA_API_KEY", "tsk_a_b")
    return client


def test_clean_upload_exits_zero(
    fake: FakeClient, report_file: Path
) -> None:
    result = runner.invoke(app, ["upload", str(report_file), "--run", "42"])

    assert result.exit_code == 0, result.output
    assert fake.uploads == ["/test-runs/42/results/import"]


def test_unmatched_without_strict_still_exits_zero(
    fake: FakeClient, report_file: Path
) -> None:
    """An unmapped test is a cataloguing problem, not a build failure."""
    fake.report = {**fake.report, "unmatched": 3, "unmatched_cases": []}

    result = runner.invoke(app, ["upload", str(report_file), "--run", "42"])

    assert result.exit_code == 0


def test_unmatched_with_strict_exits_two(
    fake: FakeClient, report_file: Path
) -> None:
    fake.report = {
        **fake.report,
        "unmatched": 1,
        "unmatched_cases": [
            {
                "identifier": "tests.a.test_renamed",
                "name": "test_renamed",
                "classname": "tests.a",
                "status": "passed",
                "reason": "no_match",
            }
        ],
    }

    result = runner.invoke(
        app, ["upload", str(report_file), "--run", "42", "--strict"]
    )

    assert result.exit_code == 2


def test_strict_failure_leaves_the_run_open(
    fake: FakeClient, report_file: Path
) -> None:
    """A run that failed its mapping check stays open for inspection."""
    fake.report = {**fake.report, "unmatched": 1, "unmatched_cases": []}

    runner.invoke(
        app,
        [
            "upload",
            str(report_file),
            "--run",
            "42",
            "--strict",
            "--close-on-finish",
        ],
    )

    assert not any("close" in path for path in fake.posts)


def test_close_on_finish_closes_the_run(
    fake: FakeClient, report_file: Path
) -> None:
    result = runner.invoke(
        app, ["upload", str(report_file), "--run", "42", "--close-on-finish"]
    )

    assert result.exit_code == 0
    assert "/test-runs/42/close" in fake.posts


def test_create_run_then_uploads_into_it(
    fake: FakeClient, report_file: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "upload",
            str(report_file),
            "--project",
            "3",
            "--create-run",
            "CI #12",
        ],
    )

    assert result.exit_code == 0
    assert fake.posts[0] == "/projects/3/test-runs"
    assert fake.uploads == ["/test-runs/99/results/import"]


def test_run_and_create_run_together_is_a_usage_error(
    fake: FakeClient, report_file: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "upload",
            str(report_file),
            "--run",
            "42",
            "--project",
            "3",
            "--create-run",
            "x",
        ],
    )

    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_neither_run_nor_project_is_a_usage_error(
    fake: FakeClient, report_file: Path
) -> None:
    result = runner.invoke(app, ["upload", str(report_file)])

    assert result.exit_code == 1
    assert "--run" in result.output


def test_missing_file_is_rejected_before_the_network(
    fake: FakeClient, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["upload", str(tmp_path / "absent.xml"), "--run", "42"]
    )

    assert result.exit_code != 0
    assert fake.uploads == []


def test_malformed_xml_fails_before_upload(
    fake: FakeClient, tmp_path: Path
) -> None:
    path = tmp_path / "bad.xml"
    path.write_bytes(b"<testsuite><oops>")

    result = runner.invoke(app, ["upload", str(path), "--run", "42"])

    assert result.exit_code == 1
    assert fake.uploads == []


def test_json_output_emits_the_raw_report(
    fake: FakeClient, report_file: Path
) -> None:
    result = runner.invoke(
        app, ["upload", str(report_file), "--run", "42", "-o", "json"]
    )

    assert result.exit_code == 0
    assert '"automation_id_dotted": 2' in result.output


def test_text_output_names_the_matching_rule(
    fake: FakeClient, report_file: Path
) -> None:
    """A suite matching on `title` is one rename from reporting nothing."""
    result = runner.invoke(app, ["upload", str(report_file), "--run", "42"])

    assert "automation_id_dotted" in result.output


def test_missing_credentials_exits_one(
    monkeypatch: pytest.MonkeyPatch, report_file: Path, tmp_path: Path
) -> None:
    monkeypatch.delenv("TESTORIA_API_KEY", raising=False)
    monkeypatch.setenv("TESTORIA_URL", "https://api.test")
    monkeypatch.setattr("testoria_cli.config.CONFIG_PATH", tmp_path / "absent.yaml")

    result = runner.invoke(app, ["upload", str(report_file), "--run", "42"])

    assert result.exit_code == 1
