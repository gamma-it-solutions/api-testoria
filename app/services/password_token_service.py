from __future__ import annotations

import json
import secrets

from app.core.redis import get_redis

# Token purposes — both flows share the machinery; only TTL + copy differ.
PURPOSE_INVITE = "invite"
PURPOSE_RESET = "reset"

_KEY_PREFIX = "pwtoken:"


def _key(token: str) -> str:
    return f"{_KEY_PREFIX}{token}"


async def create_token(user_id: int, purpose: str, ttl: int) -> str:
    """Mint a single-use, expiring token bound to `user_id` and `purpose`.

    Stored in Redis as `pwtoken:{token}` → JSON, with a native TTL.
    """
    token = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": user_id, "purpose": purpose})
    await get_redis().set(_key(token), payload, ex=ttl)
    return token


async def peek_token(token: str) -> tuple[int, str] | None:
    """Return `(user_id, purpose)` without consuming, or None if absent/expired."""
    raw = await get_redis().get(_key(token))
    if raw is None:
        return None
    data = json.loads(raw)
    return int(data["user_id"]), str(data["purpose"])


async def consume_token(token: str) -> tuple[int, str] | None:
    """Atomically fetch-and-delete the token (single use via GETDEL).

    Returns `(user_id, purpose)`, or None if the token is invalid, expired, or
    already used. A concurrent double-click can only succeed once.
    """
    raw = await get_redis().getdel(_key(token))
    if raw is None:
        return None
    data = json.loads(raw)
    return int(data["user_id"]), str(data["purpose"])
