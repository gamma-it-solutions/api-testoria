import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="suite_lead",
        email="suite_lead@example.com",
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
        username="suite_readonly",
        email="suite_readonly@example.com",
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


@pytest_asyncio.fixture
async def project_id(client: AsyncClient, lead_headers: dict[str, str]) -> int:
    resp = await client.post(
        "/api/v1/projects", json={"name": "Suite Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


# --- GET /projects/{id}/test-suites ---


async def test_list_suites_empty(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/api/v1/projects/{project_id}/test-suites", headers=lead_headers
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_list_suites_no_token(client: AsyncClient, project_id: int) -> None:
    response = await client.get(f"/api/v1/projects/{project_id}/test-suites")
    assert response.status_code == 401


# --- POST /projects/{id}/test-suites ---


async def test_create_suite(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Root Suite", "description": "Top level"},
        headers=lead_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Root Suite"
    assert data["project_id"] == project_id
    assert data["parent_suite_id"] is None


async def test_create_child_suite(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    parent_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Parent Suite"},
        headers=lead_headers,
    )
    parent_id = parent_resp.json()["id"]

    response = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Child Suite", "parent_suite_id": parent_id},
        headers=lead_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["parent_suite_id"] == parent_id


async def test_create_suite_read_only_forbidden(
    client: AsyncClient, project_id: int, read_only_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Blocked"},
        headers=read_only_headers,
    )
    assert response.status_code == 403


# --- GET /test-suites/{id} ---


async def test_get_suite(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "FetchMe"},
        headers=lead_headers,
    )
    suite_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/test-suites/{suite_id}", headers=lead_headers)
    assert response.status_code == 200
    assert response.json()["id"] == suite_id


async def test_get_suite_not_found(
    client: AsyncClient, lead_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/test-suites/999999", headers=lead_headers)
    assert response.status_code == 404


# --- PUT /test-suites/{id} ---


async def test_update_suite(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Old Suite"},
        headers=lead_headers,
    )
    suite_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/test-suites/{suite_id}",
        json={"name": "Updated Suite", "description": "New desc"},
        headers=lead_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Suite"


# --- DELETE /test-suites/{id} ---


async def test_delete_suite(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "ToDelete"},
        headers=lead_headers,
    )
    suite_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/test-suites/{suite_id}", headers=lead_headers
    )
    assert response.status_code == 204

    get_resp = await client.get(f"/api/v1/test-suites/{suite_id}", headers=lead_headers)
    assert get_resp.status_code == 404


async def test_delete_subtree_clears_project_stats_count(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    """TES-70 reproduction: deleting a parent suite cascades to subsections
    and their cases so `total_test_cases` doesn't inflate with orphans."""
    parent_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Section #1"},
        headers=lead_headers,
    )
    parent_id = parent_resp.json()["id"]

    child_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Subsection #1", "parent_suite_id": parent_id},
        headers=lead_headers,
    )
    child_id = child_resp.json()["id"]

    case_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": child_id,
            "title": "Orphan-prone case",
            "priority": "medium",
            "type": "manual",
        },
        headers=lead_headers,
    )
    assert case_resp.status_code == 201

    # Sanity: stats reflect 1 case before delete
    pre = await client.get(
        f"/api/v1/projects/{project_id}/stats", headers=lead_headers
    )
    assert pre.json()["total_test_cases"] == 1

    delete_resp = await client.delete(
        f"/api/v1/test-suites/{parent_id}", headers=lead_headers
    )
    assert delete_resp.status_code == 204

    # After cascade: stats are 0 cases, list endpoint returns nothing
    post = await client.get(
        f"/api/v1/projects/{project_id}/stats", headers=lead_headers
    )
    assert post.json()["total_test_cases"] == 0

    cases_resp = await client.get(
        f"/api/v1/projects/{project_id}/test-cases", headers=lead_headers
    )
    assert cases_resp.status_code == 200
    case_items = cases_resp.json()
    if isinstance(case_items, dict):
        case_items = case_items.get("items", [])
    assert case_items == []


async def test_delete_subtree_leaves_sibling_branch_visible(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    """Sibling branches of the deleted subtree must be unaffected."""
    a = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Branch A"},
        headers=lead_headers,
    )
    b = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Branch B"},
        headers=lead_headers,
    )
    a_id, b_id = a.json()["id"], b.json()["id"]

    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": a_id,
            "title": "Acase",
            "priority": "medium",
            "type": "manual",
        },
        headers=lead_headers,
    )
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": b_id,
            "title": "Bcase",
            "priority": "medium",
            "type": "manual",
        },
        headers=lead_headers,
    )

    delete_resp = await client.delete(
        f"/api/v1/test-suites/{a_id}", headers=lead_headers
    )
    assert delete_resp.status_code == 204

    stats = await client.get(
        f"/api/v1/projects/{project_id}/stats", headers=lead_headers
    )
    # Bcase survives
    assert stats.json()["total_test_cases"] == 1


async def test_list_suites_after_create(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Listed Suite"},
        headers=lead_headers,
    )
    response = await client.get(
        f"/api/v1/projects/{project_id}/test-suites", headers=lead_headers
    )
    assert response.status_code == 200
    names = [s["name"] for s in response.json()]
    assert "Listed Suite" in names


async def test_list_suites_sorted_by_display_order_then_created_at(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    for name, order in (
        ("Zulu", None),
        ("Alpha", 10),
        ("Beta", 5),
        ("Yankee", None),
    ):
        payload: dict[str, object] = {"name": name}
        if order is not None:
            payload["display_order"] = order
        await client.post(
            f"/api/v1/projects/{project_id}/test-suites",
            json=payload,
            headers=lead_headers,
        )

    resp = await client.get(
        f"/api/v1/projects/{project_id}/test-suites", headers=lead_headers
    )
    names = [s["name"] for s in resp.json()]
    # Beta (5), Alpha (10), Zulu (null, created first), Yankee (null, created later)
    assert names == ["Beta", "Alpha", "Zulu", "Yankee"]


async def test_list_suites_stable_across_repeat_calls(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    for name in ("S1", "S2", "S3"):
        await client.post(
            f"/api/v1/projects/{project_id}/test-suites",
            json={"name": name},
            headers=lead_headers,
        )
    first = await client.get(
        f"/api/v1/projects/{project_id}/test-suites", headers=lead_headers
    )
    second = await client.get(
        f"/api/v1/projects/{project_id}/test-suites", headers=lead_headers
    )
    assert [s["id"] for s in first.json()] == [s["id"] for s in second.json()]
