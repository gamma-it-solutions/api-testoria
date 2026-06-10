import csv
import io
import secrets
from collections.abc import AsyncGenerator

import openpyxl
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.roles import UserRole
from app.core.security import get_password_hash
from app.models.mixins import not_deleted
from app.models.user import User
from app.schemas.user import (
    BulkCreateError,
    UserBulkCreate,
    UserBulkCreateResult,
    UserCreate,
    UserListFilters,
    UserUpdate,
)
from app.services import email_service


def _unusable_password_hash() -> str:
    """Hash an unguessable random password.

    Creation is invite-only: the column stays NOT NULL while login is
    impossible until the user sets a password through the welcome invite link.
    """
    return get_password_hash(secrets.token_urlsafe(32))


def _conflict_detail(existing: User, username: str, email: str) -> str:
    """Build a specific 'already taken' message naming the colliding field."""
    if existing.email == email:
        return f"Email '{email}' is already taken"
    if existing.username == username:
        return f"Username '{username}' is already taken"
    return "Username or email already taken"


def _assert_can_manage_role(actor: User | None, target_role: UserRole) -> None:
    """Forbid a non-admin actor from creating or elevating a user to Admin.

    `actor is None` means an internal/trusted caller (seed scripts, token
    flows) and is unrestricted; the router always passes the authenticated
    user, so every externally reachable path is gated.
    """
    if actor is None or actor.role == UserRole.ADMIN:
        return
    if target_role == UserRole.ADMIN:
        raise ForbiddenError("Only an admin can manage admin users")


def _assert_can_manage_user(actor: User | None, target: User) -> None:
    """Forbid a non-admin actor from modifying or deleting an existing Admin."""
    if actor is None or actor.role == UserRole.ADMIN:
        return
    if target.role == UserRole.ADMIN:
        raise ForbiddenError("Only an admin can manage admin users")

_EXPORT_COLUMNS = [
    "id",
    "username",
    "email",
    "full_name",
    "role",
    "is_active",
    "created_at",
]


def _build_list_query(filters: UserListFilters) -> Select[tuple[User]]:
    query = select(User).where(not_deleted(User))

    if filters.search:
        term = f"%{filters.search}%"
        query = query.where(
            or_(
                User.username.ilike(term),
                User.email.ilike(term),
                User.full_name.ilike(term),
            )
        )

    if filters.status is not None:
        query = query.where(User.is_active == (filters.status == "active"))

    if filters.role is not None:
        query = query.where(User.role == filters.role)

    return query


async def create_user(
    db: AsyncSession, data: UserCreate, actor: User | None = None
) -> User:
    _assert_can_manage_role(actor, data.role)

    existing = (
        await db.execute(
            select(User).where(
                or_(User.username == data.username, User.email == data.email)
            )
        )
    ).scalars().first()
    if existing is not None:
        raise ConflictError(_conflict_detail(existing, data.username, data.email))

    user = User(
        username=data.username,
        email=data.email,
        hashed_password=_unusable_password_hash(),
        full_name=data.full_name,
        role=data.role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    # Enqueue the welcome set-password invite in the same transaction, so the
    # email exists iff the user commits. Creation is always invite-only.
    await email_service.queue_welcome_invite(db, user)
    return user


async def get_user(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(
        select(User).where(User.id == user_id, not_deleted(User))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    return user


async def list_users(
    db: AsyncSession, filters: UserListFilters
) -> tuple[list[User], int]:
    base_query = _build_list_query(filters)

    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total: int = count_result.scalar_one()

    offset = (filters.page - 1) * filters.page_size
    result = await db.execute(
        base_query.order_by(User.created_at.desc())
        .offset(offset)
        .limit(filters.page_size)
    )
    users = list(result.scalars().all())
    return users, total


async def update_user(
    db: AsyncSession, user_id: int, data: UserUpdate, actor: User | None = None
) -> User:
    user = await get_user(db, user_id)
    _assert_can_manage_user(actor, user)
    if data.role is not None:
        _assert_can_manage_role(actor, data.role)

    if data.email is not None:
        user.email = data.email
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.hashed_password = get_password_hash(data.password)

    await db.flush()
    await db.refresh(user)
    return user


async def set_password(db: AsyncSession, user_id: int, new_password: str) -> User:
    """Set a user's password from a consumed reset/invite token.

    Raises BadRequestError (not 404) if the user no longer exists or is
    inactive, so the reset endpoint never reveals account state.
    """
    result = await db.execute(
        select(User).where(User.id == user_id, not_deleted(User))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise BadRequestError("Invalid or expired token")
    user.hashed_password = get_password_hash(new_password)
    await db.flush()
    await db.refresh(user)
    return user


async def delete_user(
    db: AsyncSession, user_id: int, actor: User | None = None
) -> None:
    user = await get_user(db, user_id)
    _assert_can_manage_user(actor, user)
    if user.role == UserRole.LEAD:
        raise ConflictError("Cannot delete a user with the Lead role")
    user.deleted_at = func.now()
    await db.flush()


async def bulk_create_users(
    db: AsyncSession, data: UserBulkCreate, actor: User | None = None
) -> UserBulkCreateResult:
    created = 0
    errors: list[BulkCreateError] = []

    for i, user_data in enumerate(data.users):
        try:
            async with db.begin_nested():
                _assert_can_manage_role(actor, user_data.role)
                existing = (
                    await db.execute(
                        select(User).where(
                            or_(
                                User.username == user_data.username,
                                User.email == user_data.email,
                            )
                        )
                    )
                ).scalars().first()
                if existing is not None:
                    raise ConflictError(
                        _conflict_detail(
                            existing, user_data.username, user_data.email
                        )
                    )

                user = User(
                    username=user_data.username,
                    email=user_data.email,
                    hashed_password=_unusable_password_hash(),
                    full_name=user_data.full_name,
                    role=user_data.role,
                )
                db.add(user)
                await db.flush()
                await db.refresh(user)
                await email_service.queue_welcome_invite(db, user)
            created += 1
        except ConflictError as exc:
            errors.append(
                BulkCreateError(
                    index=i,
                    username=user_data.username,
                    email=user_data.email,
                    detail=str(exc.detail),
                )
            )
        except Exception as exc:
            errors.append(
                BulkCreateError(
                    index=i,
                    username=user_data.username,
                    email=user_data.email,
                    detail=str(exc),
                )
            )

        if data.strict and errors:
            break

    return UserBulkCreateResult(created=created, errors=errors)


async def export_users_csv(db: AsyncSession) -> AsyncGenerator[str, None]:
    result = await db.execute(
        select(User).where(not_deleted(User)).order_by(User.created_at)
    )
    users = list(result.scalars().all())

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_COLUMNS)
    yield buf.getvalue()

    for user in users:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                user.id,
                user.username,
                user.email,
                user.full_name,
                user.role,
                user.is_active,
                user.created_at.isoformat(),
            ]
        )
        yield buf.getvalue()


async def export_users_excel(db: AsyncSession) -> bytes:
    result = await db.execute(
        select(User).where(not_deleted(User)).order_by(User.created_at)
    )
    users = list(result.scalars().all())

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Users"
    ws.append(_EXPORT_COLUMNS)
    for user in users:
        ws.append(
            [
                user.id,
                user.username,
                user.email,
                user.full_name,
                user.role,
                user.is_active,
                str(user.created_at),
            ]
        )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
