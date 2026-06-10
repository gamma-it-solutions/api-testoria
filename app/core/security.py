from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.config import settings
from app.core.exceptions import UnauthorizedError


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _create_token(
    data: dict[str, object], expires_delta: timedelta, token_type: str
) -> str:
    payload = data.copy()
    expire = datetime.now(UTC) + expires_delta
    payload.update({"exp": expire, "type": token_type})
    encoded: str = jwt.encode(
        payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded


def create_access_token(data: dict[str, object]) -> str:
    return _create_token(
        data,
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def create_refresh_token(data: dict[str, object]) -> str:
    return _create_token(
        data,
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
    )


def decode_token(token: str, expected_type: str = "access") -> dict[str, object]:
    try:
        payload: dict[str, object] = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        raise UnauthorizedError("Invalid or expired token")

    tok_type = payload.get("type")
    if tok_type != expected_type:
        raise UnauthorizedError(
            f"Expected token type '{expected_type}', got '{tok_type}'"
        )

    return payload
