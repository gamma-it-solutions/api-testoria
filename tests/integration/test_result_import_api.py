import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.models.user import User

# Byte-for-byte the shape pytest 8.3.5 emits.
JUNIT = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<testsuites><testsuite name="pytest" tests="2">'
    b'<testcase classname="tests.auth.test_auth.TestAuth" name="test_ok" time="0.5"/>'
    b'<testcase classname="tests.auth.test_auth.TestAuth" name="test_bad" time="0.1">'
    b'<failure message="AssertionError: boom">trace</failure></testcase>'
    b"</testsuite></testsuites>"
)


async def _make_user(db: AsyncSession, username: str, role: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=get_password_hash("password"),
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def imp_tester(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "imp_tester", "tester")


@pytest_asyncio.fixture
async def imp_readonly(db_session: AsyncSession) -> User:
    return await _make_user(db_session, "imp_readonly", "read_only")


@pytest.fixture
def tester_headers(imp_tester: User) -> dict[str, str]:
    token = create_access_token({"sub": str(imp_tester.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def run_graph(db_session: AsyncSession) -> tuple[int, int]:
    """A run whose cases carry pytest node IDs as `automation_id`."""
    project = Project(name="Import API project")
    db_session.add(project)
    await db_session.flush()

    suite = TestSuite(project_id=project.id, name="Suite")
    db_session.add(suite)
    await db_session.flush()

    for node_id, title in [
        ("tests/auth/test_auth.py::TestAuth::test_ok", "Login works"),
        ("tests/auth/test_auth.py::TestAuth::test_bad", "Login rejects bad password"),
    ]:
        db_session.add(
            TestCase(
                suite_id=suite.id, title=title, automation_id=node_id, steps=[]
            )
        )
    await db_session.flush()

    run = TestRun(project_id=project.id, name="CI run", status="planned")
    db_session.add(run)
    await db_session.flush()
    return run.id, project.id


def _file() -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("api.xml", JUNIT, "application/xml")}


async def test_import_via_jwt(
    client: AsyncClient, tester_headers: dict[str, str], run_graph: tuple[int, int]
) -> None:
    run_id, _ = run_graph

    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import",
        files=_file(),
        headers=tester_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matched"] == 2
    assert body["submitted"] == 2
    assert body["unmatched"] == 0
    # The load-bearing assertion: node IDs matched through the dotted rule.
    assert body["matched_by"] == {"automation_id_dotted": 2}
    assert body["status_counts"] == {"passed": 1, "failed": 1}


async def test_import_via_api_key(
    client: AsyncClient, tester_headers: dict[str, str], run_graph: tuple[int, int]
) -> None:
    run_id, project_id = run_graph
    minted = await client.post(
        "/api/v1/api-keys",
        json={"name": "ci", "project_id": project_id},
        headers=tester_headers,
    )
    assert minted.status_code == 201, minted.text

    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import",
        files=_file(),
        headers={"X-API-Key": minted.json()["key"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["submitted"] == 2


async def test_scoped_key_cannot_write_to_a_foreign_run(
    client: AsyncClient,
    db_session: AsyncSession,
    tester_headers: dict[str, str],
    run_graph: tuple[int, int],
) -> None:
    run_id, _ = run_graph
    other = Project(name="Someone else's project")
    db_session.add(other)
    await db_session.flush()

    minted = await client.post(
        "/api/v1/api-keys",
        json={"name": "scoped", "project_id": other.id},
        headers=tester_headers,
    )
    assert minted.status_code == 201

    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import",
        files=_file(),
        headers={"X-API-Key": minted.json()["key"]},
    )

    assert response.status_code == 403


async def test_unscoped_key_may_write_anywhere(
    client: AsyncClient, tester_headers: dict[str, str], run_graph: tuple[int, int]
) -> None:
    run_id, _ = run_graph
    minted = await client.post(
        "/api/v1/api-keys", json={"name": "global"}, headers=tester_headers
    )

    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import",
        files=_file(),
        headers={"X-API-Key": minted.json()["key"]},
    )

    assert response.status_code == 200


async def test_import_requires_authentication(
    client: AsyncClient, run_graph: tuple[int, int]
) -> None:
    run_id, _ = run_graph
    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import", files=_file()
    )
    assert response.status_code == 401


async def test_import_rejects_read_only(
    client: AsyncClient, imp_readonly: User, run_graph: tuple[int, int]
) -> None:
    run_id, _ = run_graph
    headers = {
        "Authorization": f"Bearer {create_access_token({'sub': str(imp_readonly.id)})}"
    }

    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import", files=_file(), headers=headers
    )

    assert response.status_code == 403


async def test_import_unknown_run_404s(
    client: AsyncClient, tester_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/test-runs/987654/results/import",
        files=_file(),
        headers=tester_headers,
    )
    assert response.status_code == 404


async def test_import_malformed_xml_400s(
    client: AsyncClient, tester_headers: dict[str, str], run_graph: tuple[int, int]
) -> None:
    run_id, _ = run_graph
    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import",
        files={"file": ("api.xml", b"<testsuite><oops>", "application/xml")},
        headers=tester_headers,
    )
    assert response.status_code == 400


async def test_reimport_is_idempotent(
    client: AsyncClient, tester_headers: dict[str, str], run_graph: tuple[int, int]
) -> None:
    """A re-run after a network blip must not duplicate results or history."""
    run_id, _ = run_graph
    url = f"/api/v1/test-runs/{run_id}/results/import"

    await client.post(url, files=_file(), headers=tester_headers)
    results_after_first = await client.get(
        f"/api/v1/test-runs/{run_id}/results", headers=tester_headers
    )
    first_ids = sorted(r["id"] for r in results_after_first.json())
    histories_before = [
        len(
            (
                await client.get(
                    f"/api/v1/test-results/{rid}/history", headers=tester_headers
                )
            ).json()
        )
        for rid in first_ids
    ]

    await client.post(url, files=_file(), headers=tester_headers)
    results_after_second = await client.get(
        f"/api/v1/test-runs/{run_id}/results", headers=tester_headers
    )
    second_ids = sorted(r["id"] for r in results_after_second.json())
    histories_after = [
        len(
            (
                await client.get(
                    f"/api/v1/test-results/{rid}/history", headers=tester_headers
                )
            ).json()
        )
        for rid in second_ids
    ]

    assert first_ids == second_ids  # upsert, not insert
    assert histories_before == histories_after  # no history noise


async def test_unmatched_cases_are_named_not_counted(
    client: AsyncClient, tester_headers: dict[str, str], run_graph: tuple[int, int]
) -> None:
    run_id, _ = run_graph
    xml = (
        b'<testsuite><testcase classname="tests.auth.test_auth" '
        b'name="test_renamed"/></testsuite>'
    )

    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import",
        files={"file": ("api.xml", xml, "application/xml")},
        headers=tester_headers,
    )

    body = response.json()
    assert body["unmatched"] == 1
    assert body["unmatched_cases"][0]["identifier"] == (
        "tests.auth.test_auth.test_renamed"
    )
    assert body["unmatched_cases"][0]["reason"] == "no_match"


async def test_json_format_is_accepted(
    client: AsyncClient, tester_headers: dict[str, str], run_graph: tuple[int, int]
) -> None:
    run_id, _ = run_graph
    payload = (
        b'[{"classname": "tests.auth.test_auth.TestAuth", "name": "test_ok",'
        b' "status": "passed"}]'
    )

    response = await client.post(
        f"/api/v1/test-runs/{run_id}/results/import",
        files={"file": ("results.json", payload, "application/json")},
        headers=tester_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["submitted"] == 1


async def test_unmapped_filter_finds_cases_with_no_automation_id(
    client: AsyncClient,
    db_session: AsyncSession,
    tester_headers: dict[str, str],
    run_graph: tuple[int, int],
) -> None:
    _, project_id = run_graph
    suite_result = await client.get(
        f"/api/v1/projects/{project_id}/test-suites", headers=tester_headers
    )
    suite_id = suite_result.json()[0]["id"]
    db_session.add(
        TestCase(suite_id=suite_id, title="Manual only", automation_id=None, steps=[])
    )
    await db_session.flush()

    unmapped = await client.get(
        f"/api/v1/projects/{project_id}/test-cases?has_automation_id=false",
        headers=tester_headers,
    )
    mapped = await client.get(
        f"/api/v1/projects/{project_id}/test-cases?has_automation_id=true",
        headers=tester_headers,
    )
    unfiltered = await client.get(
        f"/api/v1/projects/{project_id}/test-cases", headers=tester_headers
    )

    assert [c["title"] for c in unmapped.json()["items"]] == ["Manual only"]
    assert unmapped.json()["total"] == 1
    assert mapped.json()["total"] == 2
    # Omitting the param must preserve today's behaviour.
    assert unfiltered.json()["total"] == 3
