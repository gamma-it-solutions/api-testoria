from collections.abc import Mapping
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.test_result import TestResult
from app.models.user import User

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="defect_tester",
        email="defect_tester@example.com",
        hashed_password=get_password_hash("password"),
        role="tester",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
def tester_headers(tester_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(tester_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="defect_lead",
        email="defect_lead@example.com",
        hashed_password=get_password_hash("password"),
        role="lead",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
def lead_headers(lead_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(lead_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def project_id(client: AsyncClient, lead_headers: dict[str, str]) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Defect Project"},
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def suite_id(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Defect Suite"},
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def case_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Defect Test Case",
            "priority": "high",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def run_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Defect Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def result_id(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    return int(resp.json()["id"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, json_body: Mapping[str, object]) -> MagicMock:
    """Build a fake httpx.Response that raise_for_status() honours."""
    mock_resp = MagicMock(spec=Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_body
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        # Use a real httpx HTTPStatusError so our handler matches it
        from httpx import HTTPStatusError

        real_req = Request("POST", "http://tracker.test/")
        real_resp = Response(status_code, request=real_req)
        mock_resp.raise_for_status.side_effect = HTTPStatusError(
            f"HTTP {status_code}", request=real_req, response=real_resp
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# GitHub defect tests
# ---------------------------------------------------------------------------


async def test_create_github_defect_success(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    fake_issue = {"number": 42, "html_url": "https://github.com/org/repo/issues/42"}
    mock_resp = _mock_response(201, fake_issue)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "app.services.defect_service.httpx.AsyncClient", return_value=mock_client
    ):
        resp = await client.post(
            "/api/v1/defects/github",
            json={
                "test_result_id": result_id,
                "repo_owner": "org",
                "repo_name": "repo",
                "token": "gh_token",
                "title": "Login fails",
                "body": "Steps to reproduce...",
            },
            headers=tester_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["tracker"] == "github"
    assert data["key"] == "42"
    assert data["url"] == "https://github.com/org/repo/issues/42"
    assert data["summary"] == "Login fails"

    # Verify defect ref stored on TestResult
    from sqlalchemy import select

    row = await db_session.execute(select(TestResult).where(TestResult.id == result_id))
    tr = row.scalar_one()
    assert len(tr.defects) == 1
    assert tr.defects[0]["tracker"] == "github"
    assert tr.defects[0]["key"] == "42"


# ---------------------------------------------------------------------------
# Jira defect tests
# ---------------------------------------------------------------------------


async def test_create_jira_defect_success(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    fake_issue = {
        "key": "BUG-123",
        "self": "https://company.atlassian.net/rest/api/2/issue/BUG-123",
    }
    mock_resp = _mock_response(201, fake_issue)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "app.services.defect_service.httpx.AsyncClient", return_value=mock_client
    ):
        resp = await client.post(
            "/api/v1/defects/jira",
            json={
                "test_result_id": result_id,
                "jira_url": "https://company.atlassian.net",
                "jira_username": "user@example.com",
                "jira_api_token": "token123",
                "project_key": "BUG",
                "summary": "Login fails on prod",
                "description": "Detailed description...",
            },
            headers=tester_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["tracker"] == "jira"
    assert data["key"] == "BUG-123"
    assert data["summary"] == "Login fails on prod"


# ---------------------------------------------------------------------------
# GitLab defect tests
# ---------------------------------------------------------------------------


async def test_create_gitlab_defect_success(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    fake_issue = {
        "iid": 7,
        "web_url": "https://gitlab.example.com/group/repo/-/issues/7",
    }
    mock_resp = _mock_response(200, fake_issue)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "app.services.defect_service.httpx.AsyncClient", return_value=mock_client
    ):
        resp = await client.post(
            "/api/v1/defects/gitlab",
            json={
                "test_result_id": result_id,
                "gitlab_url": "https://gitlab.example.com",
                "project_id": 99,
                "token": "gl_token",
                "title": "Payment fails",
                "description": "Error details...",
            },
            headers=tester_headers,
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["tracker"] == "gitlab"
    assert data["key"] == "7"
    assert data["url"] == "https://gitlab.example.com/group/repo/-/issues/7"


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


async def test_github_invalid_credentials_returns_422(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    mock_resp = _mock_response(401, {"message": "Bad credentials"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "app.services.defect_service.httpx.AsyncClient", return_value=mock_client
    ):
        resp = await client.post(
            "/api/v1/defects/github",
            json={
                "test_result_id": result_id,
                "repo_owner": "org",
                "repo_name": "repo",
                "token": "bad_token",
                "title": "Issue",
                "body": "Body",
            },
            headers=tester_headers,
        )

    assert resp.status_code == 422
    assert "credentials" in resp.json()["detail"].lower()


async def test_jira_project_not_found_returns_422(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    mock_resp = _mock_response(404, {"errorMessages": ["Project not found"]})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "app.services.defect_service.httpx.AsyncClient", return_value=mock_client
    ):
        resp = await client.post(
            "/api/v1/defects/jira",
            json={
                "test_result_id": result_id,
                "jira_url": "https://company.atlassian.net",
                "jira_username": "user@example.com",
                "jira_api_token": "token123",
                "project_key": "NOPE",
                "summary": "Issue",
                "description": "Desc",
            },
            headers=tester_headers,
        )

    assert resp.status_code == 422
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


async def test_create_github_defect_without_auth(
    client: AsyncClient,
    result_id: int,
) -> None:
    resp = await client.post(
        "/api/v1/defects/github",
        json={
            "test_result_id": result_id,
            "repo_owner": "org",
            "repo_name": "repo",
            "token": "token",
            "title": "Issue",
            "body": "Body",
        },
    )
    assert resp.status_code == 401


async def test_create_jira_defect_without_auth(
    client: AsyncClient,
    result_id: int,
) -> None:
    resp = await client.post(
        "/api/v1/defects/jira",
        json={
            "test_result_id": result_id,
            "jira_url": "https://company.atlassian.net",
            "jira_username": "u",
            "jira_api_token": "t",
            "project_key": "BUG",
            "summary": "s",
            "description": "d",
        },
    )
    assert resp.status_code == 401
