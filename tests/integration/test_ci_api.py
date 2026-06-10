import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="ci_tester",
        email="ci_tester@example.com",
        hashed_password=get_password_hash("password"),
        role="tester",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="ci_lead",
        email="ci_lead@example.com",
        hashed_password=get_password_hash("password"),
        role="lead",
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


@pytest.fixture
def lead_headers(lead_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(lead_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def project_id(client: AsyncClient, lead_headers: dict[str, str]) -> int:
    resp = await client.post(
        "/api/v1/projects", json={"name": "CI Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def suite_id(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "CI Suite"},
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
            "title": "com.example.LoginTest.test_login",
            "priority": "high",
            "type": "automated",
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
        json={"name": "CI Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    return int(resp.json()["id"])


VALID_JUNIT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<testsuite name="LoginTests" tests="2" failures="0">'
    '  <testcase classname="com.example.LoginTest" name="test_login" time="1.23"/>'
    '  <testcase classname="com.example.LoginTest" name="test_unknown" time="0.5"/>'
    "</testsuite>"
)

JUNIT_XML_WITH_FAILURE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<testsuite name="FailTests" tests="1" failures="1">'
    '  <testcase classname="com.example.LoginTest" name="test_login" time="2.0">'
    '    <failure message="Expected 200 got 500">Stack trace here</failure>'
    "  </testcase>"
    "</testsuite>"
)

JUNIT_XML_WITH_SKIPPED = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<testsuite name="SkipTests" tests="1" skipped="1">'
    '  <testcase classname="com.example.LoginTest" name="test_login" time="0.0">'
    "    <skipped/>"
    "  </testcase>"
    "</testsuite>"
)

MALFORMED_XML = b"<not valid xml><<<"


# --- POST /ci/results/bulk ---


async def test_bulk_import_valid_junit(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/ci/results/bulk",
        params={"test_run_id": run_id},
        files={"file": ("results.xml", VALID_JUNIT_XML.encode(), "application/xml")},
        headers=tester_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["submitted"] == 1
    assert data["skipped"] == 1


async def test_bulk_import_junit_skipped_maps_to_no_run(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/ci/results/bulk",
        params={"test_run_id": run_id},
        files={
            "file": (
                "skipped.xml",
                JUNIT_XML_WITH_SKIPPED.encode(),
                "application/xml",
            )
        },
        headers=tester_headers,
    )
    assert response.status_code == 200
    assert response.json()["submitted"] == 1

    results = await client.get(
        f"/api/v1/test-runs/{run_id}/results", headers=tester_headers
    )
    statuses = [r["status"] for r in results.json()]
    assert "no_run" in statuses


async def test_bulk_import_malformed_xml(
    client: AsyncClient,
    run_id: int,
    tester_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/ci/results/bulk",
        params={"test_run_id": run_id},
        files={"file": ("bad.xml", MALFORMED_XML, "application/xml")},
        headers=tester_headers,
    )
    assert response.status_code == 400


async def test_bulk_import_without_auth(
    client: AsyncClient,
    run_id: int,
) -> None:
    response = await client.post(
        "/api/v1/ci/results/bulk",
        params={"test_run_id": run_id},
        files={"file": ("results.xml", VALID_JUNIT_XML.encode(), "application/xml")},
    )
    assert response.status_code == 401


# --- GET /ci/runs/{id}/badge ---


async def test_badge_returns_svg(
    client: AsyncClient,
    run_id: int,
) -> None:
    response = await client.get(f"/api/v1/ci/runs/{run_id}/badge")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert "<svg" in response.text
    assert "tests" in response.text


async def test_badge_green_for_full_pass(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> None:
    # Submit a passing result first
    await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "passed"},
        headers=tester_headers,
    )
    response = await client.get(f"/api/v1/ci/runs/{run_id}/badge")
    assert response.status_code == 200
    assert "#4c1" in response.text
    assert "100%" in response.text


async def test_badge_not_found(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/ci/runs/999999/badge")
    assert response.status_code == 404


# --- POST /ci/webhooks ---


async def test_webhook_accepted(
    client: AsyncClient,
    tester_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/v1/ci/webhooks",
        json={"event": "pipeline_complete", "status": "success"},
        headers=tester_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


async def test_webhook_without_auth(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/ci/webhooks",
        json={"event": "test"},
    )
    assert response.status_code == 401
