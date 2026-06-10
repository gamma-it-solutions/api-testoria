from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.services import audit_service, email_outbox_service, password_token_service


def _link(path: str, token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/{path}?token={token}"


def _context(user: User, link: str) -> dict[str, object]:
    return {
        "username": user.username,
        "full_name": user.full_name,
        "link": link,
    }


async def queue_welcome_invite(db: AsyncSession, user: User) -> str:
    """Mint a set-password invite token and enqueue the welcome email.

    Runs inside the caller's transaction (same as the user INSERT) so the
    outbox row exists iff the user commits. Returns the token (for tests).
    """
    token = await password_token_service.create_token(
        user.id,
        password_token_service.PURPOSE_INVITE,
        settings.EMAIL_INVITE_TOKEN_TTL_SECONDS,
    )
    link = _link("set-password", token)
    await email_outbox_service.enqueue(
        db,
        to_email=user.email,
        template="welcome_invite",
        context=_context(user, link),
        subject="Welcome to Testoria — set your password",
    )
    await audit_service.log_action(
        db, user.id, "WELCOME_INVITE_SENT", "User", user.id
    )
    return token


async def queue_password_reset(db: AsyncSession, user: User) -> str:
    """Mint a reset token and enqueue the password-reset email."""
    token = await password_token_service.create_token(
        user.id,
        password_token_service.PURPOSE_RESET,
        settings.EMAIL_RESET_TOKEN_TTL_SECONDS,
    )
    link = _link("reset-password", token)
    await email_outbox_service.enqueue(
        db,
        to_email=user.email,
        template="password_reset",
        context=_context(user, link),
        subject="Reset your Testoria password",
    )
    return token
