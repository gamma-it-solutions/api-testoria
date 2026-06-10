import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.models.email_outbox import EmailOutbox
from app.models.user import User
from app.services import email_service, password_token_service


async def _make_user(db: AsyncSession, **over: object) -> User:
    kwargs: dict[str, object] = {
        "username": "jdoe",
        "email": "jdoe@example.com",
        "hashed_password": get_password_hash("x"),
        "full_name": "Jane Doe",
        "role": "tester",
        "is_active": True,
    }
    kwargs.update(over)
    user = User(**kwargs)  # type: ignore[arg-type]
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_queue_welcome_invite_enqueues_row_and_token(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, email="welcome@example.com")
    token = await email_service.queue_welcome_invite(db_session, user)

    # Token resolves to this user with the invite purpose.
    peeked = await password_token_service.peek_token(token)
    assert peeked == (user.id, password_token_service.PURPOSE_INVITE)

    result = await db_session.execute(
        select(EmailOutbox).where(EmailOutbox.to_email == "welcome@example.com")
    )
    row = result.scalar_one()
    assert row.template == "welcome_invite"
    assert row.context["username"] == "jdoe"
    assert f"set-password?token={token}" in row.context["link"]


@pytest.mark.asyncio
async def test_queue_password_reset_builds_reset_link(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, username="reset_u", email="reset@example.com")
    token = await email_service.queue_password_reset(db_session, user)

    peeked = await password_token_service.peek_token(token)
    assert peeked == (user.id, password_token_service.PURPOSE_RESET)

    result = await db_session.execute(
        select(EmailOutbox).where(EmailOutbox.to_email == "reset@example.com")
    )
    row = result.scalar_one()
    assert row.template == "password_reset"
    assert f"reset-password?token={token}" in row.context["link"]


@pytest.mark.asyncio
async def test_welcome_invite_writes_audit_log(db_session: AsyncSession) -> None:
    from app.models.audit_log import AuditLog

    user = await _make_user(db_session, username="aud", email="aud@example.com")
    await email_service.queue_welcome_invite(db_session, user)

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "WELCOME_INVITE_SENT")
    )
    entry = result.scalar_one()
    assert entry.user_id == user.id
