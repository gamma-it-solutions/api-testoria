import fnmatch
import os
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import redis as app_redis
from app.core import storage
from app.core.security import create_access_token, get_password_hash
from app.database import Base, get_db
from app.main import app
from app.models.user import User

# In-memory storage shim — tests run without a real MinIO. Integration tests
# exercising attachment round-trips verify through this shim.
_FAKE_STORE: dict[str, bytes] = {}


@pytest.fixture(autouse=True)
def _stub_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def put_object(
        key: str,
        body: bytes,
        content_type: str | None = None,
        bucket: str | None = None,
    ) -> None:
        _FAKE_STORE[key] = body

    async def get_object(key: str, bucket: str | None = None) -> bytes:
        if key not in _FAKE_STORE:
            raise KeyError(key)
        return _FAKE_STORE[key]

    async def delete_object(key: str, bucket: str | None = None) -> None:
        _FAKE_STORE.pop(key, None)

    async def generate_presigned_url(
        key: str,
        ttl_seconds: int | None = None,
        bucket: str | None = None,
    ) -> str:
        return f"http://fake-minio/{key}"

    def generate_presigned_url_sync(
        key: str,
        ttl_seconds: int | None = None,
        bucket: str | None = None,
    ) -> str:
        return f"http://fake-minio/{key}"

    async def ensure_bucket(bucket: str | None = None) -> None:
        return None

    monkeypatch.setattr(storage, "put_object", put_object)
    monkeypatch.setattr(storage, "get_object", get_object)
    monkeypatch.setattr(storage, "delete_object", delete_object)
    monkeypatch.setattr(storage, "generate_presigned_url", generate_presigned_url)
    monkeypatch.setattr(
        storage, "generate_presigned_url_sync", generate_presigned_url_sync
    )
    monkeypatch.setattr(storage, "ensure_bucket", ensure_bucket)
    _FAKE_STORE.clear()


class FakeRedis:
    """Tiny in-memory, pure-async stand-in for the bits of redis.asyncio the
    token service uses. Pure-async (no thread executor) so it doesn't break
    coverage tracing of the code that awaits it."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def getdel(self, key: str) -> str | None:
        return self._store.pop(key, None)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self._store.pop(key, None) is not None:
                removed += 1
        return removed

    async def keys(self, pattern: str = "*") -> list[str]:
        return [k for k in self._store if fnmatch.fnmatch(k, pattern)]

    async def aclose(self) -> None:
        self._store.clear()


@pytest.fixture(autouse=True)
def _stub_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back the async Redis client with an in-memory fake per test.

    Token minting (welcome invite / reset) runs on every user creation, so all
    tests need a working Redis without a real server.
    """
    monkeypatch.setattr(app_redis, "_client", FakeRedis())

# Use TEST_DATABASE_URL env var if set (must be a running Postgres),
# otherwise fall back to in-memory SQLite so tests run without a DB server.
_test_url = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
_connect_args: dict[str, object] = (
    {"check_same_thread": False} if _test_url.startswith("sqlite") else {}
)
test_engine = create_async_engine(_test_url, echo=False, connect_args=_connect_args)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=cast(Any, app)), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        username="testuser",
        email="testuser@example.com",
        hashed_password=get_password_hash("testpassword"),
        full_name="Test User",
        role="tester",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="adminuser",
        email="adminuser@example.com",
        hashed_password=get_password_hash("adminpassword"),
        full_name="Admin User",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
def auth_headers(test_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(admin_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(admin_user.id)})
    return {"Authorization": f"Bearer {token}"}
