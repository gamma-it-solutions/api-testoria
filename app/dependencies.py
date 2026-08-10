from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ForbiddenError, UnauthorizedError
from app.core.roles import UserRole
from app.core.security import decode_token
from app.database import get_db
from app.models.user import User
from app.services import api_key_service

# auto_error=False on both: whether *neither* credential was supplied is decided
# in get_principal, so the error says "no credentials" rather than "no bearer
# token" to someone who sent a perfectly good API key.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Who is making this request, and what they are allowed to be.

    `role` is the *effective* role — for an API key that is the weakest of the
    key's role, its owner's current role, and `API_KEY_MAX_ROLE`, so it can be
    lower than `user.role`. Always authorise against this, never `user.role`.
    """

    user: User
    role: UserRole
    project_id: int | None
    via: Literal["jwt", "api_key"]
    api_key_id: int | None


async def get_principal(
    token: str | None = Depends(oauth2_scheme),
    raw_api_key: str | None = Security(api_key_scheme),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    if token and raw_api_key:
        # Never guess which one the caller meant.
        raise BadRequestError(
            "Send either an Authorization bearer token or an X-API-Key header, not both"
        )
    if raw_api_key:
        return await _principal_from_api_key(db, raw_api_key)
    if token:
        return await _principal_from_jwt(db, token)
    raise UnauthorizedError("Not authenticated")


async def _principal_from_jwt(db: AsyncSession, token: str) -> Principal:
    payload = decode_token(token, expected_type="access")
    user_id_raw = payload.get("sub")
    if user_id_raw is None:
        raise UnauthorizedError("Invalid token payload")

    user_id = int(str(user_id_raw))
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise UnauthorizedError("Inactive user")
    if user.role == UserRole.NO_ACCESS:
        raise ForbiddenError("Access denied")

    return Principal(
        user=user,
        role=UserRole(user.role),
        project_id=None,
        via="jwt",
        api_key_id=None,
    )


async def _principal_from_api_key(db: AsyncSession, raw_api_key: str) -> Principal:
    resolved = await api_key_service.resolve(db, raw_api_key)
    if resolved is None:
        raise UnauthorizedError("Invalid or revoked API key")
    api_key, owner = resolved
    await api_key_service.touch_last_used(db, api_key)

    return Principal(
        user=owner,
        role=api_key_service.effective_role(api_key.role, owner.role),
        project_id=api_key.project_id,
        via="api_key",
        api_key_id=api_key.id,
    )


async def get_current_user(principal: Principal = Depends(get_principal)) -> User:
    return principal.user


def require_role(*roles: UserRole) -> Callable[..., object]:
    async def checker(principal: Principal = Depends(get_principal)) -> User:
        if principal.role not in roles:
            raise ForbiddenError()
        return principal.user

    return checker


async def require_jwt(principal: Principal = Depends(get_principal)) -> Principal:
    """Reject API-key principals.

    Guards credential management: an API key that could mint or revoke keys
    would turn a leak from a revocable credential into a persistent foothold.
    """
    if principal.via != "jwt":
        raise ForbiddenError(
            "This endpoint requires an interactive login, not an API key"
        )
    return principal
