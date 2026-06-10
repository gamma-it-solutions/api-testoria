import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.roles import UserRole
from app.core.security import get_password_hash
from app.models.user import User
from app.schemas.user import UserBulkCreate, UserCreate, UserListFilters, UserUpdate
from app.services import user_service


@pytest_asyncio.fixture
async def admin(db_session: AsyncSession) -> User:
    user = User(
        username="svc_admin",
        email="svc_admin@example.com",
        hashed_password=get_password_hash("password"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="svc_lead",
        email="svc_lead@example.com",
        hashed_password=get_password_hash("password"),
        role=UserRole.LEAD,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


# --- create_user ---


@pytest.mark.asyncio
async def test_create_user_success(db_session: AsyncSession) -> None:
    data = UserCreate(
        username="new_svc_user",
        email="new_svc_user@example.com",
        role=UserRole.TESTER,
    )
    user = await user_service.create_user(db_session, data)

    assert user.id is not None
    assert user.username == "new_svc_user"
    assert user.email == "new_svc_user@example.com"
    assert user.role == UserRole.TESTER
    # Invite-only: an unusable random password is set, login impossible until invite.
    assert user.hashed_password


@pytest.mark.asyncio
async def test_create_user_duplicate_username(
    db_session: AsyncSession, admin: User
) -> None:
    data = UserCreate(
        username="svc_admin",
        email="different@example.com",
    )
    with pytest.raises(ConflictError):
        await user_service.create_user(db_session, data)


@pytest.mark.asyncio
async def test_create_user_duplicate_email(
    db_session: AsyncSession, admin: User
) -> None:
    data = UserCreate(
        username="different_name",
        email="svc_admin@example.com",
    )
    with pytest.raises(ConflictError):
        await user_service.create_user(db_session, data)


@pytest.mark.asyncio
async def test_create_user_default_role(db_session: AsyncSession) -> None:
    data = UserCreate(
        username="default_role_user",
        email="default_role@example.com",
    )
    user = await user_service.create_user(db_session, data)
    assert user.role == UserRole.LEAD


# --- get_user ---


@pytest.mark.asyncio
async def test_get_user_success(db_session: AsyncSession, admin: User) -> None:
    fetched = await user_service.get_user(db_session, admin.id)
    assert fetched.id == admin.id
    assert fetched.username == "svc_admin"


@pytest.mark.asyncio
async def test_get_user_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await user_service.get_user(db_session, 999999)


# --- list_users ---


@pytest.mark.asyncio
async def test_list_users_no_filters(db_session: AsyncSession, admin: User) -> None:
    filters = UserListFilters()
    users, total = await user_service.list_users(db_session, filters)
    assert total >= 1
    assert any(u.id == admin.id for u in users)


@pytest.mark.asyncio
async def test_list_users_search(db_session: AsyncSession, admin: User) -> None:
    filters = UserListFilters(search="svc_admin")
    users, total = await user_service.list_users(db_session, filters)
    assert total >= 1
    assert all("svc_admin" in (u.username + u.email) for u in users)


@pytest.mark.asyncio
async def test_list_users_filter_by_role(
    db_session: AsyncSession, admin: User, lead_user: User
) -> None:
    filters = UserListFilters(role=UserRole.ADMIN)
    users, total = await user_service.list_users(db_session, filters)
    assert all(u.role == UserRole.ADMIN for u in users)


@pytest.mark.asyncio
async def test_list_users_filter_by_status(
    db_session: AsyncSession, admin: User
) -> None:
    filters = UserListFilters(status="active")
    users, total = await user_service.list_users(db_session, filters)
    assert all(u.is_active for u in users)


@pytest.mark.asyncio
async def test_list_users_pagination(db_session: AsyncSession) -> None:
    filters = UserListFilters(page=1, page_size=1)
    users, total = await user_service.list_users(db_session, filters)
    assert len(users) <= 1


# --- update_user ---


@pytest.mark.asyncio
async def test_update_user_success(db_session: AsyncSession, admin: User) -> None:
    data = UserUpdate(full_name="Updated Name", role=UserRole.READ_ONLY)
    updated = await user_service.update_user(db_session, admin.id, data)
    assert updated.full_name == "Updated Name"
    assert updated.role == UserRole.READ_ONLY


@pytest.mark.asyncio
async def test_update_user_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await user_service.update_user(db_session, 999999, UserUpdate(full_name="X"))


@pytest.mark.asyncio
async def test_update_user_partial(db_session: AsyncSession, admin: User) -> None:
    original_email = admin.email
    data = UserUpdate(full_name="Only Name Changed")
    updated = await user_service.update_user(db_session, admin.id, data)
    assert updated.full_name == "Only Name Changed"
    assert updated.email == original_email


@pytest.mark.asyncio
async def test_update_user_password(db_session: AsyncSession, admin: User) -> None:
    from app.core.security import verify_password

    original_hash = admin.hashed_password
    data = UserUpdate(password="new-secret-pw")
    updated = await user_service.update_user(db_session, admin.id, data)
    assert updated.hashed_password != original_hash
    assert verify_password("new-secret-pw", updated.hashed_password)


@pytest.mark.asyncio
async def test_update_user_without_password_preserves_hash(
    db_session: AsyncSession, admin: User
) -> None:
    original_hash = admin.hashed_password
    data = UserUpdate(full_name="Different Name")
    updated = await user_service.update_user(db_session, admin.id, data)
    assert updated.hashed_password == original_hash


# --- delete_user ---


@pytest.mark.asyncio
async def test_delete_user_success(db_session: AsyncSession) -> None:
    user = User(
        username="to_delete",
        email="to_delete@example.com",
        hashed_password=get_password_hash("p"),
        role=UserRole.TESTER,
    )
    db_session.add(user)
    await db_session.flush()
    user_id = user.id

    await user_service.delete_user(db_session, user_id)

    with pytest.raises(NotFoundError):
        await user_service.get_user(db_session, user_id)


@pytest.mark.asyncio
async def test_delete_user_lead_blocked(
    db_session: AsyncSession, lead_user: User
) -> None:
    with pytest.raises(ConflictError, match="Lead"):
        await user_service.delete_user(db_session, lead_user.id)


@pytest.mark.asyncio
async def test_delete_user_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await user_service.delete_user(db_session, 999999)


# --- bulk_create_users ---


@pytest.mark.asyncio
async def test_bulk_create_all_success(db_session: AsyncSession) -> None:
    data = UserBulkCreate(
        users=[
            UserCreate(username="bulk1", email="bulk1@example.com"),
            UserCreate(username="bulk2", email="bulk2@example.com"),
        ]
    )
    result = await user_service.bulk_create_users(db_session, data)
    assert result.created == 2
    assert result.errors == []


@pytest.mark.asyncio
async def test_bulk_create_partial_failure(
    db_session: AsyncSession, admin: User
) -> None:
    data = UserBulkCreate(
        users=[
            UserCreate(username="bulk_ok", email="bulk_ok@example.com"),
            # duplicate of existing admin
            UserCreate(username="svc_admin", email="other@example.com"),
        ]
    )
    result = await user_service.bulk_create_users(db_session, data)
    assert result.created == 1
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.index == 1
    # Error carries the row's identifiers and a specific message.
    assert err.username == "svc_admin"
    assert err.email == "other@example.com"
    assert "svc_admin" in err.detail


@pytest.mark.asyncio
async def test_bulk_create_duplicate_email_names_the_email(
    db_session: AsyncSession, admin: User
) -> None:
    data = UserBulkCreate(
        users=[
            # username is unique, but the email collides with the admin fixture
            UserCreate(username="fresh_name", email="svc_admin@example.com"),
        ]
    )
    result = await user_service.bulk_create_users(db_session, data)
    assert result.created == 0
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.email == "svc_admin@example.com"
    assert err.detail == "Email 'svc_admin@example.com' is already taken"


# --- export_users_csv ---


@pytest.mark.asyncio
async def test_export_users_csv(db_session: AsyncSession, admin: User) -> None:
    chunks = []
    async for chunk in user_service.export_users_csv(db_session):
        chunks.append(chunk)
    output = "".join(chunks)
    assert "id,username" in output
    assert "svc_admin" in output


# --- export_users_excel ---


@pytest.mark.asyncio
async def test_export_users_excel(db_session: AsyncSession, admin: User) -> None:
    data = await user_service.export_users_excel(db_session)
    # XLSX files start with the PK zip magic bytes
    assert data[:2] == b"PK"


# --- role ceiling (Lead capped at Lead) ---


@pytest.mark.asyncio
async def test_lead_cannot_create_admin(
    db_session: AsyncSession, lead_user: User
) -> None:
    data = UserCreate(
        username="lead_made_admin",
        email="lead_made_admin@example.com",
        role=UserRole.ADMIN,
    )
    with pytest.raises(ForbiddenError):
        await user_service.create_user(db_session, data, actor=lead_user)


@pytest.mark.asyncio
async def test_lead_can_create_non_admin(
    db_session: AsyncSession, lead_user: User
) -> None:
    data = UserCreate(
        username="lead_made_tester",
        email="lead_made_tester@example.com",
        role=UserRole.TESTER,
    )
    user = await user_service.create_user(db_session, data, actor=lead_user)
    assert user.role == UserRole.TESTER


@pytest.mark.asyncio
async def test_admin_can_create_admin(
    db_session: AsyncSession, admin: User
) -> None:
    data = UserCreate(
        username="admin_made_admin",
        email="admin_made_admin@example.com",
        role=UserRole.ADMIN,
    )
    user = await user_service.create_user(db_session, data, actor=admin)
    assert user.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_lead_cannot_update_admin(
    db_session: AsyncSession, lead_user: User, admin: User
) -> None:
    with pytest.raises(ForbiddenError):
        await user_service.update_user(
            db_session, admin.id, UserUpdate(full_name="hijack"), actor=lead_user
        )


@pytest.mark.asyncio
async def test_lead_cannot_elevate_to_admin(
    db_session: AsyncSession, lead_user: User
) -> None:
    target = User(
        username="elevate_target",
        email="elevate_target@example.com",
        hashed_password=get_password_hash("p"),
        role=UserRole.TESTER,
        is_active=True,
    )
    db_session.add(target)
    await db_session.flush()
    await db_session.refresh(target)
    with pytest.raises(ForbiddenError):
        await user_service.update_user(
            db_session, target.id, UserUpdate(role=UserRole.ADMIN), actor=lead_user
        )


@pytest.mark.asyncio
async def test_lead_cannot_delete_admin(
    db_session: AsyncSession, lead_user: User, admin: User
) -> None:
    with pytest.raises(ForbiddenError):
        await user_service.delete_user(db_session, admin.id, actor=lead_user)


@pytest.mark.asyncio
async def test_bulk_create_lead_admin_row_errors(
    db_session: AsyncSession, lead_user: User
) -> None:
    data = UserBulkCreate(
        users=[
            UserCreate(
                username="bulk_t", email="bulk_t@example.com", role=UserRole.TESTER
            ),
            UserCreate(
                username="bulk_a", email="bulk_a@example.com", role=UserRole.ADMIN
            ),
        ]
    )
    result = await user_service.bulk_create_users(db_session, data, actor=lead_user)
    assert result.created == 1
    assert len(result.errors) == 1
    assert result.errors[0].index == 1
