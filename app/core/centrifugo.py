import logging
import time
from typing import Any

import httpx
from jose import jwt

from app.config import settings

logger = logging.getLogger(__name__)

_CONNECTION_TOKEN_TTL = 300  # 5 minutes
_SUBSCRIPTION_TOKEN_TTL = 300  # 5 minutes


def generate_connection_token(user_id: int, username: str) -> str:
    claims = {
        "sub": str(user_id),
        "info": {"username": username},
        "exp": int(time.time()) + _CONNECTION_TOKEN_TTL,
    }
    token: str = jwt.encode(claims, settings.CENTRIFUGO_TOKEN_SECRET, algorithm="HS256")
    return token


def generate_subscription_token(user_id: int, channel: str) -> str:
    claims = {
        "sub": str(user_id),
        "channel": channel,
        "exp": int(time.time()) + _SUBSCRIPTION_TOKEN_TTL,
    }
    token: str = jwt.encode(claims, settings.CENTRIFUGO_TOKEN_SECRET, algorithm="HS256")
    return token


async def publish(channel: str, data: dict[str, Any]) -> None:
    url = f"{settings.CENTRIFUGO_URL}/api/publish"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"apikey {settings.CENTRIFUGO_API_KEY}",
    }
    payload = {"channel": channel, "data": data}

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
