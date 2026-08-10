import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.roles import ROLE_HIERARCHY, UserRole
from app.core.security import (
    generate_api_key,
    split_api_key,
    verify_api_key,
)
from app.models.api_key import ApiKey
from app.models.mixins import not_deleted
from app.models.project import Project
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate

logger = logging.getLogger(__name__)


def _rank(role: str) -> int:
    try:
        return ROLE_HIERARCHY[UserRole(role)]
    except ValueError:
        return 0


def effective_role(key_role: str, owner_role: str) -> UserRole:
    """The role a request authenticated by this key actually gets.

    The weakest of the key's own role, its owner's *current* role, and the
    configured ceiling. Recomputed per request, so demoting a user immediately
    degrades every key they own — the role is never frozen at mint time.
    """
    weakest = min(
        (key_role, owner_role, settings.API_KEY_MAX_ROLE),
        key=_rank,
    )
    return UserRole(weakest)


async def _get_owner(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(
        select(User).where(User.id == user_id, not_deleted(User))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    return user


async def mint(
    db: AsyncSession,
    data: ApiKeyCreate,
    actor: User,
) -> tuple[ApiKey, str]:
    """Create an API key and return `(row, plaintext_key)`.

    The plaintext is returned once and never persisted. A key can never be
    stronger than its owner, nor than `API_KEY_MAX_ROLE`.
    """
    owner = actor
    if data.user_id is not None and data.user_id != actor.id:
        if _rank(actor.role) < ROLE_HIERARCHY[UserRole.LEAD]:
            raise ForbiddenError("Only leads and admins can mint keys for other users")
        owner = await _get_owner(db, data.user_id)
        if _rank(owner.role) > _rank(actor.role):
            raise ForbiddenError("Cannot mint a key for a user above your own role")

    if not owner.is_active:
        raise BadRequestError("Cannot mint a key for an inactive user")

    if _rank(data.role) > _rank(owner.role):
        raise BadRequestError(
            f"Cannot mint a '{data.role}' key for a '{owner.role}' user"
        )
    if _rank(data.role) > _rank(settings.API_KEY_MAX_ROLE):
        raise BadRequestError(
            f"API keys are capped at role '{settings.API_KEY_MAX_ROLE}'"
        )

    if data.project_id is not None:
        result = await db.execute(
            select(Project).where(
                Project.id == data.project_id, not_deleted(Project)
            )
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError(f"Project {data.project_id} not found")

    expires_at: datetime | None = None
    if not data.never_expires:
        days = data.expires_in_days or settings.API_KEY_DEFAULT_TTL_DAYS
        expires_at = datetime.now(UTC) + timedelta(days=days)

    full_key, key_prefix, key_hash = generate_api_key()
    api_key = ApiKey(
        name=data.name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        user_id=owner.id,
        project_id=data.project_id,
        role=str(data.role),
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    return api_key, full_key


async def list_keys(
    db: AsyncSession,
    actor: User,
    user_id: int | None = None,
    include_revoked: bool = False,
) -> list[ApiKey]:
    """List API keys. Non-admins only ever see their own."""
    target = actor.id
    if user_id is not None and user_id != actor.id:
        if UserRole(actor.role) != UserRole.ADMIN:
            raise ForbiddenError("Only admins can list another user's keys")
        target = user_id

    query = select(ApiKey).where(ApiKey.user_id == target)
    if not include_revoked:
        query = query.where(ApiKey.revoked_at.is_(None))
    result = await db.execute(query.order_by(ApiKey.created_at.desc()))
    return list(result.scalars().all())


async def revoke(db: AsyncSession, key_id: int, actor: User) -> None:
    """Revoke a key. Idempotent — revoking an already-revoked key is a no-op."""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise NotFoundError(f"API key {key_id} not found")

    if api_key.user_id != actor.id and UserRole(actor.role) != UserRole.ADMIN:
        # Same 404 as a missing key: whether someone else's key id exists is not
        # information this caller is entitled to.
        raise NotFoundError(f"API key {key_id} not found")

    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await db.flush()


async def resolve(db: AsyncSession, raw_key: str) -> tuple[ApiKey, User] | None:
    """Resolve a presented key to `(key, owner)`, or None if it is not usable.

    Returns None for every rejection reason — unknown prefix, wrong secret,
    revoked, expired, missing/inactive owner. The caller turns that into a 401;
    distinguishing the reasons to the client would confirm which prefixes exist.
    """
    split = split_api_key(raw_key)
    if split is None:
        return None
    key_prefix, secret = split

    result = await db.execute(select(ApiKey).where(ApiKey.key_prefix == key_prefix))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        return None
    if not verify_api_key(secret, api_key.key_hash):
        return None
    if api_key.revoked_at is not None:
        return None
    if api_key.expires_at is not None and _as_utc(api_key.expires_at) <= datetime.now(
        UTC
    ):
        return None

    owner_result = await db.execute(
        select(User).where(User.id == api_key.user_id, not_deleted(User))
    )
    owner = owner_result.scalar_one_or_none()
    if owner is None or not owner.is_active:
        return None
    if UserRole(owner.role) == UserRole.NO_ACCESS:
        return None

    return api_key, owner


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; Postgres hands back aware ones."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def touch_last_used(db: AsyncSession, api_key: ApiKey) -> None:
    """Best-effort `last_used_at` bump, throttled.

    An operator convenience for spotting stale keys, not an audit record — it
    must never cost a write per request on a busy pipeline.
    """
    now = datetime.now(UTC)
    if api_key.last_used_at is not None:
        elapsed = (now - _as_utc(api_key.last_used_at)).total_seconds()
        if elapsed < settings.API_KEY_LAST_USED_THROTTLE_SECONDS:
            return
    api_key.last_used_at = now
    await db.flush()
