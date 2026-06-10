import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.core.security import get_password_hash
from app.models.email_outbox import EmailOutbox
from app.models.user import User


async def _mint_token_via_forgot(client: AsyncClient, email: str) -> str:
    """Trigger forgot-password and read back the token the fake Redis stored."""
    resp = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert resp.status_code == 202
    keys = await get_redis().keys("pwtoken:*")
    assert keys, "no reset token was minted"
    return keys[-1].split("pwtoken:", 1)[1]


@pytest.fixture
async def reset_target(db_session: AsyncSession) -> User:
    user = User(
        username="reset_target",
        email="reset_target@example.com",
        hashed_password=get_password_hash("oldpassword"),
        full_name="Reset Target",
        role="tester",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


# --- forgot-password (no enumeration) ---


@pytest.mark.asyncio
async def test_forgot_password_existing_user_returns_202(
    client: AsyncClient, reset_target: User
) -> None:
    resp = await client.post(
        "/api/v1/auth/forgot-password", json={"email": reset_target.email}
    )
    assert resp.status_code == 202
    assert "reset link" in resp.json()["message"].lower()
    keys = await get_redis().keys("pwtoken:*")
    assert len(keys) == 1  # a token was queued


@pytest.mark.asyncio
async def test_forgot_password_unknown_email_returns_202_no_token(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    assert resp.status_code == 202
    # Same message, and no token minted — indistinguishable from the hit case.
    assert await get_redis().keys("pwtoken:*") == []


@pytest.mark.asyncio
async def test_forgot_password_is_public(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "x@example.com"}
    )
    assert resp.status_code != 401


# --- reset-password ---


@pytest.mark.asyncio
async def test_reset_password_happy_path(
    client: AsyncClient, reset_target: User
) -> None:
    token = await _mint_token_via_forgot(client, reset_target.email)

    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "newpassword12"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Password updated."

    # New password works; old one no longer does.
    ok = await client.post(
        "/api/v1/auth/login",
        data={"username": "reset_target", "password": "newpassword12"},
    )
    assert ok.status_code == 200
    bad = await client.post(
        "/api/v1/auth/login",
        data={"username": "reset_target", "password": "oldpassword"},
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_reset_password_invalid_token_returns_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "not-a-real-token", "new_password": "newpassword12"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reset_password_token_is_single_use(
    client: AsyncClient, reset_target: User
) -> None:
    token = await _mint_token_via_forgot(client, reset_target.email)
    first = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "newpassword12"},
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "anotherpass34"},
    )
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_reset_password_weak_password_returns_422(
    client: AsyncClient, reset_target: User
) -> None:
    token = await _mint_token_via_forgot(client, reset_target.email)
    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "short"},
    )
    assert resp.status_code == 422
    # Rejected before the token was consumed — it is still valid.
    assert await get_redis().keys("pwtoken:*")


# --- validate ---


@pytest.mark.asyncio
async def test_validate_token_returns_username(
    client: AsyncClient, reset_target: User
) -> None:
    token = await _mint_token_via_forgot(client, reset_target.email)
    resp = await client.get(
        "/api/v1/auth/reset-password/validate", params={"token": token}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["username"] == "reset_target"
    # validate peeks — the token is still consumable afterwards.
    assert await get_redis().keys("pwtoken:*")


@pytest.mark.asyncio
async def test_validate_invalid_token_returns_400(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/auth/reset-password/validate", params={"token": "nope"}
    )
    assert resp.status_code == 400


# --- creation enqueues welcome invites through the outbox ---


@pytest.mark.asyncio
async def test_bulk_create_enqueues_one_row_per_user(
    client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    payload = {
        "users": [
            {"username": f"bulk_u{i}", "email": f"bulk_u{i}@example.com"}
            for i in range(3)
        ]
    }
    resp = await client.post(
        "/api/v1/users/bulk", json=payload, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 3

    # Scope the count to this test's rows so it doesn't depend on welcome-invite
    # rows other tests may have enqueued.
    count = await db_session.execute(
        select(func.count())
        .select_from(EmailOutbox)
        .where(
            EmailOutbox.template == "welcome_invite",
            EmailOutbox.to_email.in_(
                [f"bulk_u{i}@example.com" for i in range(3)]
            ),
        )
    )
    assert count.scalar_one() == 3


@pytest.mark.asyncio
async def test_create_user_enqueues_welcome_invite(
    client: AsyncClient, db_session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    # Creation is invite-only: no password is accepted; a welcome set-password
    # invite is enqueued instead.
    resp = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "no_pw_user", "email": "no_pw@example.com"},
    )
    assert resp.status_code == 201
    row = await db_session.execute(
        select(EmailOutbox).where(EmailOutbox.to_email == "no_pw@example.com")
    )
    assert row.scalar_one().template == "welcome_invite"
