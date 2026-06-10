"""Integration tests for RBAC permission enforcement."""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ROLE_PERMISSIONS, Permission, has_permission
from app.core.roles import UserRole
from app.core.security import create_access_token, get_password_hash
from app.models.project import Project
from app.models.user import User


@pytest_asyncio.fixture
async def read_only_user(db_session: AsyncSession) -> User:
    user = User(
        username="perm_readonly",
        email="perm_readonly@example.com",
        hashed_password=get_password_hash("password"),
        role=UserRole.READ_ONLY,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="perm_tester",
        email="perm_tester@example.com",
        hashed_password=get_password_hash("password"),
        role=UserRole.TESTER,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sample_project(db_session: AsyncSession) -> Project:
    project = Project(name="Perm Test Project", description="For permission tests")
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest.fixture
def read_only_headers(read_only_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(read_only_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tester_headers(tester_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(tester_user.id)})
    return {"Authorization": f"Bearer {token}"}


# --- Unit tests for has_permission ---


def test_admin_has_all_permissions(admin_user: User) -> None:
    for permission in Permission:
        assert has_permission(admin_user, permission)


def test_read_only_can_view_but_not_edit(read_only_user: User) -> None:
    assert has_permission(read_only_user, Permission.VIEW_PROJECT)
    assert has_permission(read_only_user, Permission.VIEW_REPORTS)
    assert not has_permission(read_only_user, Permission.CREATE_TEST_CASE)
    assert not has_permission(read_only_user, Permission.DELETE_PROJECT)
    assert not has_permission(read_only_user, Permission.MANAGE_USERS)


def test_tester_can_execute_but_not_delete_project(tester_user: User) -> None:
    assert has_permission(tester_user, Permission.EXECUTE_TEST)
    assert has_permission(tester_user, Permission.CREATE_TEST_CASE)
    assert not has_permission(tester_user, Permission.DELETE_PROJECT)
    assert not has_permission(tester_user, Permission.MANAGE_USERS)


def test_role_permissions_completeness() -> None:
    """Every UserRole must have an entry in ROLE_PERMISSIONS."""
    for role in UserRole:
        assert role in ROLE_PERMISSIONS


# --- Endpoint permission tests ---


@pytest.mark.asyncio
async def test_read_only_cannot_create_project(
    client: AsyncClient,
    read_only_user: User,
    read_only_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/projects",
        headers=read_only_headers,
        json={"name": "Should Fail"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_read_only_can_list_projects(
    client: AsyncClient,
    read_only_user: User,
    read_only_headers: dict[str, str],
) -> None:
    response = await client.get("/api/v1/projects", headers=read_only_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_tester_cannot_delete_project(
    client: AsyncClient,
    tester_user: User,
    tester_headers: dict[str, str],
    sample_project: Project,
) -> None:
    response = await client.delete(
        f"/api/v1/projects/{sample_project.id}", headers=tester_headers
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_delete_project(
    client: AsyncClient,
    admin_user: User,
    admin_headers: dict[str, str],
    sample_project: Project,
) -> None:
    response = await client.delete(
        f"/api/v1/projects/{sample_project.id}", headers=admin_headers
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_tester_cannot_manage_users(
    client: AsyncClient,
    tester_user: User,
    tester_headers: dict[str, str],
) -> None:
    response = await client.get("/api/v1/users", headers=tester_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.post("/api/v1/projects", json={"name": "No Auth"})
    assert response.status_code == 401
