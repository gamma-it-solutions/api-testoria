"""Credential resolution: flag > environment > config file.

The API key is deliberately **never** persisted. It comes from `--api-key` or
`TESTORIA_API_KEY` only, so a CI secret cannot leak into a mounted home
directory or a committed dotfile. Only JWTs — which expire — are written to disk,
and the file is created 0600.
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path.home() / ".testoria"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

ENV_URL = "TESTORIA_URL"
ENV_API_KEY = "TESTORIA_API_KEY"


@dataclass
class Config:
    url: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    username: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "username": self.username,
        }


def load(path: Path | None = None) -> Config:
    target = path or CONFIG_PATH
    if not target.exists():
        return Config()
    raw = yaml.safe_load(target.read_text()) or {}
    if not isinstance(raw, dict):
        return Config()
    return Config(
        url=raw.get("url"),
        access_token=raw.get("access_token"),
        refresh_token=raw.get("refresh_token"),
        username=raw.get("username"),
    )


def save(config: Config, path: Path | None = None) -> None:
    target = path or CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config.to_dict(), sort_keys=True))
    # Owner-only: this file holds a bearer token.
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)


def clear(path: Path | None = None) -> None:
    target = path or CONFIG_PATH
    if target.exists():
        target.unlink()


@dataclass
class Credentials:
    url: str
    api_key: str | None
    access_token: str | None

    @property
    def via(self) -> str:
        return "api_key" if self.api_key else "jwt"


def resolve(
    url: str | None = None,
    api_key: str | None = None,
    path: Path | None = None,
    env: dict[str, str] | None = None,
) -> Credentials:
    """Combine flags, environment and config file into usable credentials.

    Raises nothing — an absent credential is reported by the caller so the
    message can name the command that would fix it.
    """
    environ = env if env is not None else dict(os.environ)
    stored = load(path)

    resolved_url = url or environ.get(ENV_URL) or stored.url or ""
    resolved_key = api_key or environ.get(ENV_API_KEY) or None

    # Both are carried; the *client* decides which single header to send (an API
    # key wins by default — the server rejects both at once by design). Keeping
    # the JWT here is what lets `testoria key …` fall back to it: key management
    # is JWT-only, because a key that could mint keys would be a foothold rather
    # than a revocable credential.
    return Credentials(
        url=resolved_url.rstrip("/"),
        api_key=resolved_key,
        access_token=stored.access_token,
    )
