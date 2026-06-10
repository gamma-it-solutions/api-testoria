"""Integration tests for audit logging."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


@pytest.mark.asyncio
async def test_login_creates_audit_entry(
    client: AsyncClient,
    test_user: None,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "LOGIN",
            AuditLog.entity_type == "User",
        )
    )
    entry = result.scalar_one_or_none()
    assert entry is not None
    assert entry.action == "LOGIN"
    assert entry.entity_type == "User"


@pytest.mark.asyncio
async def test_logout_creates_audit_entry(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    response = await client.post("/api/v1/auth/logout", headers=auth_headers)
    assert response.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "LOGOUT",
            AuditLog.entity_type == "User",
        )
    )
    entry = result.scalar_one_or_none()
    assert entry is not None


@pytest.mark.asyncio
async def test_project_create_creates_audit_entry(
    client: AsyncClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"name": "Audit Test Project"},
    )
    assert response.status_code == 201
    project_id = response.json()["id"]

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "CREATE",
            AuditLog.entity_type == "Project",
            AuditLog.entity_id == project_id,
        )
    )
    entry = result.scalar_one_or_none()
    assert entry is not None
    assert entry.entity_id == project_id


@pytest.mark.asyncio
async def test_project_update_creates_audit_entry(
    client: AsyncClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    create_resp = await client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"name": "Audit Update Project"},
    )
    assert create_resp.status_code == 201
    project_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/v1/projects/{project_id}",
        headers=admin_headers,
        json={"name": "Audit Update Project Renamed"},
    )
    assert update_resp.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "UPDATE",
            AuditLog.entity_type == "Project",
            AuditLog.entity_id == project_id,
        )
    )
    entry = result.scalar_one_or_none()
    assert entry is not None
    assert entry.changes is not None
    assert "name" in entry.changes


@pytest.mark.asyncio
async def test_project_delete_creates_audit_entry(
    client: AsyncClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    create_resp = await client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"name": "Audit Delete Project"},
    )
    assert create_resp.status_code == 201
    project_id = create_resp.json()["id"]

    delete_resp = await client.delete(
        f"/api/v1/projects/{project_id}", headers=admin_headers
    )
    assert delete_resp.status_code == 204

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "DELETE",
            AuditLog.entity_type == "Project",
            AuditLog.entity_id == project_id,
        )
    )
    entry = result.scalar_one_or_none()
    assert entry is not None


@pytest.mark.asyncio
async def test_audit_entry_has_user_id(
    client: AsyncClient,
    admin_user: None,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"name": "Audit User ID Project"},
    )
    assert response.status_code == 201

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "CREATE",
            AuditLog.entity_type == "Project",
        )
    )
    entry = result.scalar_one_or_none()
    assert entry is not None
    assert entry.user_id is not None
