from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.core.roles import UserRole
from app.core.security import get_password_hash
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.api_key import ApiKeyCreate
from app.services import api_key_service


async def _user(db: AsyncSession, username: str, role: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=get_password_hash("pw"),
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def test_mint_returns_plaintext_once_and_stores_only_a_hash(
    db_session: AsyncSession,
) -> None:
    actor = await _user(db_session, "minter", UserRole.TESTER)

    key, plaintext = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci"), actor
    )

    assert plaintext.startswith("tsk_")
    assert plaintext.split("_")[1] == key.key_prefix
    # The stored hash must not be the secret, and the secret must not be stored.
    assert key.key_hash != plaintext
    assert plaintext not in key.key_hash
    assert len(key.key_hash) == 64


async def test_mint_defaults_to_an_expiry(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "expiry", UserRole.TESTER)

    key, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="ci"), actor)

    assert key.expires_at is not None


async def test_mint_never_expires_requires_opt_in(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "forever", UserRole.TESTER)

    key, _ = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci", never_expires=True), actor
    )

    assert key.expires_at is None


async def test_mint_rejects_a_role_above_the_owner(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "weak", UserRole.READ_ONLY)

    with pytest.raises(BadRequestError):
        await api_key_service.mint(
            db_session, ApiKeyCreate(name="ci", role=UserRole.TESTER), actor
        )


async def test_mint_rejects_a_role_above_the_configured_cap(
    db_session: AsyncSession,
) -> None:
    actor = await _user(db_session, "boss", UserRole.ADMIN)

    with pytest.raises(BadRequestError):
        await api_key_service.mint(
            db_session, ApiKeyCreate(name="ci", role=UserRole.ADMIN), actor
        )


async def test_mint_for_another_user_requires_lead(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "peon", UserRole.TESTER)
    other = await _user(db_session, "other", UserRole.TESTER)

    with pytest.raises(ForbiddenError):
        await api_key_service.mint(
            db_session, ApiKeyCreate(name="ci", user_id=other.id), actor
        )


async def test_mint_for_unknown_project_404s(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "scoped", UserRole.TESTER)

    with pytest.raises(NotFoundError):
        await api_key_service.mint(
            db_session, ApiKeyCreate(name="ci", project_id=987654), actor
        )


def test_effective_role_is_the_weakest_of_the_three() -> None:
    # Key stronger than owner -> owner wins.
    assert (
        api_key_service.effective_role(UserRole.TESTER, UserRole.READ_ONLY)
        == UserRole.READ_ONLY
    )
    # Owner stronger than key -> key wins.
    assert (
        api_key_service.effective_role(UserRole.READ_ONLY, UserRole.ADMIN)
        == UserRole.READ_ONLY
    )
    # Both above the cap -> cap wins.
    assert (
        api_key_service.effective_role(UserRole.ADMIN, UserRole.ADMIN)
        == UserRole(settings.API_KEY_MAX_ROLE)
    )


async def test_resolve_accepts_a_valid_key(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "valid", UserRole.TESTER)
    _, plaintext = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci"), actor
    )

    resolved = await api_key_service.resolve(db_session, plaintext)

    assert resolved is not None
    assert resolved[1].id == actor.id


@pytest.mark.parametrize(
    "raw",
    ["", "garbage", "tsk_only_two", "Bearer abc", "tsk__nosecret"],
)
async def test_resolve_rejects_malformed_keys(
    db_session: AsyncSession, raw: str
) -> None:
    assert await api_key_service.resolve(db_session, raw) is None


async def test_resolve_rejects_a_wrong_secret(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "wrongsecret", UserRole.TESTER)
    key, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="ci"), actor)

    assert (
        await api_key_service.resolve(db_session, f"tsk_{key.key_prefix}_notthesecret")
        is None
    )


async def test_resolve_rejects_a_revoked_key(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "revoked", UserRole.TESTER)
    key, plaintext = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci"), actor
    )

    await api_key_service.revoke(db_session, key.id, actor)

    assert await api_key_service.resolve(db_session, plaintext) is None


async def test_resolve_rejects_an_expired_key(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "expired", UserRole.TESTER)
    key, plaintext = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci"), actor
    )
    key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    assert await api_key_service.resolve(db_session, plaintext) is None


async def test_resolve_rejects_an_inactive_owner(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "inactive", UserRole.TESTER)
    _, plaintext = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci"), actor
    )
    actor.is_active = False
    await db_session.flush()

    assert await api_key_service.resolve(db_session, plaintext) is None


async def test_resolve_rejects_a_no_access_owner(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "noaccess", UserRole.TESTER)
    _, plaintext = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci"), actor
    )
    actor.role = UserRole.NO_ACCESS
    await db_session.flush()

    assert await api_key_service.resolve(db_session, plaintext) is None


async def test_revoke_is_idempotent(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "twice", UserRole.TESTER)
    key, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="ci"), actor)

    await api_key_service.revoke(db_session, key.id, actor)
    first = key.revoked_at
    await api_key_service.revoke(db_session, key.id, actor)

    assert key.revoked_at == first


async def test_revoke_someone_elses_key_404s(db_session: AsyncSession) -> None:
    owner = await _user(db_session, "owner", UserRole.TESTER)
    stranger = await _user(db_session, "stranger", UserRole.TESTER)
    key, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="ci"), owner)

    # 404 not 403: whether another user's key id exists is not this caller's business.
    with pytest.raises(NotFoundError):
        await api_key_service.revoke(db_session, key.id, stranger)


async def test_list_keys_hides_revoked_by_default(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "lister", UserRole.TESTER)
    live, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="live"), actor)
    dead, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="dead"), actor)
    await api_key_service.revoke(db_session, dead.id, actor)

    default = await api_key_service.list_keys(db_session, actor)
    everything = await api_key_service.list_keys(
        db_session, actor, include_revoked=True
    )

    assert [k.id for k in default] == [live.id]
    assert {k.id for k in everything} == {live.id, dead.id}


async def test_list_another_users_keys_requires_admin(
    db_session: AsyncSession,
) -> None:
    actor = await _user(db_session, "nosy", UserRole.TESTER)
    other = await _user(db_session, "target", UserRole.TESTER)

    with pytest.raises(ForbiddenError):
        await api_key_service.list_keys(db_session, actor, user_id=other.id)


async def test_touch_last_used_is_throttled(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "throttle", UserRole.TESTER)
    key, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="ci"), actor)

    await api_key_service.touch_last_used(db_session, key)
    first = key.last_used_at
    assert first is not None

    # Immediately again — inside the throttle window, so unchanged.
    await api_key_service.touch_last_used(db_session, key)
    assert key.last_used_at == first


async def test_touch_last_used_writes_after_the_window(
    db_session: AsyncSession,
) -> None:
    actor = await _user(db_session, "throttle2", UserRole.TESTER)
    key, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="ci"), actor)
    key.last_used_at = datetime.now(UTC) - timedelta(
        seconds=settings.API_KEY_LAST_USED_THROTTLE_SECONDS + 5
    )
    await db_session.flush()
    stale = key.last_used_at

    await api_key_service.touch_last_used(db_session, key)

    assert key.last_used_at != stale


async def test_prefixes_are_unique_across_mints(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "unique", UserRole.TESTER)
    prefixes = set()
    for index in range(10):
        key, _ = await api_key_service.mint(
            db_session, ApiKeyCreate(name=f"k{index}"), actor
        )
        prefixes.add(key.key_prefix)
    assert len(prefixes) == 10


async def test_resolve_does_not_leak_via_prefix_collision(
    db_session: AsyncSession,
) -> None:
    """A valid prefix with another key's secret must not authenticate."""
    actor = await _user(db_session, "collide", UserRole.TESTER)
    first, _ = await api_key_service.mint(db_session, ApiKeyCreate(name="a"), actor)
    _, second_plain = await api_key_service.mint(
        db_session, ApiKeyCreate(name="b"), actor
    )
    second_secret = second_plain.split("_", 2)[2]

    assert (
        await api_key_service.resolve(
            db_session, f"tsk_{first.key_prefix}_{second_secret}"
        )
        is None
    )


async def test_stored_row_never_contains_the_secret(db_session: AsyncSession) -> None:
    actor = await _user(db_session, "nosecret", UserRole.TESTER)
    key, plaintext = await api_key_service.mint(
        db_session, ApiKeyCreate(name="ci"), actor
    )
    secret = plaintext.split("_", 2)[2]

    row = await db_session.get(ApiKey, key.id)
    assert row is not None
    assert secret not in (row.key_hash + row.key_prefix + row.name)
