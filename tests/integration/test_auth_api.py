import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, test_user: User) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, test_user: User) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "wrongpassword"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "nobody", "password": "whatever"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user(
    client: AsyncClient, test_user: User, db_session: AsyncSession
) -> None:
    test_user.is_active = False
    await db_session.flush()

    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 400

    test_user.is_active = True
    await db_session.flush()


@pytest.mark.asyncio
async def test_refresh_token(client: AsyncClient, test_user: User) -> None:
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpassword"},
    )
    refresh_token = login.json()["refresh_token"]

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_refresh_with_access_token_fails(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    access_token = auth_headers["Authorization"].removeprefix("Bearer ")
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access_token},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me(
    client: AsyncClient, test_user: User, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert data["email"] == "testuser@example.com"
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_protected_endpoint_without_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout(
    client: AsyncClient, test_user: User, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/api/v1/auth/logout", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["message"] == "Successfully logged out"
