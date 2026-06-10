from __future__ import annotations

import redis.asyncio as redis

from app.config import settings

# Process-wide singleton. The first real async Redis consumer in the codebase;
# the planned token-blocklist-on-logout work can reuse this client.
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return the shared async Redis client, creating it on first use.

    `decode_responses=True` so string values (our JSON token payloads) come
    back as `str` rather than `bytes`.
    """
    global _client
    if _client is None:
        _client = redis.from_url(  # type: ignore[no-untyped-call]
            settings.REDIS_URL, decode_responses=True
        )
    return _client


async def close_redis() -> None:
    """Close the shared client (called from the FastAPI lifespan shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
