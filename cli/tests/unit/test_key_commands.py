"""`testoria key …` — API key management.

The load-bearing behaviour is that these commands use the **JWT**, never the API
key, even when TESTORIA_API_KEY is set: `/api-keys` is JWT-only server-side, and
sending the key would earn a bare 403 instead of a message naming the fix.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from testoria_cli import config as config_module
from testoria_cli.config import Config
from testoria_cli.main import app

runner = CliRunner()

CREATED = {
    "id": 3,
    "name": "github-actions",
    "key_prefix": "a1b2c3d4",
    "user_id": 1,
    "project_id": 7,
    "role": "tester",
    "expires_at": "2026-11-08T12:00:00",
    "last_used_at": None,
    "revoked_at": None,
    "created_at": "2026-08-10T12:00:00",
    "key": "tsk_a1b2c3d4_SECRETVALUE",
}


class FakeClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, Any]] = []
        self.gets: list[tuple[str, Any]] = []
        self.deletes: list[str] = []
        self.listing: list[dict[str, Any]] = []

    def post(self, path: str, json: Any = None) -> Any:
        self.posts.append((path, json))
        return CREATED

    def get(self, path: str, params: Any = None) -> Any:
        self.gets.append((path, params))
        return self.listing

    def delete(self, path: str) -> Any:
        self.deletes.append(path)
        return None


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Record which client each command asked for (plain vs jwt)."""
    built: dict[str, Any] = {"prefer_jwt": [], "client": FakeClient()}

    def build(credentials: Any, prefer_jwt: bool = False, **_: Any) -> FakeClient:
        built["prefer_jwt"].append(prefer_jwt)
        built["credentials"] = credentials
        return built["client"]

    monkeypatch.setattr("testoria_cli.main.TestoriaClient", build)
    monkeypatch.setenv("TESTORIA_URL", "https://api.test")
    monkeypatch.delenv("TESTORIA_API_KEY", raising=False)
    monkeypatch.setattr("testoria_cli.config.CONFIG_PATH", tmp_path / "config.yaml")
    config_module.save(
        Config(url="https://api.test", access_token="jwt"), tmp_path / "config.yaml"
    )
    return built


def test_create_posts_the_expected_body(fake: dict[str, Any]) -> None:
    result = runner.invoke(
        app,
        ["key", "create", "--name", "github-actions", "--project", "7"],
    )

    assert result.exit_code == 0, result.output
    path, body = fake["client"].posts[0]
    assert path == "/api-keys"
    assert body == {"name": "github-actions", "role": "tester", "project_id": 7}


def test_create_uses_the_jwt_client_not_the_api_key(fake: dict[str, Any]) -> None:
    runner.invoke(app, ["key", "create", "--name", "x"])

    assert fake["prefer_jwt"] == [True]


def test_create_prints_the_secret_once_with_a_warning(fake: dict[str, Any]) -> None:
    result = runner.invoke(app, ["key", "create", "--name", "x"])

    assert "tsk_a1b2c3d4_SECRETVALUE" in result.output
    assert "only time" in result.output


def test_create_json_output_is_scriptable(fake: dict[str, Any]) -> None:
    result = runner.invoke(app, ["key", "create", "--name", "x", "-o", "json"])

    assert result.exit_code == 0
    assert '"key": "tsk_a1b2c3d4_SECRETVALUE"' in result.output


def test_create_passes_expiry_options(fake: dict[str, Any]) -> None:
    runner.invoke(app, ["key", "create", "--name", "x", "--expires-in-days", "30"])
    assert fake["client"].posts[0][1]["expires_in_days"] == 30

    runner.invoke(app, ["key", "create", "--name", "y", "--never-expires"])
    assert fake["client"].posts[1][1]["never_expires"] is True


def test_create_rejects_conflicting_expiry_flags(fake: dict[str, Any]) -> None:
    result = runner.invoke(
        app,
        ["key", "create", "--name", "x", "--never-expires", "--expires-in-days", "30"],
    )

    assert result.exit_code == 1
    assert fake["client"].posts == []


def test_create_rejects_an_unknown_role(fake: dict[str, Any]) -> None:
    result = runner.invoke(app, ["key", "create", "--name", "x", "--role", "wizard"])

    assert result.exit_code == 1
    assert "Unknown role" in result.output
    assert fake["client"].posts == []


def test_create_for_another_user(fake: dict[str, Any]) -> None:
    runner.invoke(app, ["key", "create", "--name", "x", "--for-user", "9"])

    assert fake["client"].posts[0][1]["user_id"] == 9


def test_list_renders_a_table_without_secrets(fake: dict[str, Any]) -> None:
    fake["client"].listing = [
        {
            "id": 3,
            "name": "github-actions",
            "key_prefix": "a1b2c3d4",
            "role": "tester",
            "project_id": 7,
            "last_used_at": "2026-08-10T09:00:00",
            "expires_at": "2026-11-08T12:00:00",
            "revoked_at": None,
        }
    ]

    result = runner.invoke(app, ["key", "list"])

    assert result.exit_code == 0
    assert "a1b2c3d4" in result.output
    assert "github-actions" in result.output
    assert "tsk_" not in result.output


def test_list_empty_names_the_next_step(fake: dict[str, Any]) -> None:
    result = runner.invoke(app, ["key", "list"])

    assert "testoria key create" in result.output


def test_list_include_revoked_is_passed_through(fake: dict[str, Any]) -> None:
    runner.invoke(app, ["key", "list", "--include-revoked"])

    assert fake["client"].gets[0][1]["include_revoked"] == "true"


def test_list_uses_the_jwt_client(fake: dict[str, Any]) -> None:
    runner.invoke(app, ["key", "list"])

    assert fake["prefer_jwt"] == [True]


def test_revoke_calls_delete(fake: dict[str, Any]) -> None:
    result = runner.invoke(app, ["key", "revoke", "--id", "3"])

    assert result.exit_code == 0
    assert fake["client"].deletes == ["/api-keys/3"]


def test_revoke_uses_the_jwt_client(fake: dict[str, Any]) -> None:
    runner.invoke(app, ["key", "revoke", "--id", "3"])

    assert fake["prefer_jwt"] == [True]


def test_key_commands_explain_that_an_api_key_will_not_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The common CI-shell mistake: TESTORIA_API_KEY set, no interactive login."""
    monkeypatch.setenv("TESTORIA_URL", "https://api.test")
    monkeypatch.setenv("TESTORIA_API_KEY", "tsk_a_b")
    monkeypatch.setattr("testoria_cli.config.CONFIG_PATH", tmp_path / "absent.yaml")

    result = runner.invoke(app, ["key", "list"])

    assert result.exit_code == 1
    assert "auth login" in result.output
    assert "cannot manage keys" in result.output


def test_upload_still_prefers_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: carrying both credentials must not change transport."""
    from testoria_cli.client import TestoriaClient
    from testoria_cli.config import Credentials

    both = Credentials(
        url="https://api.test", api_key="tsk_a_b", access_token="jwt-token"
    )

    assert "X-API-Key" in TestoriaClient(both)._headers()
    assert "Authorization" in TestoriaClient(both, prefer_jwt=True)._headers()
    # Still exactly one header, either way.
    assert len(TestoriaClient(both)._headers()) == 1
    assert len(TestoriaClient(both, prefer_jwt=True)._headers()) == 1
