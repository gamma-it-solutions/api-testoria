import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.core.security import create_access_token, get_password_hash
from app.models.user import User


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="api_lead",
        email="api_lead@example.com",
        hashed_password=get_password_hash("password"),
        role=UserRole.LEAD,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def no_access_user(db_session: AsyncSession) -> User:
    user = User(
        username="api_noaccess",
        email="api_noaccess@example.com",
        hashed_password=get_password_hash("password"),
        role=UserRole.NO_ACCESS,
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
def no_access_headers(no_access_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(no_access_user.id)})
    return {"Authorization": f"Bearer {token}"}


# --- GET /users ---


@pytest.mark.asyncio
async def test_list_users_admin(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/users", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_list_users_no_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_users_lead_allowed(
    client: AsyncClient, lead_user: User, lead_headers: dict[str, str]
) -> None:
    # Lead now has user management.
    response = await client.get("/api/v1/users", headers=lead_headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_users_tester_forbidden(
    client: AsyncClient, test_user: User, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/users", headers=auth_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_users_no_access(
    client: AsyncClient, no_access_user: User, no_access_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/users", headers=no_access_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_users_search(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/users", headers=admin_headers, params={"search": "adminuser"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any(u["username"] == "adminuser" for u in data["items"])


@pytest.mark.asyncio
async def test_list_users_filter_role(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/users", headers=admin_headers, params={"role": "admin"}
    )
    assert response.status_code == 200
    data = response.json()
    assert all(u["role"] == "admin" for u in data["items"])


# --- POST /users ---


@pytest.mark.asyncio
async def test_create_user_admin(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": "created_via_api",
            "email": "created_via_api@example.com",
            "role": "tester",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "created_via_api"
    assert data["role"] == "tester"
    assert "hashed_password" not in data
    assert "password" not in data


@pytest.mark.asyncio
async def test_create_user_duplicate(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": "adminuser",
            "email": "another@example.com",
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_user_lead_allowed(
    client: AsyncClient, lead_user: User, lead_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users",
        headers=lead_headers,
        json={"username": "lead_made", "email": "lead_made@x.com", "role": "tester"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "tester"


@pytest.mark.asyncio
async def test_create_user_tester_forbidden(
    client: AsyncClient, test_user: User, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": "x", "email": "x@x.com"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_user_lead_cannot_create_admin(
    client: AsyncClient, lead_user: User, lead_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users",
        headers=lead_headers,
        json={"username": "ladmin", "email": "ladmin@x.com", "role": "admin"},
    )
    assert response.status_code == 403


# --- POST /users/bulk ---


@pytest.mark.asyncio
async def test_bulk_create_users(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users/bulk",
        headers=admin_headers,
        json={
            "users": [
                {"username": "bapi1", "email": "bapi1@example.com"},
                {"username": "bapi2", "email": "bapi2@example.com"},
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 2
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_bulk_create_partial_failure(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/users/bulk",
        headers=admin_headers,
        json={
            "users": [
                {"username": "bapi_ok", "email": "bapi_ok@ex.com"},
                # duplicate username
                {"username": "adminuser", "email": "uniq@example.com"},
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 1
    assert len(data["errors"]) == 1
    err = data["errors"][0]
    assert err["index"] == 1
    assert err["username"] == "adminuser"
    assert err["email"] == "uniq@example.com"
    assert "adminuser" in err["detail"]


# --- GET /users/export ---


@pytest.mark.asyncio
async def test_export_csv(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/users/export", headers=admin_headers, params={"format": "csv"}
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "username" in response.text


@pytest.mark.asyncio
async def test_export_excel(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/users/export", headers=admin_headers, params={"format": "excel"}
    )
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    # XLSX zip magic bytes
    assert response.content[:2] == b"PK"


# --- GET /users/{id} ---


@pytest.mark.asyncio
async def test_get_user_admin(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.get(f"/api/v1/users/{admin_user.id}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["id"] == admin_user.id


@pytest.mark.asyncio
async def test_get_user_not_found(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/users/999999", headers=admin_headers)
    assert response.status_code == 404


# --- PUT /users/{id} ---


@pytest.mark.asyncio
async def test_update_user(
    client: AsyncClient, admin_user: User, admin_headers: dict[str, str]
) -> None:
    response = await client.put(
        f"/api/v1/users/{admin_user.id}",
        headers=admin_headers,
        json={"full_name": "Updated Admin"},
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Updated Admin"


@pytest.mark.asyncio
async def test_update_user_not_found(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.put(
        "/api/v1/users/999999",
        headers=admin_headers,
        json={"full_name": "Ghost"},
    )
    assert response.status_code == 404


# --- DELETE /users/{id} ---


@pytest.mark.asyncio
async def test_delete_user_success(
    client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    user = User(
        username="to_delete_api",
        email="to_delete_api@example.com",
        hashed_password=get_password_hash("p"),
        role=UserRole.TESTER,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    response = await client.delete(f"/api/v1/users/{user.id}", headers=admin_headers)
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_user_lead_blocked(
    client: AsyncClient,
    admin_headers: dict[str, str],
    lead_user: User,
) -> None:
    response = await client.delete(
        f"/api/v1/users/{lead_user.id}", headers=admin_headers
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_delete_user_not_found(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.delete("/api/v1/users/999999", headers=admin_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_lead_cannot_update_admin(
    client: AsyncClient,
    lead_user: User,
    lead_headers: dict[str, str],
    admin_user: User,
) -> None:
    response = await client.put(
        f"/api/v1/users/{admin_user.id}",
        headers=lead_headers,
        json={"full_name": "hijacked"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_lead_cannot_delete_admin(
    client: AsyncClient,
    lead_user: User,
    lead_headers: dict[str, str],
    admin_user: User,
) -> None:
    response = await client.delete(
        f"/api/v1/users/{admin_user.id}", headers=lead_headers
    )
    assert response.status_code == 403


# --- GET /roles ---


@pytest.mark.asyncio
async def test_list_roles_authenticated(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/roles", headers=auth_headers)
    assert response.status_code == 200
    roles = response.json()
    assert len(roles) == 5
    slugs = {r["slug"] for r in roles}
    assert slugs == {"no_access", "read_only", "tester", "lead", "admin"}
    lead = next(r for r in roles if r["slug"] == "lead")
    assert lead["is_default"] is True
    assert lead["is_deletable"] is False


@pytest.mark.asyncio
async def test_list_roles_unauthenticated(client: AsyncClient) -> None:
    response = await client.get("/api/v1/roles")
    assert response.status_code == 401
