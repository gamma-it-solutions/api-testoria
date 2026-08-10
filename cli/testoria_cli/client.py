"""Synchronous HTTP client. The CLI runs in a terminal; async buys nothing."""

import time
from pathlib import Path
from typing import Any

import httpx

from testoria_cli.config import Credentials
from testoria_cli.errors import APIError, AuthError

_RETRY_STATUSES = {502, 503, 504}
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.0


class TestoriaClient:
    def __init__(
        self,
        credentials: Credentials,
        timeout: float = 30.0,
        prefer_jwt: bool = False,
    ) -> None:
        """`prefer_jwt` forces the interactive login for JWT-only routes.

        Key management is JWT-only server-side. Sending the API key there would
        earn a bare 403, so those commands ask for the JWT explicitly and get a
        message that names the fix instead.
        """
        if not credentials.url:
            raise AuthError(
                "No Testoria URL. Pass --url, set TESTORIA_URL, "
                "or run `testoria auth login`."
            )
        if prefer_jwt and not credentials.access_token:
            extra = (
                " An API key cannot manage keys, so TESTORIA_API_KEY will not "
                "work here."
                if credentials.api_key
                else ""
            )
            raise AuthError(
                f"This command needs an interactive login. "
                f"Run `testoria auth login`.{extra}"
            )
        if not credentials.api_key and not credentials.access_token:
            raise AuthError(
                "No credentials. Set TESTORIA_API_KEY for CI, "
                "or run `testoria auth login`."
            )
        self._credentials = credentials
        self._prefer_jwt = prefer_jwt
        self._base_url = f"{credentials.url}/api/v1"
        self._client = httpx.Client(timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        # Exactly one credential — sending both is a 400 by design.
        if self._credentials.api_key and not self._prefer_jwt:
            return {"X-API-Key": self._credentials.api_key}
        return {"Authorization": f"Bearer {self._credentials.access_token}"}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TestoriaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        retry: bool = False,
    ) -> Any:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(_MAX_ATTEMPTS if retry else 1):
            try:
                response = self._client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    files=files,
                    data=data,
                    headers=self._headers(),
                )
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS * (2**attempt))
                    continue
                raise APIError(f"Could not reach {url}: {exc}") from exc

            if response.status_code in _RETRY_STATUSES and attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_SECONDS * (2**attempt))
                continue
            return _unwrap(response)

        raise APIError(f"Could not reach {url}: {last_error}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params, retry=True)

    def post(self, path: str, json: Any = None) -> Any:
        return self._request("POST", path, json=json)

    def put(self, path: str, json: Any = None) -> Any:
        return self._request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def upload(
        self,
        path: str,
        file_path: Path,
        content: bytes,
        data: dict[str, Any] | None = None,
        content_type: str = "application/octet-stream",
    ) -> Any:
        return self._request(
            "POST",
            path,
            files={"file": (file_path.name, content, content_type)},
            data=data,
        )


def _unwrap(response: httpx.Response) -> Any:
    if response.status_code == 401:
        raise AuthError(f"Rejected by the server: {_detail(response)}")
    if response.status_code == 403:
        raise AuthError(f"Forbidden: {_detail(response)}")
    if response.status_code >= 400:
        raise APIError(
            f"{response.status_code}: {_detail(response)}",
            status_code=response.status_code,
        )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return str(payload)[:200]
