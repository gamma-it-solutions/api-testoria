import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="proj_lead",
        email="proj_lead@example.com",
        hashed_password=get_password_hash("password"),
        role="lead",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def read_only_user(db_session: AsyncSession) -> User:
    user = User(
        username="proj_readonly",
        email="proj_readonly@example.com",
        hashed_password=get_password_hash("password"),
        role="read_only",
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


@pytest.fixture
def read_only_headers(read_only_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(read_only_user.id)})
    return {"Authorization": f"Bearer {token}"}


# --- GET /projects ---


async def test_list_projects_viewer(
    client: AsyncClient, read_only_user: User, read_only_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/projects", headers=read_only_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


async def test_list_projects_no_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/projects")
    assert response.status_code == 401


# --- POST /projects ---


async def test_create_project_lead(
    client: AsyncClient, lead_user: User, lead_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Alpha Project", "description": "Test project"},
        headers=lead_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Alpha Project"
    assert data["is_archived"] is False


async def test_create_project_read_only_forbidden(
    client: AsyncClient, read_only_user: User, read_only_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Blocked"},
        headers=read_only_headers,
    )
    assert response.status_code == 403


async def test_create_project_no_token(client: AsyncClient) -> None:
    response = await client.post("/api/v1/projects", json={"name": "X"})
    assert response.status_code == 401


# --- GET /projects/{id} ---


async def test_get_project(
    client: AsyncClient, lead_headers: dict[str, str], lead_user: User
) -> None:
    create_resp = await client.post(
        "/api/v1/projects",
        json={"name": "GetMe"},
        headers=lead_headers,
    )
    project_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/projects/{project_id}", headers=lead_headers)
    assert response.status_code == 200
    assert response.json()["id"] == project_id


async def test_get_project_not_found(
    client: AsyncClient, lead_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/projects/999999", headers=lead_headers)
    assert response.status_code == 404


# --- PUT /projects/{id} ---


async def test_update_project(
    client: AsyncClient, lead_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        "/api/v1/projects", json={"name": "Old Name"}, headers=lead_headers
    )
    project_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/projects/{project_id}",
        json={"name": "New Name", "is_archived": True},
        headers=lead_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Name"
    assert data["is_archived"] is True


# --- DELETE /projects/{id} ---


async def test_delete_project_admin(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        "/api/v1/projects", json={"name": "ToDelete"}, headers=admin_headers
    )
    project_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/projects/{project_id}", headers=admin_headers
    )
    assert response.status_code == 204

    get_resp = await client.get(f"/api/v1/projects/{project_id}", headers=admin_headers)
    assert get_resp.status_code == 404


async def test_delete_project_lead_forbidden(
    client: AsyncClient, lead_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        "/api/v1/projects", json={"name": "Protected"}, headers=lead_headers
    )
    project_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/projects/{project_id}", headers=lead_headers
    )
    assert response.status_code == 403


# --- GET /projects/{id}/stats ---


async def test_get_stats(client: AsyncClient, lead_headers: dict[str, str]) -> None:
    create_resp = await client.post(
        "/api/v1/projects", json={"name": "StatsProject"}, headers=lead_headers
    )
    project_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/v1/projects/{project_id}/stats", headers=lead_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_test_cases"] == 0
    assert data["total_test_suites"] == 0
    assert data["total_test_runs"] == 0


# --- GET /projects/stats (bulk) ---


async def _seed_project_with_run_and_results(
    client: AsyncClient,
    admin_headers: dict[str, str],
    name: str,
    *,
    pass_count: int,
    fail_count: int,
    close_run: bool = True,
) -> int:
    project = (
        await client.post(
            "/api/v1/projects", json={"name": name}, headers=admin_headers
        )
    ).json()
    project_id = int(project["id"])
    suite = (
        await client.post(
            f"/api/v1/projects/{project_id}/test-suites",
            json={"name": f"{name} Suite"},
            headers=admin_headers,
        )
    ).json()
    suite_id = int(suite["id"])

    case_ids: list[int] = []
    for i in range(pass_count + fail_count):
        case = (
            await client.post(
                f"/api/v1/projects/{project_id}/test-cases",
                json={
                    "suite_id": suite_id,
                    "title": f"{name} Case {i}",
                    "priority": "medium",
                    "type": "manual",
                    "status": "active",
                },
                headers=admin_headers,
            )
        ).json()
        case_ids.append(int(case["id"]))

    if pass_count + fail_count == 0:
        return project_id

    run = (
        await client.post(
            f"/api/v1/projects/{project_id}/test-runs",
            json={"name": f"{name} Run", "suite_id": suite_id},
            headers=admin_headers,
        )
    ).json()
    run_id = int(run["id"])

    idx = 0
    for _ in range(pass_count):
        await client.post(
            f"/api/v1/test-runs/{run_id}/results",
            json={"test_case_id": case_ids[idx], "status": "passed"},
            headers=admin_headers,
        )
        idx += 1
    for _ in range(fail_count):
        await client.post(
            f"/api/v1/test-runs/{run_id}/results",
            json={"test_case_id": case_ids[idx], "status": "failed"},
            headers=admin_headers,
        )
        idx += 1
    if close_run:
        # plan 039: pass-rate only counts completed runs, so close the seeded
        # run so the fixture reflects the production "finished work" shape.
        await client.post(
            f"/api/v1/test-runs/{run_id}/close", headers=admin_headers
        )
    return project_id


async def test_bulk_stats_empty_workspace(
    client: AsyncClient, lead_headers: dict[str, str]
) -> None:
    resp = await client.get("/api/v1/projects/stats", headers=lead_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_bulk_stats_multiple_projects(
    client: AsyncClient,
    admin_user: User,
    admin_headers: dict[str, str],
) -> None:
    alpha = await _seed_project_with_run_and_results(
        client, admin_headers, "Alpha", pass_count=3, fail_count=1
    )
    beta = await _seed_project_with_run_and_results(
        client, admin_headers, "Beta", pass_count=0, fail_count=0
    )

    resp = await client.get("/api/v1/projects/stats", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    by_id = {row["project_id"]: row for row in body["items"]}
    assert body["total"] == len(body["items"])
    assert alpha in by_id
    assert beta in by_id

    assert by_id[alpha]["total_test_cases"] == 4
    assert by_id[alpha]["total_test_suites"] == 1
    assert by_id[alpha]["total_test_runs"] == 1
    # Fixture closes the run, so active_runs is 0 and pass_rate counts it.
    assert by_id[alpha]["active_runs"] == 0
    assert by_id[alpha]["pass_rate"] == pytest.approx(0.75)

    assert by_id[beta]["total_test_cases"] == 0
    assert by_id[beta]["total_test_runs"] == 0
    assert by_id[beta]["pass_rate"] is None


async def test_bulk_stats_pass_rate_is_mean_of_run_rates(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """plan 041: pass_rate is the arithmetic mean of per-completed-run rates,
    not a weighted passed/total ratio. Seed two runs scoped to separate suites
    at 1/1 (100%) and 0/3 (0%): weighted would be 1/4 = 0.25, mean-of-rates
    is 0.5. Separate suites so each run's scope only includes its own cases."""
    project = (
        await client.post(
            "/api/v1/projects", json={"name": "MeanOfRates"}, headers=admin_headers
        )
    ).json()
    project_id = int(project["id"])

    async def _make_run(suite_name: str, statuses: list[str]) -> None:
        suite = (
            await client.post(
                f"/api/v1/projects/{project_id}/test-suites",
                json={"name": suite_name},
                headers=admin_headers,
            )
        ).json()
        suite_id = int(suite["id"])
        case_ids: list[int] = []
        for i in range(len(statuses)):
            c = (
                await client.post(
                    f"/api/v1/projects/{project_id}/test-cases",
                    json={
                        "suite_id": suite_id,
                        "title": f"{suite_name}-C{i}",
                        "priority": "medium",
                        "type": "manual",
                        "status": "active",
                    },
                    headers=admin_headers,
                )
            ).json()
            case_ids.append(int(c["id"]))
        run = (
            await client.post(
                f"/api/v1/projects/{project_id}/test-runs",
                json={"name": suite_name, "suite_id": suite_id},
                headers=admin_headers,
            )
        ).json()
        run_id = int(run["id"])
        for case_id, status in zip(case_ids, statuses, strict=True):
            await client.post(
                f"/api/v1/test-runs/{run_id}/results",
                json={"test_case_id": case_id, "status": status},
                headers=admin_headers,
            )
        await client.post(
            f"/api/v1/test-runs/{run_id}/close", headers=admin_headers
        )

    await _make_run("SuiteA", ["passed"])
    await _make_run("SuiteB", ["failed", "failed", "failed"])

    resp = await client.get(
        "/api/v1/projects/stats",
        params={"project_ids": [project_id]},
        headers=admin_headers,
    )
    row = resp.json()["items"][0]
    assert row["pass_rate"] == pytest.approx(0.5)


async def test_bulk_stats_matches_single_project_stats(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    project_id = await _seed_project_with_run_and_results(
        client, admin_headers, "Parity", pass_count=2, fail_count=2
    )

    single = (
        await client.get(
            f"/api/v1/projects/{project_id}/stats", headers=admin_headers
        )
    ).json()
    bulk = (
        await client.get(
            "/api/v1/projects/stats",
            params={"project_ids": [project_id]},
            headers=admin_headers,
        )
    ).json()

    assert bulk["total"] == 1
    row = bulk["items"][0]
    assert row["project_id"] == project_id
    assert row["total_test_cases"] == single["total_test_cases"]
    assert row["total_test_suites"] == single["total_test_suites"]
    assert row["total_test_runs"] == single["total_test_runs"]
    assert row["pass_rate"] == single["pass_rate"]


async def test_bulk_stats_project_ids_filter(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    alpha = await _seed_project_with_run_and_results(
        client, admin_headers, "FilterAlpha", pass_count=1, fail_count=0
    )
    beta = await _seed_project_with_run_and_results(
        client, admin_headers, "FilterBeta", pass_count=0, fail_count=1
    )

    resp = await client.get(
        "/api/v1/projects/stats",
        params={"project_ids": [alpha]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    ids = {row["project_id"] for row in resp.json()["items"]}
    assert alpha in ids
    assert beta not in ids


async def test_bulk_stats_include_archived(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    active = (
        await client.post(
            "/api/v1/projects", json={"name": "ArchiveActive"}, headers=admin_headers
        )
    ).json()
    archived = (
        await client.post(
            "/api/v1/projects", json={"name": "ArchiveOff"}, headers=admin_headers
        )
    ).json()
    await client.put(
        f"/api/v1/projects/{archived['id']}",
        json={"is_archived": True},
        headers=admin_headers,
    )

    default_resp = await client.get("/api/v1/projects/stats", headers=admin_headers)
    default_ids = {row["project_id"] for row in default_resp.json()["items"]}
    assert active["id"] in default_ids
    assert archived["id"] not in default_ids

    with_archived = await client.get(
        "/api/v1/projects/stats",
        params={"include_archived": True},
        headers=admin_headers,
    )
    all_ids = {row["project_id"] for row in with_archived.json()["items"]}
    assert archived["id"] in all_ids


async def test_bulk_stats_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/projects/stats")
    assert resp.status_code == 401
