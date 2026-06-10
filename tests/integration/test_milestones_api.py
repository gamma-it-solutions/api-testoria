import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="ms_lead",
        email="ms_lead@example.com",
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
        username="ms_readonly",
        email="ms_readonly@example.com",
        hashed_password=get_password_hash("password"),
        role="read_only",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


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
        "/api/v1/projects", json={"name": "MS Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


# --- GET /projects/{id}/milestones ---


async def test_list_milestones_empty(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/api/v1/projects/{project_id}/milestones", headers=lead_headers
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_list_milestones_no_token(client: AsyncClient, project_id: int) -> None:
    response = await client.get(f"/api/v1/projects/{project_id}/milestones")
    assert response.status_code == 401


# --- POST /projects/{id}/milestones ---


async def test_create_milestone(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/milestones",
        json={
            "name": "v1.0 Release",
            "description": "First release",
            "target_date": "2026-06-01",
        },
        headers=lead_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "v1.0 Release"
    assert data["project_id"] == project_id
    assert data["is_completed"] is False
    assert data["target_date"] == "2026-06-01"


async def test_create_milestone_read_only_forbidden(
    client: AsyncClient, project_id: int, read_only_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/milestones",
        json={"name": "Blocked"},
        headers=read_only_headers,
    )
    assert response.status_code == 403


# --- PUT /milestones/{id} ---


async def test_update_milestone(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/milestones",
        json={"name": "Old Name"},
        headers=lead_headers,
    )
    ms_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/milestones/{ms_id}",
        json={"name": "New Name", "is_completed": True},
        headers=lead_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Name"
    assert data["is_completed"] is True


async def test_update_milestone_not_found(
    client: AsyncClient, lead_headers: dict[str, str]
) -> None:
    response = await client.put(
        "/api/v1/milestones/999999",
        json={"name": "X"},
        headers=lead_headers,
    )
    assert response.status_code == 404


# --- DELETE /milestones/{id} ---


async def test_delete_milestone(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/milestones",
        json={"name": "ToDelete"},
        headers=lead_headers,
    )
    ms_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/milestones/{ms_id}", headers=lead_headers)
    assert response.status_code == 204


async def test_list_milestones_after_create(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    await client.post(
        f"/api/v1/projects/{project_id}/milestones",
        json={"name": "Listed"},
        headers=lead_headers,
    )
    response = await client.get(
        f"/api/v1/projects/{project_id}/milestones", headers=lead_headers
    )
    assert response.status_code == 200
    names = [m["name"] for m in response.json()]
    assert "Listed" in names
