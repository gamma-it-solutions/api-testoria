import httpx
import pytest

from testoria_cli.client import TestoriaClient
from testoria_cli.config import Credentials
from testoria_cli.errors import APIError, AuthError


def _client(monkeypatch: pytest.MonkeyPatch, handler, **creds) -> TestoriaClient:
    defaults = {"url": "https://api.test", "api_key": "tsk_a_b", "access_token": None}
    client = TestoriaClient(Credentials(**{**defaults, **creds}))
    transport = httpx.MockTransport(handler)
    client._client = httpx.Client(transport=transport)
    return client


def test_api_key_is_sent_as_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = _client(monkeypatch, handler)
    client.get("/auth/me")

    assert seen["x-api-key"] == "tsk_a_b"
    assert "authorization" not in seen


def test_jwt_is_sent_when_there_is_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    client = _client(monkeypatch, handler, api_key=None, access_token="jwt-token")
    client.get("/auth/me")

    assert seen["authorization"] == "Bearer jwt-token"
    assert "x-api-key" not in seen


def test_never_sends_both_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server 400s on both — the client must not provoke it."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    client = _client(monkeypatch, handler, api_key="tsk_a_b", access_token="jwt")
    client.get("/auth/me")

    assert ("x-api-key" in seen) != ("authorization" in seen)


def test_401_becomes_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        lambda r: httpx.Response(401, json={"detail": "Invalid or revoked API key"}),
    )
    with pytest.raises(AuthError, match="Invalid or revoked"):
        client.get("/auth/me")


def test_403_becomes_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch, lambda r: httpx.Response(403, json={"detail": "nope"})
    )
    with pytest.raises(AuthError):
        client.get("/auth/me")


def test_404_becomes_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        lambda r: httpx.Response(404, json={"detail": "TestRun 9 not found"}),
    )
    with pytest.raises(APIError, match="not found"):
        client.get("/test-runs/9")


def test_no_url_is_a_clear_auth_error() -> None:
    with pytest.raises(AuthError, match="No Testoria URL"):
        TestoriaClient(Credentials(url="", api_key="tsk_a_b", access_token=None))


def test_no_credentials_names_the_fix() -> None:
    with pytest.raises(AuthError, match="TESTORIA_API_KEY"):
        TestoriaClient(
            Credentials(url="https://x", api_key=None, access_token=None)
        )


def test_get_retries_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("testoria_cli.client.time.sleep", lambda _: None)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": True})

    client = _client(monkeypatch, handler)
    assert client.get("/x") == {"ok": True}
    assert attempts["n"] == 3


def test_post_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writes are re-runnable by hand, but must not be silently duplicated."""
    monkeypatch.setattr("testoria_cli.client.time.sleep", lambda _: None)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("boom")

    client = _client(monkeypatch, handler)
    with pytest.raises(APIError):
        client.post("/x", json={})
    assert attempts["n"] == 1


def test_204_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, lambda r: httpx.Response(204))
    assert client.post("/test-runs/1/close") is None


def test_non_json_error_body_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch, lambda r: httpx.Response(500, text="<html>gateway</html>")
    )
    with pytest.raises(APIError, match="gateway"):
        client.get("/x")
