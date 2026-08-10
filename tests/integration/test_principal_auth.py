"""Both credential types must behave identically where they overlap, and the
API key must be strictly weaker where they do not.

`get_current_user` and `require_role` were rewired onto `get_principal` — these
tests are the guard on that change.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


async def _make_user(db: AsyncSession, username: str, role: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=get_password_hash("password"),
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def principal_admin(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "prin_admin", "admin")


@pytest.fixture
def admin_jwt(principal_admin: User) -> dict[str, str]:
    token = create_access_token({"sub": str(principal_admin.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_api_key(client: AsyncClient, admin_jwt: dict[str, str]) -> str:
    response = await client.post(
        "/api/v1/api-keys", json={"name": "ci"}, headers=admin_jwt
    )
    assert response.status_code == 201, response.text
    key: str = response.json()["key"]
    return key


async def test_no_credentials_is_401(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_both_credentials_at_once_is_400(
    client: AsyncClient, admin_jwt: dict[str, str], admin_api_key: str
) -> None:
    """Never guess which one the caller meant."""
    response = await client.get(
        "/api/v1/auth/me", headers={**admin_jwt, "X-API-Key": admin_api_key}
    )
    assert response.status_code == 400


async def test_jwt_and_api_key_resolve_to_the_same_user(
    client: AsyncClient, admin_jwt: dict[str, str], admin_api_key: str
) -> None:
    via_jwt = await client.get("/api/v1/auth/me", headers=admin_jwt)
    via_key = await client.get("/api/v1/auth/me", headers={"X-API-Key": admin_api_key})

    assert via_jwt.status_code == via_key.status_code == 200
    assert via_jwt.json()["id"] == via_key.json()["id"]


async def test_api_key_is_capped_below_its_admin_owner(
    client: AsyncClient, admin_jwt: dict[str, str], admin_api_key: str
) -> None:
    """The owner is an admin; the key must still not reach an admin route.

    This is the whole containment argument — no per-route allowlist, just the
    effective-role cap meeting the existing require_role gates.
    """
    via_jwt = await client.get("/api/v1/users", headers=admin_jwt)
    via_key = await client.get("/api/v1/users", headers={"X-API-Key": admin_api_key})

    assert via_jwt.status_code == 200
    assert via_key.status_code == 403


async def test_api_key_cannot_create_a_project(
    client: AsyncClient, admin_api_key: str
) -> None:
    """Project creation is lead/admin — closed to keys by the same mechanism."""
    response = await client.post(
        "/api/v1/projects",
        json={"name": "should not exist"},
        headers={"X-API-Key": admin_api_key},
    )
    assert response.status_code == 403


async def test_garbage_api_key_is_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me", headers={"X-API-Key": "nonsense"})
    assert response.status_code == 401


async def test_wellformed_but_unknown_api_key_is_401(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"X-API-Key": "tsk_deadbeef_" + "x" * 43}
    )
    assert response.status_code == 401


async def test_expired_jwt_still_401s(client: AsyncClient) -> None:
    """Regression guard: JWT rejection must survive the rewiring."""
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert response.status_code == 401


async def test_inactive_owner_disables_the_key(
    client: AsyncClient,
    db_session: AsyncSession,
    principal_admin: User,
    admin_api_key: str,
) -> None:
    key_headers = {"X-API-Key": admin_api_key}
    assert (await client.get("/api/v1/auth/me", headers=key_headers)).status_code == 200

    principal_admin.is_active = False
    await db_session.flush()

    assert (await client.get("/api/v1/auth/me", headers=key_headers)).status_code == 401


async def test_demoting_the_owner_degrades_the_key(
    client: AsyncClient,
    db_session: AsyncSession,
    principal_admin: User,
    admin_api_key: str,
) -> None:
    """Effective role is recomputed per request, never frozen at mint time."""
    key_headers = {"X-API-Key": admin_api_key}
    first = await client.get("/api/v1/projects", headers=key_headers)
    assert first.status_code == 200

    principal_admin.role = "no_access"
    await db_session.flush()

    after = await client.get("/api/v1/projects", headers=key_headers)
    assert after.status_code == 401
