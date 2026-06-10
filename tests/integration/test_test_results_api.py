import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="res_tester",
        email="res_tester@example.com",
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
        username="res_lead",
        email="res_lead@example.com",
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
        username="res_readonly",
        email="res_readonly@example.com",
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
        "/api/v1/projects", json={"name": "Result Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def suite_id(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Result Suite"},
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
            "title": "Result Case",
            "priority": "high",
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
        json={"name": "Result Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    return int(resp.json()["id"])


# --- GET /test-runs/{id}/results ---


async def test_list_results_empty(
    client: AsyncClient, run_id: int, tester_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/api/v1/test-runs/{run_id}/results", headers=tester_headers
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_list_results_hides_orphans_by_default(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> None:
    """Case removed from an explicit run's scope after a result was submitted
    should disappear from /results by default, and reappear with
    ?include_orphans=true."""
    c1 = await client.post(
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
    c1_id = c1.json()["id"]
    c2 = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Removed",
            "priority": "medium",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    c2_id = c2.json()["id"]

    run = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={
            "name": "Orphan Run",
            "suite_id": suite_id,
            "include_test_cases": [c1_id, c2_id],
        },
        headers=tester_headers,
    )
    rid = run.json()["id"]

    for cid in (c1_id, c2_id):
        await client.post(
            f"/api/v1/test-runs/{rid}/results",
            json={"test_case_id": cid, "status": "passed"},
            headers=tester_headers,
        )

    # Shrink the run to only c1 — c2's result is now orphaned
    await client.put(
        f"/api/v1/test-runs/{rid}/cases",
        json={"test_case_ids": [c1_id]},
        headers=tester_headers,
    )

    default_resp = await client.get(
        f"/api/v1/test-runs/{rid}/results", headers=tester_headers
    )
    assert default_resp.status_code == 200
    ids = [r["test_case_id"] for r in default_resp.json()]
    assert ids == [c1_id]

    orphan_resp = await client.get(
        f"/api/v1/test-runs/{rid}/results",
        params={"include_orphans": "true"},
        headers=tester_headers,
    )
    orphan_ids = {r["test_case_id"] for r in orphan_resp.json()}
    assert orphan_ids == {c1_id, c2_id}


# --- POST /test-runs/{id}/results (submit / upsert) ---


async def test_submit_result(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed", "comment": "All good"},
        headers=tester_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "passed"
    assert data["test_case_id"] == case_id
    assert data["test_run_id"] == run_id


async def test_submit_result_with_message_and_stack_trace(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={
            "test_case_id": case_id,
            "status": "failed",
            "message": "AssertionError: expected 200 got 500",
            "stack_trace": "Traceback ...\n  File test.py line 42",
        },
        headers=tester_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "AssertionError: expected 200 got 500"
    assert "line 42" in data["stack_trace"]


async def test_submit_result_upsert(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    first = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    assert second.status_code == 201
    data = second.json()
    assert data["id"] == first_id
    assert data["status"] == "passed"


async def test_submit_result_read_only_forbidden(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    read_only_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=read_only_headers,
    )
    assert response.status_code == 403


# --- GET /test-results/{id} ---


async def test_get_result(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "blocked"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/v1/test-results/{result_id}", headers=tester_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"


async def test_get_result_not_found(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/test-results/999999", headers=tester_headers)
    assert response.status_code == 404


# --- PUT /test-results/{id} ---


async def test_update_result(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/test-results/{result_id}",
        json={"status": "passed", "comment": "Retested and fixed"},
        headers=tester_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "passed"
    assert data["comment"] == "Retested and fixed"


# --- GET /test-results/{id}/history ---


async def test_get_history_on_submit(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/v1/test-results/{result_id}/history", headers=tester_headers
    )
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 1
    assert history[0]["status"] == "passed"


async def test_get_history_grows_on_status_change(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    await client.put(
        f"/api/v1/test-results/{result_id}",
        json={"status": "passed"},
        headers=tester_headers,
    )

    response = await client.get(
        f"/api/v1/test-results/{result_id}/history", headers=tester_headers
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_history_no_duplicate_on_same_status(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    # Plan 038: history is now recorded on comment changes too, so a
    # comment edit after a create yields two rows. A true no-op resubmit
    # (same status + same comment) yields exactly one row.
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed", "comment": "ok"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed", "comment": "ok"},
        headers=tester_headers,
    )

    response = await client.get(
        f"/api/v1/test-results/{result_id}/history", headers=tester_headers
    )
    assert len(response.json()) == 1


async def test_get_history_records_comment_change(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    await client.put(
        f"/api/v1/test-results/{result_id}",
        json={"comment": "Updating comment, no status change"},
        headers=tester_headers,
    )

    response = await client.get(
        f"/api/v1/test-results/{result_id}/history", headers=tester_headers
    )
    assert len(response.json()) == 2


async def test_history_not_found(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/test-results/999999/history", headers=tester_headers
    )
    assert response.status_code == 404


# --- Per-step results ---


@pytest_asyncio.fixture
async def case_with_steps_id(
    client: AsyncClient, project_id: int, suite_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Stepped Case",
            "priority": "high",
            "type": "manual",
            "status": "active",
            "steps": [
                {"step": "Open app", "expected": "App opens"},
                {"step": "Login", "expected": "Dashboard shown"},
                {"step": "Logout", "expected": "Login page shown"},
            ],
        },
        headers=lead_headers,
    )
    return int(resp.json()["id"])


async def test_submit_with_step_results(
    client: AsyncClient,
    run_id: int,
    case_with_steps_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={
            "test_case_id": case_with_steps_id,
            "status": "failed",
            "step_results": [
                {"index": 0, "status": "passed"},
                {"index": 1, "status": "failed", "comment": "Login button broken"},
            ],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["step_results"] is not None
    assert len(data["step_results"]) == 2
    assert data["step_results"][0]["status"] == "passed"
    assert data["step_results"][1]["comment"] == "Login button broken"


async def test_submit_without_step_results_returns_null(
    client: AsyncClient,
    run_id: int,
    case_with_steps_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_with_steps_id, "status": "passed"},
        headers=tester_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["step_results"] is None


async def test_update_step_results(
    client: AsyncClient,
    run_id: int,
    case_with_steps_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_with_steps_id, "status": "failed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/v1/test-results/{result_id}",
        json={
            "step_results": [
                {"index": 0, "status": "passed"},
                {"index": 1, "status": "passed"},
                {"index": 2, "status": "passed"},
            ],
        },
        headers=tester_headers,
    )
    assert update_resp.status_code == 200
    assert len(update_resp.json()["step_results"]) == 3


async def test_step_results_out_of_range_rejected(
    client: AsyncClient,
    run_id: int,
    case_with_steps_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={
            "test_case_id": case_with_steps_id,
            "status": "failed",
            "step_results": [{"index": 99, "status": "failed"}],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 400


async def test_step_results_duplicate_index_rejected(
    client: AsyncClient,
    run_id: int,
    case_with_steps_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={
            "test_case_id": case_with_steps_id,
            "status": "failed",
            "step_results": [
                {"index": 0, "status": "passed"},
                {"index": 0, "status": "failed"},
            ],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 400


async def test_step_results_partial_coverage_allowed(
    client: AsyncClient,
    run_id: int,
    case_with_steps_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={
            "test_case_id": case_with_steps_id,
            "status": "failed",
            "step_results": [{"index": 1, "status": "failed"}],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 201
    assert len(resp.json()["step_results"]) == 1


# --- POST /test-results/{id}/attachments ---


async def test_upload_attachment(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/test-results/{result_id}/attachments",
        files={"file": ("screenshot.png", b"fake png data", "image/png")},
        headers=tester_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "screenshot.png"
    assert data["file_size"] == len(b"fake png data")
    assert data["mime_type"] == "image/png"
    assert data["test_result_id"] == result_id


async def test_upload_attachment_read_only_forbidden(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
    read_only_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/test-results/{result_id}/attachments",
        files={"file": ("file.txt", b"data", "text/plain")},
        headers=read_only_headers,
    )
    assert response.status_code == 403


# --- DELETE /test-results/{id}/attachments/{attach_id} ---


async def test_delete_attachment(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    upload_resp = await client.post(
        f"/api/v1/test-results/{result_id}/attachments",
        files={"file": ("log.txt", b"log content", "text/plain")},
        headers=tester_headers,
    )
    attach_id = upload_resp.json()["id"]

    delete_resp = await client.delete(
        f"/api/v1/test-results/{result_id}/attachments/{attach_id}",
        headers=tester_headers,
    )
    assert delete_resp.status_code == 204

    again = await client.delete(
        f"/api/v1/test-results/{result_id}/attachments/{attach_id}",
        headers=tester_headers,
    )
    assert again.status_code == 404


async def test_delete_attachment_wrong_result(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    result_id = create_resp.json()["id"]

    upload_resp = await client.post(
        f"/api/v1/test-results/{result_id}/attachments",
        files={"file": ("x.txt", b"x", "text/plain")},
        headers=tester_headers,
    )
    attach_id = upload_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/test-results/999999/attachments/{attach_id}",
        headers=tester_headers,
    )
    assert response.status_code == 404


# --- status: no_run rename + compat window ---


async def test_submit_result_with_no_run_status(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "no_run"},
        headers=tester_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "no_run"


async def test_submit_result_with_skipped_status_normalises_to_no_run(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "skipped"},
        headers=tester_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "no_run"


async def test_submit_result_defaults_to_no_run_when_status_omitted(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id},
        headers=tester_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "no_run"


async def test_step_results_skipped_normalises_to_no_run(
    client: AsyncClient,
    run_id: int,
    case_with_steps_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={
            "test_case_id": case_with_steps_id,
            "status": "failed",
            "step_results": [{"index": 0, "status": "skipped"}],
        },
        headers=tester_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["step_results"][0]["status"] == "no_run"
