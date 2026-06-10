import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def viewer_user(db_session: AsyncSession) -> User:
    user = User(
        username="report_viewer",
        email="report_viewer@example.com",
        hashed_password=get_password_hash("password"),
        role="read_only",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="report_lead",
        email="report_lead@example.com",
        hashed_password=get_password_hash("password"),
        role="lead",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="report_tester",
        email="report_tester@example.com",
        hashed_password=get_password_hash("password"),
        role="tester",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
def viewer_headers(viewer_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(viewer_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def lead_headers(lead_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(lead_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tester_headers(tester_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(tester_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def project_id(client: AsyncClient, lead_headers: dict[str, str]) -> int:
    resp = await client.post(
        "/api/v1/projects", json={"name": "Report Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def suite_id(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Report Suite"},
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def case_ids(
    client: AsyncClient, project_id: int, suite_id: int, lead_headers: dict[str, str]
) -> list[int]:
    ids = []
    for title in ["Case Alpha", "Case Beta", "Case Gamma"]:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/test-cases",
            json={
                "suite_id": suite_id,
                "title": title,
                "priority": "medium",
                "type": "manual",
                "status": "active",
            },
            headers=lead_headers,
        )
        ids.append(int(resp.json()["id"]))
    return ids


@pytest_asyncio.fixture
async def run_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Report Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    assert resp.status_code == 201
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def run_with_results(
    client: AsyncClient,
    run_id: int,
    case_ids: list[int],
    tester_headers: dict[str, str],
) -> int:
    """Submit results: first passed, second failed, third left untested."""
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_ids[0], "status": "passed"},
        headers=tester_headers,
    )
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_ids[1], "status": "failed", "comment": "Bug found"},
        headers=tester_headers,
    )
    return run_id


# --- GET /projects/{id}/dashboard ---


async def test_dashboard_empty_project(
    client: AsyncClient, project_id: int, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get(
        f"/api/v1/projects/{project_id}/dashboard", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_test_cases"] == 0
    assert data["total_test_runs"] == 0
    assert data["pass_rate"] == 0.0
    assert data["recent_runs"] == []
    assert data["result_distribution"] == {}


async def test_dashboard_with_data(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    case_ids: list[int],
    viewer_headers: dict[str, str],
) -> None:
    """After plan 039, pass-rate counts only completed runs. The fixture run
    is `active` (auto-transitioned by the result submits) but not closed,
    so summary stats are empty. Per-run counts in `recent_runs` still show
    the work in flight."""
    resp = await client.get(
        f"/api/v1/projects/{project_id}/dashboard", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_test_cases"] == 3
    assert data["total_test_runs"] == 1
    assert data["total_test_suites"] == 1
    # The run auto-transitioned from planned to active on the first result.
    assert data["active_runs"] == 1
    # No completed runs → pass_rate falls back to 0.0 and distribution is empty.
    assert data["pass_rate"] == 0.0
    assert data["result_distribution"] == {}
    assert len(data["recent_runs"]) == 1
    assert data["recent_runs"][0]["status"] == "active"
    assert data["recent_runs"][0]["passed"] == 1
    assert data["recent_runs"][0]["failed"] == 1


async def test_dashboard_counts_only_completed_runs(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    tester_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    """Closing the run should flip it into the pass-rate denominator."""
    await client.post(
        f"/api/v1/test-runs/{run_with_results}/close", headers=tester_headers
    )
    resp = await client.get(
        f"/api/v1/projects/{project_id}/dashboard", headers=viewer_headers
    )
    data = resp.json()
    assert data["active_runs"] == 0
    assert data["pass_rate"] == 0.5
    assert data["result_distribution"].get("passed") == 1
    assert data["result_distribution"].get("failed") == 1


async def test_dashboard_not_found(
    client: AsyncClient, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get("/api/v1/projects/999999/dashboard", headers=viewer_headers)
    assert resp.status_code == 404


async def test_dashboard_no_auth(client: AsyncClient, project_id: int) -> None:
    resp = await client.get(f"/api/v1/projects/{project_id}/dashboard")
    assert resp.status_code == 401


# --- GET /test-runs/{id}/report ---


async def test_run_report_json(
    client: AsyncClient,
    run_with_results: int,
    case_ids: list[int],
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        f"/api/v1/test-runs/{run_with_results}/report", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_with_results
    assert data["passed"] == 1
    assert data["failed"] == 1
    # Cases with no result now count as no_run (untested-fold)
    assert data["no_run"] == 1
    assert data["total"] == 3
    assert len(data["cases"]) == 3
    # Denominator is total, ratio in [0, 1]
    assert data["pass_rate"] == pytest.approx(0.333, abs=5e-4)


async def test_run_report_excel(
    client: AsyncClient,
    run_with_results: int,
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        f"/api/v1/test-runs/{run_with_results}/report",
        params={"format": "excel"},
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    assert (
        resp.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(resp.content) > 0


async def test_run_report_pdf(
    client: AsyncClient,
    run_with_results: int,
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        f"/api/v1/test-runs/{run_with_results}/report",
        params={"format": "pdf"},
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 0


async def test_run_report_not_found(
    client: AsyncClient, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get("/api/v1/test-runs/999999/report", headers=viewer_headers)
    assert resp.status_code == 404


async def test_run_report_no_auth(client: AsyncClient, run_with_results: int) -> None:
    resp = await client.get(f"/api/v1/test-runs/{run_with_results}/report")
    assert resp.status_code == 401


# --- GET /projects/{id}/metrics ---


async def test_metrics_empty(
    client: AsyncClient, project_id: int, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get(
        f"/api/v1/projects/{project_id}/metrics", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project_id
    assert data["days"] == 30
    assert data["data"] == []


async def test_metrics_with_data(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        f"/api/v1/projects/{project_id}/metrics", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) >= 1
    point = data["data"][0]
    assert point["passed"] + point["failed"] == point["total"]


async def test_metrics_not_found(
    client: AsyncClient, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get("/api/v1/projects/999999/metrics", headers=viewer_headers)
    assert resp.status_code == 404


# --- GET /projects/{id}/report-analytics ---


async def test_report_analytics_empty_project(
    client: AsyncClient, project_id: int, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get(
        f"/api/v1/projects/{project_id}/report-analytics", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project_id
    assert data["summary"]["total_test_cases"] == 0
    assert data["summary"]["total_test_runs"] == 0
    assert data["summary"]["active_runs"] == 0
    assert data["summary"]["overall_pass_rate"] == 0.0
    assert data["runs"] == []
    assert data["test_case_distribution"]["by_automation"] == {
        "automated": 0,
        "manual": 0,
    }
    assert data["trend"] == []


async def test_report_analytics_happy_path(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    case_ids: list[int],
    viewer_headers: dict[str, str],
) -> None:
    """After plan 039: the summary pass-rate / distribution count only
    completed runs. The run in the fixture is `active`, so the summary is
    empty but the per-run `runs` list still surfaces its counts unchanged."""
    resp = await client.get(
        f"/api/v1/projects/{project_id}/report-analytics", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_test_cases"] == 3
    assert data["summary"]["total_test_runs"] == 1
    assert data["summary"]["active_runs"] == 1
    assert data["summary"]["total_results"] == 0
    assert data["summary"]["result_distribution"] == {}
    assert data["summary"]["overall_pass_rate"] == 0.0
    assert len(data["runs"]) == 1
    run = data["runs"][0]
    assert run["id"] == run_with_results
    assert run["status"] == "active"
    assert run["passed"] == 1
    assert run["failed"] == 1
    assert run["total"] == 2
    assert run["pass_rate"] == 0.5
    assert data["test_case_distribution"]["by_priority"] == {"medium": 3}
    assert data["test_case_distribution"]["by_type"] == {"manual": 3}
    assert data["test_case_distribution"]["by_automation"] == {
        "automated": 0,
        "manual": 3,
    }


async def test_report_analytics_summary_counts_only_completed_runs(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    tester_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    await client.post(
        f"/api/v1/test-runs/{run_with_results}/close", headers=tester_headers
    )
    resp = await client.get(
        f"/api/v1/projects/{project_id}/report-analytics", headers=viewer_headers
    )
    data = resp.json()
    assert data["summary"]["active_runs"] == 0
    assert data["summary"]["total_results"] == 2
    assert data["summary"]["result_distribution"].get("passed") == 1
    assert data["summary"]["result_distribution"].get("failed") == 1
    # Run has 2 results (1 passed, 1 failed) but its scope is the 3 cases
    # in the suite; per-run rate = 1 / max(3, 2) = 1/3 (plan 041).
    assert data["summary"]["overall_pass_rate"] == pytest.approx(0.333, abs=5e-4)


async def test_report_analytics_overall_pass_rate_matches_run_progress(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_ids: list[int],
    tester_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    """Regression for the Dashboard/Reports mismatch: when a completed run has
    untested cases, its per-run pass rate must use
    `passed / max(cases_in_scope, tested)` — the same denominator the run-list
    endpoint exposes via `TestRun.progress.pass_rate` — so Dashboard and
    Reports agree. Previously Reports divided passed by results-written,
    yielding 100% for a 1-passed-of-3-cases run while the Dashboard showed
    33.3%. Seed one run with 1 passed result and 2 untested cases: expect
    overall_pass_rate ≈ 0.333."""
    run = (
        await client.post(
            f"/api/v1/projects/{project_id}/test-runs",
            json={"name": "Partial", "suite_id": suite_id},
            headers=tester_headers,
        )
    ).json()
    run_id = int(run["id"])
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_ids[0], "status": "passed"},
        headers=tester_headers,
    )
    await client.post(
        f"/api/v1/test-runs/{run_id}/close", headers=tester_headers
    )

    # Reference: what the run-list endpoint reports
    list_resp = await client.get(
        f"/api/v1/projects/{project_id}/test-runs", headers=viewer_headers
    )
    run_row = next(r for r in list_resp.json()["items"] if r["id"] == run_id)
    assert run_row["progress"]["pass_rate"] == pytest.approx(0.333, abs=5e-4)

    # Reports summary must match
    resp = await client.get(
        f"/api/v1/projects/{project_id}/report-analytics", headers=viewer_headers
    )
    assert resp.json()["summary"]["overall_pass_rate"] == pytest.approx(0.333, abs=5e-4)


async def test_report_analytics_overall_pass_rate_is_mean_of_run_rates(
    client: AsyncClient,
    project_id: int,
    lead_headers: dict[str, str],
    tester_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    """plan 041: `overall_pass_rate` is the arithmetic mean of each completed
    run's own pass rate. Each run lives in its own suite so the per-run scope
    matches the results written. Seed runs at 1/1 (100%) and 1/2 (50%):
    weighted would be 2/3 ≈ 0.667; mean-of-rates is 0.75."""

    async def _make_run(suite_name: str, statuses: list[str]) -> None:
        suite = (
            await client.post(
                f"/api/v1/projects/{project_id}/test-suites",
                json={"name": suite_name},
                headers=lead_headers,
            )
        ).json()
        suite_id = int(suite["id"])
        case_ids: list[int] = []
        for i in range(len(statuses)):
            c = (
                await client.post(
                    f"/api/v1/projects/{project_id}/test-cases",
                    json={
                        "suite_id": suite_id,
                        "title": f"{suite_name}-C{i}",
                        "priority": "medium",
                        "type": "manual",
                        "status": "active",
                    },
                    headers=lead_headers,
                )
            ).json()
            case_ids.append(int(c["id"]))
        run = (
            await client.post(
                f"/api/v1/projects/{project_id}/test-runs",
                json={"name": suite_name, "suite_id": suite_id},
                headers=tester_headers,
            )
        ).json()
        run_id = int(run["id"])
        for case_id, status in zip(case_ids, statuses, strict=True):
            await client.post(
                f"/api/v1/test-runs/{run_id}/results",
                json={"test_case_id": case_id, "status": status},
                headers=tester_headers,
            )
        await client.post(
            f"/api/v1/test-runs/{run_id}/close", headers=tester_headers
        )

    await _make_run("SuiteA", ["passed"])
    await _make_run("SuiteB", ["passed", "failed"])

    resp = await client.get(
        f"/api/v1/projects/{project_id}/report-analytics", headers=viewer_headers
    )
    data = resp.json()
    assert data["summary"]["overall_pass_rate"] == pytest.approx(0.75)
    # result_distribution still sums all results across the project
    assert data["summary"]["result_distribution"].get("passed") == 2
    assert data["summary"]["result_distribution"].get("failed") == 1


async def test_report_analytics_date_filter(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    viewer_headers: dict[str, str],
) -> None:
    # Window in the far past: excludes the run
    resp = await client.get(
        f"/api/v1/projects/{project_id}/report-analytics",
        params={
            "date_from": "2000-01-01T00:00:00Z",
            "date_to": "2000-01-02T00:00:00Z",
        },
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_test_runs"] == 1  # project-wide
    assert data["runs"] == []  # window excludes the run


async def test_report_analytics_include_trend_false(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        f"/api/v1/projects/{project_id}/report-analytics",
        params={"include_trend": "false"},
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["trend"] == []


async def test_report_analytics_not_found(
    client: AsyncClient, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get(
        "/api/v1/projects/999999/report-analytics", headers=viewer_headers
    )
    assert resp.status_code == 404


async def test_report_analytics_no_auth(
    client: AsyncClient, project_id: int
) -> None:
    resp = await client.get(f"/api/v1/projects/{project_id}/report-analytics")
    assert resp.status_code == 401


# --- POST /reports/custom ---


async def test_custom_report(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.post(
        "/api/v1/reports/custom",
        json={"project_id": project_id},
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2  # 2 results submitted
    assert len(data["items"]) == 2


async def test_custom_report_filter_status(
    client: AsyncClient,
    project_id: int,
    run_with_results: int,
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.post(
        "/api/v1/reports/custom",
        json={"project_id": project_id, "status": ["passed"]},
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "passed"


async def test_custom_report_not_found_project(
    client: AsyncClient, viewer_headers: dict[str, str]
) -> None:
    resp = await client.post(
        "/api/v1/reports/custom",
        json={"project_id": 999999},
        headers=viewer_headers,
    )
    assert resp.status_code == 404


async def test_custom_report_no_auth(client: AsyncClient, project_id: int) -> None:
    resp = await client.post(
        "/api/v1/reports/custom",
        json={"project_id": project_id},
    )
    assert resp.status_code == 401


# --- GET /reports/analytics (cross-project) ---


@pytest_asyncio.fixture
async def second_project_id(client: AsyncClient, lead_headers: dict[str, str]) -> int:
    resp = await client.post(
        "/api/v1/projects", json={"name": "Second Report Project"},
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def cross_project_data(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_ids: list[int],
    second_project_id: int,
    lead_headers: dict[str, str],
    tester_headers: dict[str, str],
) -> dict[str, int]:
    """Set up two projects, each with one completed run carrying results.
    Project 1 (default fixtures): 3 cases / 1 run / 1 passed + 1 failed.
    Project 2: 1 suite / 1 case / 1 completed run / 1 passed.
    """
    # Project 1 — close the run started by run_with_results-style setup.
    p1_run = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "P1-Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    p1_run_id = int(p1_run.json()["id"])
    await client.post(
        f"/api/v1/test-runs/{p1_run_id}/results",
        json={"test_case_id": case_ids[0], "status": "passed"},
        headers=tester_headers,
    )
    await client.post(
        f"/api/v1/test-runs/{p1_run_id}/results",
        json={"test_case_id": case_ids[1], "status": "failed"},
        headers=tester_headers,
    )
    await client.post(
        f"/api/v1/test-runs/{p1_run_id}/close", headers=tester_headers
    )

    # Project 2 — fresh suite, case, run, result.
    p2_suite = await client.post(
        f"/api/v1/projects/{second_project_id}/test-suites",
        json={"name": "P2 Suite"},
        headers=lead_headers,
    )
    p2_suite_id = int(p2_suite.json()["id"])
    p2_case = await client.post(
        f"/api/v1/projects/{second_project_id}/test-cases",
        json={
            "suite_id": p2_suite_id,
            "title": "P2 Case",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    p2_case_id = int(p2_case.json()["id"])
    p2_run = await client.post(
        f"/api/v1/projects/{second_project_id}/test-runs",
        json={"name": "P2-Run", "suite_id": p2_suite_id},
        headers=tester_headers,
    )
    p2_run_id = int(p2_run.json()["id"])
    await client.post(
        f"/api/v1/test-runs/{p2_run_id}/results",
        json={"test_case_id": p2_case_id, "status": "passed"},
        headers=tester_headers,
    )
    await client.post(
        f"/api/v1/test-runs/{p2_run_id}/close", headers=tester_headers
    )
    return {
        "p1_run_id": p1_run_id,
        "p2_run_id": p2_run_id,
        "p2_suite_id": p2_suite_id,
        "p2_case_id": p2_case_id,
    }


async def test_cross_project_analytics_no_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/reports/analytics")
    assert resp.status_code == 401


async def test_cross_project_analytics_empty_database(
    client: AsyncClient, viewer_headers: dict[str, str]
) -> None:
    """No projects at all — endpoint returns the documented empty payload."""
    resp = await client.get(
        "/api/v1/reports/analytics", headers=viewer_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_ids"] is None
    assert data["summary"]["total_test_runs"] == 0
    assert data["summary"]["overall_pass_rate"] == 0.0
    assert data["runs"] == []
    assert data["per_project"] == []


async def test_cross_project_analytics_explicit_subset(
    client: AsyncClient,
    project_id: int,
    second_project_id: int,
    cross_project_data: dict[str, int],
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        "/api/v1/reports/analytics",
        params=[("project_ids", project_id), ("project_ids", second_project_id)],
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert sorted(data["project_ids"]) == sorted([project_id, second_project_id])
    # Per-project rows for both projects
    by_project = {row["project_id"]: row for row in data["per_project"]}
    assert project_id in by_project
    assert second_project_id in by_project
    # P1 rate = 1/3 (1 passed of 3 cases-in-scope), P2 rate = 1.0
    assert by_project[project_id]["overall_pass_rate"] == pytest.approx(0.333, abs=5e-4)
    assert by_project[second_project_id]["overall_pass_rate"] == pytest.approx(1.0)
    # Summary mean = (1/3 + 1.0) / 2
    # Mean of (0.333, 1.0) rounded to 3 decimals.
    assert data["summary"]["overall_pass_rate"] == pytest.approx(0.667, abs=5e-4)
    # Runs list carries project_id + project_name
    for run_row in data["runs"]:
        assert run_row["project_id"] in (project_id, second_project_id)
        assert run_row["project_name"] is not None


async def test_cross_project_analytics_default_includes_all_visible(
    client: AsyncClient,
    project_id: int,
    second_project_id: int,
    cross_project_data: dict[str, int],
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        "/api/v1/reports/analytics", headers=viewer_headers
    )
    data = resp.json()
    assert data["project_ids"] is None
    project_ids_in_breakdown = {row["project_id"] for row in data["per_project"]}
    assert {project_id, second_project_id}.issubset(project_ids_in_breakdown)


async def test_cross_project_analytics_unknown_id_silently_dropped(
    client: AsyncClient,
    project_id: int,
    cross_project_data: dict[str, int],
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        "/api/v1/reports/analytics",
        params=[("project_ids", project_id), ("project_ids", 999_999)],
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    breakdown_ids = {row["project_id"] for row in data["per_project"]}
    assert breakdown_ids == {project_id}


async def test_cross_project_analytics_include_trend_false(
    client: AsyncClient,
    project_id: int,
    cross_project_data: dict[str, int],
    viewer_headers: dict[str, str],
) -> None:
    resp = await client.get(
        "/api/v1/reports/analytics",
        params=[("include_trend", "false"), ("project_ids", project_id)],
        headers=viewer_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["trend"] == []


async def test_cross_project_analytics_malformed_date(
    client: AsyncClient, viewer_headers: dict[str, str]
) -> None:
    resp = await client.get(
        "/api/v1/reports/analytics",
        params={"date_from": "not-a-date"},
        headers=viewer_headers,
    )
    assert resp.status_code == 422
