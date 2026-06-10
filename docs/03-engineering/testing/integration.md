# Integration Tests

Testing API endpoints with a real database in Testoria.

---

## What to integration test

- HTTP status codes for happy path and error paths
- Response body shapes (required fields present, correct types)
- Authentication enforcement (401 when no token, 403 when wrong role)
- DB state after mutations (create, update, delete)

---

## Test pattern

```python
# tests/integration/test_projects_api.py
import pytest
from httpx import AsyncClient

async def test_create_project(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Alpha", "key": "ALPHA", "description": "Test project"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Alpha"
    assert data["key"] == "ALPHA"
    assert "id" in data

async def test_create_project_requires_auth(client: AsyncClient):
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Alpha", "key": "ALPHA"},
    )
    assert response.status_code == 401

async def test_get_nonexistent_project_returns_404(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/v1/projects/99999", headers=auth_headers)
    assert response.status_code == 404
```

---

## Testing role enforcement

```python
async def test_delete_project_requires_project_manager(
    client: AsyncClient,
    auth_headers: dict,      # tester role
    admin_headers: dict,
    db_session,
):
    # Create a project as admin
    create_resp = await client.post(
        "/api/v1/projects",
        json={"name": "To Delete", "key": "TDEL"},
        headers=admin_headers,
    )
    project_id = create_resp.json()["id"]

    # Tester cannot delete
    resp = await client.delete(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert resp.status_code == 403

    # Admin can delete
    resp = await client.delete(f"/api/v1/projects/{project_id}", headers=admin_headers)
    assert resp.status_code == 204
```

---

## Testing file upload

```python
async def test_upload_attachment(client: AsyncClient, auth_headers: dict):
    files = {"file": ("screenshot.png", b"fake-png-data", "image/png")}
    response = await client.post(
        "/api/v1/test-results/1/attachments",
        files=files,
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "screenshot.png"
```

---

## Testing pagination

```python
async def test_list_test_cases_pagination(client: AsyncClient, auth_headers: dict, project_id: int):
    response = await client.get(
        f"/api/v1/projects/{project_id}/test-cases",
        params={"page": 1, "page_size": 5},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "total_pages" in data
    assert len(data["items"]) <= 5
```

---

## Test isolation

Each test function gets a fresh `db_session` that rolls back after the test. This means:

- Tests are isolated — no state leaks between them
- Order-independent — tests can run in any order
- Fast — no `TRUNCATE` between tests, just a rollback

**Important**: if a test commits explicitly (`await db.commit()`), the rollback on teardown cannot undo it. Avoid explicit commits in tests — rely on the `get_db` auto-commit in the app layer.
