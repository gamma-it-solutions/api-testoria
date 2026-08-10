import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.config import settings
from app.core.exceptions import UnauthorizedError

API_KEY_PREFIX = "tsk"
_API_KEY_PREFIX_BYTES = 4  # -> 8 hex chars
_API_KEY_SECRET_BYTES = 32  # -> 256 bits of entropy


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def generate_api_key() -> tuple[str, str, str]:
    """Mint a new API key.

    Returns:
        `(full_key, key_prefix, key_hash)`. `full_key` is shown to the caller
        exactly once and never stored; only `key_prefix` and `key_hash` persist.
    """
    key_prefix = secrets.token_hex(_API_KEY_PREFIX_BYTES)
    secret = secrets.token_urlsafe(_API_KEY_SECRET_BYTES)
    full_key = f"{API_KEY_PREFIX}_{key_prefix}_{secret}"
    return full_key, key_prefix, hash_api_key(secret)


def hash_api_key(secret: str) -> str:
    """Hash the secret half of an API key.

    SHA-256, not bcrypt: the secret is 256 bits of CSPRNG output, so there is no
    dictionary for a slow KDF to defend against, and bcrypt's cost would be paid
    on every request an unattended pipeline makes.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def split_api_key(full_key: str) -> tuple[str, str] | None:
    """Split `tsk_<prefix>_<secret>` into `(prefix, secret)`.

    Returns None when the value is not shaped like an API key at all, so callers
    can fall through without raising on arbitrary header junk.
    """
    parts = full_key.strip().split("_", 2)
    if len(parts) != 3:
        return None
    scheme, key_prefix, secret = parts
    if scheme != API_KEY_PREFIX or not key_prefix or not secret:
        return None
    return key_prefix, secret


def verify_api_key(secret: str, key_hash: str) -> bool:
    """Constant-time comparison of a presented secret against a stored hash."""
    return secrets.compare_digest(hash_api_key(secret), key_hash)


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
