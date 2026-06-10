import pytest

from app.services import password_token_service as pts


@pytest.mark.asyncio
async def test_create_and_peek_roundtrip() -> None:
    token = await pts.create_token(42, pts.PURPOSE_RESET, ttl=60)
    assert isinstance(token, str) and token

    peeked = await pts.peek_token(token)
    assert peeked == (42, pts.PURPOSE_RESET)


@pytest.mark.asyncio
async def test_peek_does_not_consume() -> None:
    token = await pts.create_token(7, pts.PURPOSE_INVITE, ttl=60)
    assert await pts.peek_token(token) is not None
    # Still valid after peeking.
    assert await pts.peek_token(token) is not None


@pytest.mark.asyncio
async def test_consume_is_single_use() -> None:
    token = await pts.create_token(9, pts.PURPOSE_RESET, ttl=60)
    first = await pts.consume_token(token)
    assert first == (9, pts.PURPOSE_RESET)
    # Second consume finds nothing — GETDEL already removed it.
    assert await pts.consume_token(token) is None


@pytest.mark.asyncio
async def test_peek_after_consume_returns_none() -> None:
    token = await pts.create_token(9, pts.PURPOSE_RESET, ttl=60)
    await pts.consume_token(token)
    assert await pts.peek_token(token) is None


@pytest.mark.asyncio
async def test_unknown_token_returns_none() -> None:
    assert await pts.peek_token("does-not-exist") is None
    assert await pts.consume_token("does-not-exist") is None


@pytest.mark.asyncio
async def test_tokens_are_unique() -> None:
    a = await pts.create_token(1, pts.PURPOSE_RESET, ttl=60)
    b = await pts.create_token(1, pts.PURPOSE_RESET, ttl=60)
    assert a != b


@pytest.mark.asyncio
async def test_expired_token_is_gone() -> None:
    from app.core import redis as app_redis

    # Simulate a TTL lapse by dropping the key, then confirm it's unusable.
    token = await pts.create_token(5, pts.PURPOSE_RESET, ttl=1)
    await app_redis.get_redis().delete(f"pwtoken:{token}")
    assert await pts.consume_token(token) is None
