import pytest
from httpx import AsyncClient
from jose import jwt

from app.config import settings
from app.models.user import User


@pytest.mark.asyncio
async def test_connection_token_success(
    client: AsyncClient, test_user: User, auth_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/websocket/connection-token", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data

    payload = jwt.decode(
        data["token"], settings.CENTRIFUGO_TOKEN_SECRET, algorithms=["HS256"]
    )
    assert payload["sub"] == str(test_user.id)
    assert "exp" in payload


@pytest.mark.asyncio
async def test_connection_token_unauthenticated(client: AsyncClient) -> None:
    response = await client.get("/api/v1/websocket/connection-token")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_subscription_tokens_success(
    client: AsyncClient, test_user: User, auth_headers: dict[str, str]
) -> None:
    channels = ["project:1", "testrun:5"]
    response = await client.post(
        "/api/v1/websocket/subscription-tokens",
        json={"channels": channels},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "tokens" in data
    assert set(data["tokens"].keys()) == set(channels)

    for channel in channels:
        payload = jwt.decode(
            data["tokens"][channel],
            settings.CENTRIFUGO_TOKEN_SECRET,
            algorithms=["HS256"],
        )
        assert payload["sub"] == str(test_user.id)
        assert payload["channel"] == channel
        assert "exp" in payload


@pytest.mark.asyncio
async def test_subscription_tokens_unauthenticated(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/websocket/subscription-tokens",
        json={"channels": ["project:1"]},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_subscription_tokens_empty_channels(
    client: AsyncClient, test_user: User, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/websocket/subscription-tokens",
        json={"channels": []},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["tokens"] == {}
