import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="tag_tester",
        email="tag_tester@example.com",
        hashed_password=get_password_hash("password"),
        role="tester",
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


async def test_list_tags_empty(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    resp = await client.get("/api/v1/tags", headers=tester_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_tags_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tags")
    assert resp.status_code == 401


async def test_create_tag_returns_201_when_new(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    resp = await client.post(
        "/api/v1/tags", json={"name": "alpha"}, headers=tester_headers
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "alpha"
    assert "id" in body


async def test_create_tag_idempotent_returns_200(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    first = await client.post(
        "/api/v1/tags", json={"name": "beta"}, headers=tester_headers
    )
    second = await client.post(
        "/api/v1/tags", json={"name": "BETA"}, headers=tester_headers
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


async def test_create_tag_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/tags", json={"name": "x"})
    assert resp.status_code == 401


async def test_search_tags_prefix(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    for name in ["login", "logout", "smoke"]:
        await client.post(
            "/api/v1/tags", json={"name": name}, headers=tester_headers
        )
    resp = await client.get(
        "/api/v1/tags", params={"q": "log"}, headers=tester_headers
    )
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert "login" in names and "logout" in names
    assert "smoke" not in names
