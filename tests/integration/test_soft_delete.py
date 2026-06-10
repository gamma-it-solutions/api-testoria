import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.milestone import Milestone
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.models.user import User


@pytest_asyncio.fixture
async def admin(db_session: AsyncSession) -> User:
    user = User(
        username="sd_admin",
        email="sd_admin@example.com",
        hashed_password=get_password_hash("password"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
def admin_hdr(admin: User) -> dict[str, str]:
    token = create_access_token({"sub": str(admin.id)})
    return {"Authorization": f"Bearer {token}"}


async def _make_project(
    client: AsyncClient, hdr: dict[str, str], name: str = "SD"
) -> int:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=hdr)
    assert resp.status_code == 201
    return int(resp.json()["id"])


async def _make_suite(
    client: AsyncClient, project_id: int, hdr: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Suite"},
        headers=hdr,
    )
    assert resp.status_code == 201
    return int(resp.json()["id"])


async def _make_case(
    client: AsyncClient, project_id: int, suite_id: int, hdr: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Case",
            "steps": [{"step": "s", "expected": "e"}],
            "priority": "medium",
            "type": "manual",
            "status": "active",
            "tags": [],
        },
        headers=hdr,
    )
    assert resp.status_code == 201
    return int(resp.json()["id"])


# --- Project soft delete ---


async def test_delete_project_is_soft(
    client: AsyncClient, db_session: AsyncSession, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr)

    resp = await client.delete(f"/api/v1/projects/{pid}", headers=admin_hdr)
    assert resp.status_code == 204

    # Row still exists with deleted_at populated
    row = (
        await db_session.execute(select(Project).where(Project.id == pid))
    ).scalar_one()
    assert row.deleted_at is not None

    # Default GET excludes it
    get_resp = await client.get(f"/api/v1/projects/{pid}", headers=admin_hdr)
    assert get_resp.status_code == 404

    # List with include_deleted=True returns it
    list_resp = await client.get(
        "/api/v1/projects?include_deleted=true", headers=admin_hdr
    )
    assert list_resp.status_code == 200
    ids = [p["id"] for p in list_resp.json()["items"]]
    assert pid in ids


async def test_delete_project_cascades_soft_delete(
    client: AsyncClient, db_session: AsyncSession, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="Cascade")
    sid = await _make_suite(client, pid, admin_hdr)
    cid = await _make_case(client, pid, sid, admin_hdr)

    resp = await client.delete(f"/api/v1/projects/{pid}", headers=admin_hdr)
    assert resp.status_code == 204

    suite = (
        await db_session.execute(select(TestSuite).where(TestSuite.id == sid))
    ).scalar_one()
    case = (
        await db_session.execute(select(TestCase).where(TestCase.id == cid))
    ).scalar_one()
    assert suite.deleted_at is not None
    assert case.deleted_at is not None


async def test_restore_project(
    client: AsyncClient, db_session: AsyncSession, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="Restore")
    await client.delete(f"/api/v1/projects/{pid}", headers=admin_hdr)

    resp = await client.post(f"/api/v1/projects/{pid}/restore", headers=admin_hdr)
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is None

    # Now reachable again
    get_resp = await client.get(f"/api/v1/projects/{pid}", headers=admin_hdr)
    assert get_resp.status_code == 200


async def test_restore_project_not_deleted_returns_400(
    client: AsyncClient, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="NotDeleted")
    resp = await client.post(f"/api/v1/projects/{pid}/restore", headers=admin_hdr)
    assert resp.status_code == 400


# --- Suite/case: restore blocked when parent deleted ---


async def test_restore_suite_blocked_when_project_deleted(
    client: AsyncClient, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="ParentDeleted")
    sid = await _make_suite(client, pid, admin_hdr)

    # Delete suite first (explicitly), then delete project (cascades)
    await client.delete(f"/api/v1/test-suites/{sid}", headers=admin_hdr)
    await client.delete(f"/api/v1/projects/{pid}", headers=admin_hdr)

    resp = await client.post(
        f"/api/v1/test-suites/{sid}/restore", headers=admin_hdr
    )
    assert resp.status_code == 400


async def test_restore_test_case(
    client: AsyncClient, db_session: AsyncSession, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="RestoreCase")
    sid = await _make_suite(client, pid, admin_hdr)
    cid = await _make_case(client, pid, sid, admin_hdr)

    del_resp = await client.delete(f"/api/v1/test-cases/{cid}", headers=admin_hdr)
    assert del_resp.status_code == 204

    # Default GET excludes
    get_resp = await client.get(f"/api/v1/test-cases/{cid}", headers=admin_hdr)
    assert get_resp.status_code == 404

    restore_resp = await client.post(
        f"/api/v1/test-cases/{cid}/restore", headers=admin_hdr
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["deleted_at"] is None


# --- TestRun cascades to results ---


async def test_delete_run_cascades_results(
    client: AsyncClient, db_session: AsyncSession, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="RunCascade")
    sid = await _make_suite(client, pid, admin_hdr)
    cid = await _make_case(client, pid, sid, admin_hdr)

    run_resp = await client.post(
        f"/api/v1/projects/{pid}/test-runs",
        json={"name": "R", "suite_id": sid},
        headers=admin_hdr,
    )
    assert run_resp.status_code == 201
    rid = int(run_resp.json()["id"])

    res_resp = await client.post(
        f"/api/v1/test-runs/{rid}/results",
        json={"test_case_id": cid, "status": "passed"},
        headers=admin_hdr,
    )
    assert res_resp.status_code == 201

    del_resp = await client.delete(f"/api/v1/test-runs/{rid}", headers=admin_hdr)
    assert del_resp.status_code == 204

    run = (
        await db_session.execute(select(TestRun).where(TestRun.id == rid))
    ).scalar_one()
    assert run.deleted_at is not None


# --- List include_deleted filter on test cases ---


async def test_list_test_cases_include_deleted(
    client: AsyncClient, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="ListInclude")
    sid = await _make_suite(client, pid, admin_hdr)
    cid = await _make_case(client, pid, sid, admin_hdr)

    await client.delete(f"/api/v1/test-cases/{cid}", headers=admin_hdr)

    default_resp = await client.get(
        f"/api/v1/projects/{pid}/test-cases", headers=admin_hdr
    )
    assert default_resp.status_code == 200
    default_ids = [c["id"] for c in default_resp.json()["items"]]
    assert cid not in default_ids

    include_resp = await client.get(
        f"/api/v1/projects/{pid}/test-cases?include_deleted=true", headers=admin_hdr
    )
    assert include_resp.status_code == 200
    include_ids = [c["id"] for c in include_resp.json()["items"]]
    assert cid in include_ids


# --- Milestone soft delete ---


async def test_milestone_soft_delete_and_restore(
    client: AsyncClient, db_session: AsyncSession, admin_hdr: dict[str, str]
) -> None:
    pid = await _make_project(client, admin_hdr, name="MsSoft")
    ms_resp = await client.post(
        f"/api/v1/projects/{pid}/milestones",
        json={"name": "v1.0"},
        headers=admin_hdr,
    )
    assert ms_resp.status_code == 201
    mid = int(ms_resp.json()["id"])

    await client.delete(f"/api/v1/milestones/{mid}", headers=admin_hdr)
    row = (
        await db_session.execute(select(Milestone).where(Milestone.id == mid))
    ).scalar_one()
    assert row.deleted_at is not None

    restore_resp = await client.post(
        f"/api/v1/milestones/{mid}/restore", headers=admin_hdr
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["deleted_at"] is None


# --- Unit-ish: is_deleted property ---


async def test_is_deleted_property(db_session: AsyncSession) -> None:
    project = Project(name="prop")
    db_session.add(project)
    await db_session.flush()
    assert project.is_deleted is False

    from sqlalchemy import func as sa_func

    project.deleted_at = sa_func.now()
    await db_session.flush()
    await db_session.refresh(project)
    assert project.is_deleted is True
