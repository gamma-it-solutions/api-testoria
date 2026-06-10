from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="run_tester",
        email="run_tester@example.com",
        hashed_password=get_password_hash("password"),
        role="tester",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="run_lead",
        email="run_lead@example.com",
        hashed_password=get_password_hash("password"),
        role="lead",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def read_only_user(db_session: AsyncSession) -> User:
    user = User(
        username="run_readonly",
        email="run_readonly@example.com",
        hashed_password=get_password_hash("password"),
        role="read_only",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
def tester_headers(tester_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(tester_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def lead_headers(lead_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(lead_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def read_only_headers(read_only_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(read_only_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def project_id(client: AsyncClient, lead_headers: dict[str, str]) -> int:
    resp = await client.post(
        "/api/v1/projects", json={"name": "Run Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def suite_id(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Run Suite"},
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def case_id(
    client: AsyncClient, project_id: int, suite_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Sample Case",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def run_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Sprint 1 Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    assert resp.status_code == 201
    return int(resp.json()["id"])


# --- GET /projects/{id}/test-runs ---


async def test_list_runs_empty(
    client: AsyncClient, project_id: int, tester_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/api/v1/projects/{project_id}/test-runs", headers=tester_headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_list_runs_no_token(client: AsyncClient, project_id: int) -> None:
    response = await client.get(f"/api/v1/projects/{project_id}/test-runs")
    assert response.status_code == 401


# --- POST /projects/{id}/test-runs ---


async def test_create_run(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Sprint 2", "suite_id": suite_id},
        headers=tester_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Sprint 2"
    assert data["status"] == "planned"
    assert data["project_id"] == project_id


async def test_create_run_read_only_forbidden(
    client: AsyncClient, project_id: int, read_only_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Blocked"},
        headers=read_only_headers,
    )
    assert response.status_code == 403


# --- GET /test-runs/{id} ---


async def test_get_run(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    response = await client.get(f"/api/v1/test-runs/{run_id}", headers=tester_headers)
    assert response.status_code == 200
    assert response.json()["id"] == run_id


async def test_get_run_not_found(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/test-runs/999999", headers=tester_headers)
    assert response.status_code == 404


# --- PUT /test-runs/{id} ---


async def test_update_run(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    response = await client.put(
        f"/api/v1/test-runs/{run_id}",
        json={"name": "Updated Run", "status": "active"},
        headers=tester_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Run"
    assert data["status"] == "active"


async def test_update_run_accepts_in_progress_alias(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    """Legacy `in_progress` is accepted on input and normalised to `active`."""
    response = await client.put(
        f"/api/v1/test-runs/{run_id}",
        json={"status": "in_progress"},
        headers=tester_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"


# --- POST /test-runs/{id}/close ---


async def test_close_run(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/test-runs/{run_id}/close", headers=tester_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["completed_at"] is not None


async def test_close_already_closed_run(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    await client.post(f"/api/v1/test-runs/{run_id}/close", headers=tester_headers)
    response = await client.post(
        f"/api/v1/test-runs/{run_id}/close", headers=tester_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


# --- DELETE /test-runs/{id} ---


async def test_delete_run(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "ToDelete"},
        headers=tester_headers,
    )
    rid = create_resp.json()["id"]
    response = await client.delete(f"/api/v1/test-runs/{rid}", headers=lead_headers)
    assert response.status_code == 204

    get_resp = await client.get(f"/api/v1/test-runs/{rid}", headers=tester_headers)
    assert get_resp.status_code == 404


async def test_delete_run_tester_forbidden(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    response = await client.delete(
        f"/api/v1/test-runs/{run_id}", headers=tester_headers
    )
    assert response.status_code == 403


# --- GET /test-runs/{id}/progress ---


async def test_progress_no_results(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/progress", headers=tester_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] == 0
    assert data["failed"] == 0
    assert "untested" not in data
    assert data["no_run"] == data["total"]
    assert data["pass_rate"] is None


async def test_progress_with_results(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/progress", headers=tester_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] == 1
    assert data["no_run"] == 0
    assert data["pass_rate"] == 1.0


# --- GET /test-runs/{id}/cases ---


async def test_get_run_with_cases(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/cases", headers=tester_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert "run" in data
    assert "cases" in data
    assert len(data["cases"]) >= 1
    assert data["cases"][0]["result"] is None


async def test_progress_ignores_orphan_results(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    """Results for cases that have been removed from the run's explicit
    selection must not inflate the progress counts."""
    kept = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Kept",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    kept_id = kept.json()["id"]
    dropped = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Dropped",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    dropped_id = dropped.json()["id"]

    run = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={
            "name": "Orphan Progress",
            "suite_id": suite_id,
            "include_test_cases": [kept_id, dropped_id],
        },
        headers=tester_headers,
    )
    rid = run.json()["id"]

    for cid in (kept_id, dropped_id):
        await client.post(
            f"/api/v1/test-runs/{rid}/results",
            json={"test_case_id": cid, "status": "passed"},
            headers=tester_headers,
        )

    # Shrink scope to just the first case — second result becomes orphan
    await client.put(
        f"/api/v1/test-runs/{rid}/cases",
        json={"test_case_ids": [kept_id]},
        headers=tester_headers,
    )

    progress = (
        await client.get(
            f"/api/v1/test-runs/{rid}/progress", headers=tester_headers
        )
    ).json()
    assert progress["total"] == 1
    assert progress["passed"] == 1
    assert progress["no_run"] == 0
    assert progress["pass_rate"] == 1.0


async def test_list_runs_always_populates_progress(
    client: AsyncClient,
    project_id: int,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    resp = await client.get(
        f"/api/v1/projects/{project_id}/test-runs",
        headers=tester_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(item.get("progress") is not None for item in items)
    hit = next(i for i in items if i["id"] == run_id)
    progress = hit["progress"]
    assert progress["passed"] >= 1
    assert 0 <= (progress["pass_rate"] or 0) <= 1


async def test_get_run_with_cases_group_by_suite(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    child_suite_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Child Suite", "parent_suite_id": suite_id},
        headers=lead_headers,
    )
    child_suite_id = child_suite_resp.json()["id"]
    for (sid, name) in ((suite_id, "Root case"), (child_suite_id, "Child case")):
        await client.post(
            f"/api/v1/projects/{project_id}/test-cases",
            json={
                "suite_id": sid,
                "title": name,
                "priority": "low",
                "type": "manual",
                "status": "active",
            },
            headers=lead_headers,
        )
    run_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Tree Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    rid = run_resp.json()["id"]

    resp = await client.get(
        f"/api/v1/test-runs/{rid}/cases",
        params={"group_by": "suite"},
        headers=tester_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "roots" in data
    assert "total" in data
    leaf_sum = 0
    def walk(node: dict[str, Any]) -> None:
        nonlocal leaf_sum
        leaf_sum += node["progress"]["total"]
        for c in node["children"]:
            walk(c)
    for root in data["roots"]:
        walk(root)
    assert leaf_sum == data["total"]


async def test_group_by_invalid_returns_422(
    client: AsyncClient,
    run_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.get(
        f"/api/v1/test-runs/{run_id}/cases",
        params={"group_by": "bogus"},
        headers=tester_headers,
    )
    assert resp.status_code == 422


async def test_get_run_with_cases_returns_total_and_case_status(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/cases", headers=tester_headers
    )
    data = response.json()
    assert "total" in data
    assert data["total"] == len(data["cases"])
    assert data["cases"][0]["case_status"] == data["cases"][0]["status"]


async def test_get_run_with_cases_pagination(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    for i in range(5):
        await client.post(
            f"/api/v1/projects/{project_id}/test-cases",
            json={
                "suite_id": suite_id,
                "title": f"Page Case {i}",
                "priority": "low",
                "type": "manual",
                "status": "active",
            },
            headers=lead_headers,
        )
    run_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Pagination Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    rid = run_resp.json()["id"]

    first = await client.get(
        f"/api/v1/test-runs/{rid}/cases",
        params={"limit": 2, "offset": 0},
        headers=tester_headers,
    )
    assert first.status_code == 200
    first_data = first.json()
    assert len(first_data["cases"]) == 2
    assert first_data["total"] >= 5

    second = await client.get(
        f"/api/v1/test-runs/{rid}/cases",
        params={"limit": 2, "offset": 2},
        headers=tester_headers,
    )
    second_ids = [c["id"] for c in second.json()["cases"]]
    first_ids = [c["id"] for c in first_data["cases"]]
    assert set(first_ids).isdisjoint(second_ids)


async def test_get_run_with_cases_invalid_sort(
    client: AsyncClient,
    run_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/cases",
        params={"sort": "bogus"},
        headers=tester_headers,
    )
    assert response.status_code == 422


async def test_get_run_with_cases_limit_clamped(
    client: AsyncClient,
    run_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/cases",
        params={"limit": 9999},
        headers=tester_headers,
    )
    assert response.status_code == 422


async def test_get_run_with_cases_after_submit(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed", "comment": "Bug found"},
        headers=tester_headers,
    )
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/cases", headers=tester_headers
    )
    assert response.status_code == 200
    cases = response.json()["cases"]
    tc = next(c for c in cases if c["id"] == case_id)
    assert tc["result"] is not None
    assert tc["result"]["status"] == "failed"


# --- Explicit case selection ---


async def test_create_run_with_include_test_cases(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    case2_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Second Case",
            "priority": "low",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    case2_id = case2_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={
            "name": "Explicit Run",
            "suite_id": suite_id,
            "include_test_cases": [case_id],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 201
    rid = resp.json()["id"]

    cases_resp = await client.get(
        f"/api/v1/test-runs/{rid}/cases", headers=tester_headers
    )
    assert cases_resp.status_code == 200
    case_ids = [c["id"] for c in cases_resp.json()["cases"]]
    assert case_id in case_ids
    assert case2_id not in case_ids


async def test_create_run_with_empty_include_test_cases(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={
            "name": "Empty explicit",
            "suite_id": suite_id,
            "include_test_cases": [],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 201
    rid = resp.json()["id"]
    assert resp.json()["cases_mode"] == "explicit"

    cases_resp = await client.get(
        f"/api/v1/test-runs/{rid}/cases", headers=tester_headers
    )
    assert cases_resp.status_code == 200
    assert cases_resp.json()["cases"] == []
    assert cases_resp.json()["total"] == 0


async def test_create_run_default_mode_is_auto(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Auto Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["cases_mode"] == "auto"
    rid = resp.json()["id"]

    cases_resp = await client.get(
        f"/api/v1/test-runs/{rid}/cases", headers=tester_headers
    )
    assert cases_resp.json()["total"] >= 1


async def test_put_run_cases_flips_mode_to_explicit(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Flip me", "suite_id": suite_id},
        headers=tester_headers,
    )
    rid = resp.json()["id"]
    assert resp.json()["cases_mode"] == "auto"

    put_resp = await client.put(
        f"/api/v1/test-runs/{rid}/cases",
        json={"test_case_ids": []},
        headers=tester_headers,
    )
    assert put_resp.status_code == 204

    get_resp = await client.get(
        f"/api/v1/test-runs/{rid}", headers=tester_headers
    )
    assert get_resp.json()["cases_mode"] == "explicit"


async def test_create_run_cross_project_case_rejected(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    other_proj = await client.post(
        "/api/v1/projects", json={"name": "Other"}, headers=lead_headers
    )
    other_pid = other_proj.json()["id"]
    other_suite = await client.post(
        f"/api/v1/projects/{other_pid}/test-suites",
        json={"name": "Other Suite"},
        headers=lead_headers,
    )
    other_sid = other_suite.json()["id"]
    other_case = await client.post(
        f"/api/v1/projects/{other_pid}/test-cases",
        json={
            "suite_id": other_sid,
            "title": "Foreign Case",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    foreign_id = other_case.json()["id"]

    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={
            "name": "Should fail",
            "include_test_cases": [foreign_id],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 400


async def test_put_run_cases(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    case2_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Another Case",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    case2_id = case2_resp.json()["id"]

    run_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "PUT cases run", "include_test_cases": [case_id]},
        headers=tester_headers,
    )
    rid = run_resp.json()["id"]

    put_resp = await client.put(
        f"/api/v1/test-runs/{rid}/cases",
        json={"test_case_ids": [case2_id]},
        headers=tester_headers,
    )
    assert put_resp.status_code == 204

    cases_resp = await client.get(
        f"/api/v1/test-runs/{rid}/cases", headers=tester_headers
    )
    case_ids = [c["id"] for c in cases_resp.json()["cases"]]
    assert case2_id in case_ids
    assert case_id not in case_ids


async def test_progress_with_explicit_cases(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    c1 = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Progress Case 1",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    c2 = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Progress Case 2",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    c1_id = c1.json()["id"]
    c2_id = c2.json()["id"]

    run_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Progress Run", "include_test_cases": [c1_id, c2_id]},
        headers=tester_headers,
    )
    rid = run_resp.json()["id"]

    progress_resp = await client.get(
        f"/api/v1/test-runs/{rid}/progress", headers=tester_headers
    )
    assert progress_resp.status_code == 200
    data = progress_resp.json()
    assert data["total"] == 2
    assert data["no_run"] == 2


async def test_put_run_cases_blocked_when_run_completed(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    run_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Locked Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    rid = run_resp.json()["id"]

    await client.post(
        f"/api/v1/test-runs/{rid}/close", headers=tester_headers
    )

    resp = await client.put(
        f"/api/v1/test-runs/{rid}/cases",
        json={"test_case_ids": [case_id]},
        headers=tester_headers,
    )
    assert resp.status_code == 409


async def test_put_run_cases_no_auth(
    client: AsyncClient, run_id: int
) -> None:
    resp = await client.put(
        f"/api/v1/test-runs/{run_id}/cases",
        json={"test_case_ids": []},
    )
    assert resp.status_code == 401


async def test_filter_runs_by_status(
    client: AsyncClient,
    project_id: int,
    run_id: int,
    tester_headers: dict[str, str],
) -> None:
    await client.post(f"/api/v1/test-runs/{run_id}/close", headers=tester_headers)
    response = await client.get(
        f"/api/v1/projects/{project_id}/test-runs",
        params={"status": "completed"},
        headers=tester_headers,
    )
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["status"] == "completed"


# --- Lifecycle: planned → active → completed (plan 039) ---


async def test_run_lifecycle_planned_active_completed(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    # 1. Fresh run starts as `planned`.
    created = await client.get(f"/api/v1/test-runs/{run_id}", headers=tester_headers)
    assert created.json()["status"] == "planned"

    # 2. First meaningful result flips the run to `active`.
    submit = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    assert submit.status_code == 201
    after_submit = await client.get(
        f"/api/v1/test-runs/{run_id}", headers=tester_headers
    )
    assert after_submit.json()["status"] == "active"

    # 3. Close is still the only path to `completed`.
    close = await client.post(
        f"/api/v1/test-runs/{run_id}/close", headers=tester_headers
    )
    assert close.status_code == 200
    assert close.json()["status"] == "completed"


async def test_auto_transition_skips_on_no_op_write(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    """Submitting the same status/comment twice should not flip the run back
    to `active` if it has already been closed (the guarded UPDATE is a no-op).
    """
    # First submit → run becomes active.
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    # Close the run.
    await client.post(f"/api/v1/test-runs/{run_id}/close", headers=tester_headers)

    # Re-submitting an identical payload must NOT move the run back.
    resubmit = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    assert resubmit.status_code == 201
    after = await client.get(f"/api/v1/test-runs/{run_id}", headers=tester_headers)
    assert after.json()["status"] == "completed"
