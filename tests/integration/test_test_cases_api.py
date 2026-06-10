import csv
import io

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="tc_lead",
        email="tc_lead@example.com",
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
        username="tc_readonly",
        email="tc_readonly@example.com",
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
        "/api/v1/projects", json={"name": "TC Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def suite_id(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "TC Suite"},
        headers=lead_headers,
    )
    return int(resp.json()["id"])


def _make_case_payload(suite_id: int, title: str = "Login Test") -> dict[str, object]:
    return {
        "suite_id": suite_id,
        "title": title,
        "description": "Test login flow",
        "steps": [
            {"step": "Open browser", "expected": "Browser opens"},
            {"step": "Navigate to /login", "expected": "Login page shown"},
        ],
        "priority": "high",
        "type": "manual",
        "status": "active",
        "tags": ["smoke", "login"],
    }


# --- GET /projects/{id}/test-cases ---


async def test_list_test_cases_empty(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases", headers=lead_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_list_test_cases_no_token(client: AsyncClient, project_id: int) -> None:
    response = await client.get(f"/api/v1/projects/{project_id}/test-cases")
    assert response.status_code == 401


# --- POST /projects/{id}/test-cases ---


async def test_create_test_case(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id),
        headers=lead_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Login Test"
    assert data["priority"] == "high"
    assert len(data["steps"]) == 2
    assert {t["name"] for t in data["tags"]} == {"smoke", "login"}
    assert all(isinstance(t["id"], int) for t in data["tags"])


async def test_create_test_case_read_only_forbidden(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    read_only_headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id),
        headers=read_only_headers,
    )
    assert response.status_code == 403


# --- GET /test-cases/{id} ---


async def test_get_test_case(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id, "FetchMe"),
        headers=lead_headers,
    )
    tc_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/test-cases/{tc_id}", headers=lead_headers)
    assert response.status_code == 200
    assert response.json()["id"] == tc_id


async def test_get_test_case_not_found(
    client: AsyncClient, lead_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/test-cases/999999", headers=lead_headers)
    assert response.status_code == 404


# --- PUT /test-cases/{id} ---


async def test_update_test_case(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id),
        headers=lead_headers,
    )
    tc_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/test-cases/{tc_id}",
        json={"title": "Updated Title", "priority": "critical", "tags": ["regression"]},
        headers=lead_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["priority"] == "critical"
    assert [t["name"] for t in data["tags"]] == ["regression"]


# --- DELETE /test-cases/{id} ---


async def test_delete_test_case(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id, "ToDelete"),
        headers=lead_headers,
    )
    tc_id = create_resp.json()["id"]

    response = await client.delete(f"/api/v1/test-cases/{tc_id}", headers=lead_headers)
    assert response.status_code == 204

    get_resp = await client.get(f"/api/v1/test-cases/{tc_id}", headers=lead_headers)
    assert get_resp.status_code == 404


# --- Search and filter ---


async def test_search_by_title(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id, "Unique Search Title XYZ"),
        headers=lead_headers,
    )
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id, "Other Case"),
        headers=lead_headers,
    )

    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases",
        params={"search": "XYZ"},
        headers=lead_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Unique Search Title XYZ"


async def test_filter_by_priority(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    payload = _make_case_payload(suite_id, "Critical Case")
    payload["priority"] = "critical"
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=payload,
        headers=lead_headers,
    )

    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases",
        params={"priority": "critical"},
        headers=lead_headers,
    )
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["priority"] == "critical"


async def test_filter_by_type(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    payload = _make_case_payload(suite_id, "Automated Case")
    payload["type"] = "automated"
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=payload,
        headers=lead_headers,
    )

    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases",
        params={"type": "automated"},
        headers=lead_headers,
    )
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["type"] == "automated"


async def test_filter_by_tag_ids_single(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    smoke_payload = _make_case_payload(suite_id, "Smoke only")
    smoke_payload["tags"] = ["smoke"]
    regression_payload = _make_case_payload(suite_id, "Regression only")
    regression_payload["tags"] = ["regression"]
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=smoke_payload,
        headers=lead_headers,
    )
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=regression_payload,
        headers=lead_headers,
    )

    tags_resp = await client.get("/api/v1/tags", headers=lead_headers)
    tag_map = {t["name"]: t["id"] for t in tags_resp.json()}
    smoke_id = tag_map["smoke"]

    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases",
        params={"tag_ids": smoke_id},
        headers=lead_headers,
    )
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()["items"]]
    assert "Smoke only" in titles
    assert "Regression only" not in titles


async def test_filter_by_tag_ids_multiple_or_semantics(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    a_payload = _make_case_payload(suite_id, "Has A")
    a_payload["tags"] = ["alpha"]
    b_payload = _make_case_payload(suite_id, "Has B")
    b_payload["tags"] = ["beta"]
    both_payload = _make_case_payload(suite_id, "Has both")
    both_payload["tags"] = ["alpha", "beta"]
    neither_payload = _make_case_payload(suite_id, "Has neither")
    neither_payload["tags"] = ["gamma"]
    for payload in (a_payload, b_payload, both_payload, neither_payload):
        await client.post(
            f"/api/v1/projects/{project_id}/test-cases",
            json=payload,
            headers=lead_headers,
        )

    tags_resp = await client.get("/api/v1/tags", headers=lead_headers)
    tag_map = {t["name"]: t["id"] for t in tags_resp.json()}
    alpha_id = tag_map["alpha"]
    beta_id = tag_map["beta"]

    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases",
        params=[("tag_ids", alpha_id), ("tag_ids", beta_id)],
        headers=lead_headers,
    )
    assert response.status_code == 200
    data = response.json()
    titles = {c["title"] for c in data["items"]}
    assert titles == {"Has A", "Has B", "Has both"}
    assert data["total"] == 3


# --- automation_id ---


async def test_create_with_automation_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    payload = _make_case_payload(suite_id, "Automated Login")
    payload["automation_id"] = "tests/auth/test_login.py::test_success"
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=payload,
        headers=lead_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["automation_id"] == "tests/auth/test_login.py::test_success"


async def test_create_without_automation_id_returns_null(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    payload = _make_case_payload(suite_id, "No automation")
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=payload,
        headers=lead_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["automation_id"] is None


async def test_empty_string_automation_id_becomes_null(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    payload = _make_case_payload(suite_id, "Empty automation")
    payload["automation_id"] = ""
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=payload,
        headers=lead_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["automation_id"] is None


async def test_update_automation_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    payload = _make_case_payload(suite_id, "Will update automation")
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=payload,
        headers=lead_headers,
    )
    tc_id = resp.json()["id"]
    assert resp.json()["type"] == "manual"

    update_resp = await client.put(
        f"/api/v1/test-cases/{tc_id}",
        json={"automation_id": "spec::login_flow"},
        headers=lead_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["automation_id"] == "spec::login_flow"
    assert update_resp.json()["type"] == "automated"


async def test_filter_by_automation_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    p1 = _make_case_payload(suite_id, "Auto A")
    p1["automation_id"] = "unique_auto_id_xyz"
    p2 = _make_case_payload(suite_id, "Auto B")
    p2["automation_id"] = "other_auto_id"
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases", json=p1, headers=lead_headers
    )
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases", json=p2, headers=lead_headers
    )

    resp = await client.get(
        f"/api/v1/projects/{project_id}/test-cases",
        params={"automation_id": "unique_auto_id_xyz"},
        headers=lead_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Auto A"


# --- Import CSV ---


async def test_import_csv(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf)
    headers_row = [
        "title",
        "description",
        "preconditions",
        "steps_json",
        "priority",
        "type",
        "status",
        "suite_id",
        "tags",
    ]
    writer.writerow(headers_row)
    writer.writerow(
        [
            "Imported Case 1",
            "desc",
            "",
            '[{"step":"Do X","expected":"Y"}]',
            "medium",
            "manual",
            "draft",
            str(suite_id),
            "import",
        ]
    )
    writer.writerow(
        [
            "Imported Case 2",
            "",
            "",
            "[]",
            "low",
            "automated",
            "active",
            str(suite_id),
            "",
        ]
    )
    csv_bytes = buf.getvalue().encode("utf-8")

    response = await client.post(
        f"/api/v1/projects/{project_id}/test-cases/import",
        files={"file": ("cases.csv", csv_bytes, "text/csv")},
        headers=lead_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 2
    assert data["errors"] == []


async def test_import_csv_invalid_suite(
    client: AsyncClient,
    project_id: int,
    lead_headers: dict[str, str],
) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["title", "suite_id", "priority", "type", "status"])
    writer.writerow(["Bad Case", "999999", "medium", "manual", "draft"])
    csv_bytes = buf.getvalue().encode("utf-8")

    response = await client.post(
        f"/api/v1/projects/{project_id}/test-cases/import",
        files={"file": ("cases.csv", csv_bytes, "text/csv")},
        headers=lead_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 0
    assert len(data["errors"]) == 1


# --- Export CSV ---


async def test_export_csv(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> None:
    await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json=_make_case_payload(suite_id, "Export Me"),
        headers=lead_headers,
    )

    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases/export",
        params={"format": "csv"},
        headers=lead_headers,
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    content = response.content.decode("utf-8")
    assert "Export Me" in content


async def test_export_read_only_allowed(
    client: AsyncClient,
    project_id: int,
    read_only_headers: dict[str, str],
) -> None:
    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases/export",
        params={"format": "csv"},
        headers=read_only_headers,
    )
    assert response.status_code == 200
