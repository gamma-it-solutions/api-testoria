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
async def key_tester(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "key_tester", "tester")


@pytest_asyncio.fixture
async def key_admin(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "key_admin", "admin")


@pytest.fixture
def tester_headers(key_tester: User) -> dict[str, str]:
    token = create_access_token({"sub": str(key_tester.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_jwt_headers(key_admin: User) -> dict[str, str]:
    token = create_access_token({"sub": str(key_admin.id)})
    return {"Authorization": f"Bearer {token}"}


async def _mint(client: AsyncClient, headers: dict[str, str], **body: object) -> dict:
    payload = {"name": "ci"} | body
    response = await client.post("/api/v1/api-keys", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_mint_returns_the_key_once(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    created = await _mint(client, tester_headers)

    assert created["key"].startswith("tsk_")
    assert created["key_prefix"] in created["key"]

    listed = await client.get("/api/v1/api-keys", headers=tester_headers)
    assert listed.status_code == 200
    # The secret is never retrievable again.
    assert all("key" not in row for row in listed.json())


async def test_mint_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/api/v1/api-keys", json={"name": "ci"})
    assert response.status_code == 401


async def test_an_api_key_cannot_mint_another_api_key(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    """The line between a revocable credential and a persistent foothold."""
    created = await _mint(client, tester_headers)

    response = await client.post(
        "/api/v1/api-keys",
        json={"name": "second"},
        headers={"X-API-Key": created["key"]},
    )

    assert response.status_code == 403


async def test_an_api_key_cannot_revoke_a_key(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    created = await _mint(client, tester_headers)

    response = await client.delete(
        f"/api/v1/api-keys/{created['id']}",
        headers={"X-API-Key": created["key"]},
    )

    assert response.status_code == 403


async def test_an_api_key_cannot_list_keys(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    created = await _mint(client, tester_headers)

    response = await client.get(
        "/api/v1/api-keys", headers={"X-API-Key": created["key"]}
    )

    assert response.status_code == 403


async def test_revoked_key_stops_working_immediately(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    created = await _mint(client, tester_headers)
    key_headers = {"X-API-Key": created["key"]}

    before = await client.get("/api/v1/auth/me", headers=key_headers)
    assert before.status_code == 200

    revoked = await client.delete(
        f"/api/v1/api-keys/{created['id']}", headers=tester_headers
    )
    assert revoked.status_code == 204

    after = await client.get("/api/v1/auth/me", headers=key_headers)
    assert after.status_code == 401


async def test_revoke_unknown_key_404s(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    response = await client.delete("/api/v1/api-keys/987654", headers=tester_headers)
    assert response.status_code == 404


async def test_cannot_mint_above_the_configured_cap(
    client: AsyncClient, admin_jwt_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/api-keys",
        json={"name": "root", "role": "admin"},
        headers=admin_jwt_headers,
    )
    assert response.status_code == 400


async def test_listing_hides_other_users_keys(
    client: AsyncClient,
    tester_headers: dict[str, str],
    admin_jwt_headers: dict[str, str],
) -> None:
    await _mint(client, tester_headers, name="mine")
    await _mint(client, admin_jwt_headers, name="theirs")

    listed = await client.get("/api/v1/api-keys", headers=tester_headers)

    assert [row["name"] for row in listed.json()] == ["mine"]
