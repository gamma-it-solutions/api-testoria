# API Layer

How FastAPI routers are structured in Testoria.

---

## Overview

```
HTTP request
     ↓
FastAPI router (app/api/v1/<domain>.py)
  - Depends(get_db)           → AsyncSession injected
  - Depends(require_role(...))→ authenticated User with role check
  - Pydantic schema           → request body validated
     ↓
Service function (app/services/<domain>_service.py)
     ↓
Pydantic response schema returned as JSON
```

---

## Router structure

Each domain has its own file in `app/api/v1/`. Routers are thin — they do three things only:

1. Declare HTTP method, path, and response schema
2. Call one service function
3. Return the result

```python
# app/api/v1/projects.py — example of the thin router pattern
router = APIRouter(prefix="/projects", tags=["projects"])

@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> ProjectResponse:
    project = await project_service.get_project(db, project_id)
    return ProjectResponse.model_validate(project)
```

No business logic in the router function body. The service raises `NotFoundError` (404) if the project doesn't exist.

---

## Dependency injection

### `get_db`

Defined in `app/database.py`. Yields an `AsyncSession` that auto-commits on success and rolls back on exception:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

### `get_current_user`

Defined in `app/dependencies.py`. Extracts and validates the Bearer token via `OAuth2PasswordBearer`, queries the user, raises 401 if invalid, 403 if `no_access` role.

### `require_role(*roles)`

Defined in `app/dependencies.py`. Returns a dependency that calls `get_current_user` and then checks the user's role against the allowed list. Raises 403 if the role doesn't match.

Standard role tuples used in routers:

```python
_VIEWER  = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_TESTER  = (UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_MANAGER = (UserRole.LEAD, UserRole.ADMIN)
_ADMIN   = (UserRole.ADMIN,)
```

---

## URL pattern

Nested resources use full paths in the router, not nested sub-routers:

```python
# app/api/v1/test_suites.py
@router.get("/projects/{project_id}/test-suites", ...)
@router.post("/projects/{project_id}/test-suites", ...)
@router.get("/test-suites/{suite_id}", ...)
@router.put("/test-suites/{suite_id}", ...)
@router.delete("/test-suites/{suite_id}", ...)
```

All routers are registered in `app/main.py` with prefix `/api/v1`:

```python
app.include_router(projects.router, prefix="/api/v1")
app.include_router(test_suites.router, prefix="/api/v1")
```

---

## Pagination

Use the `PaginatedResponse[T]` generic from `app/schemas/user.py`:

```python
@router.get("", response_model=PaginatedResponse[ProjectResponse])
async def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ...
) -> PaginatedResponse[ProjectResponse]:
    items, total = await project_service.list_projects(db, page=page, page_size=page_size)
    pages = math.ceil(total / page_size) if page_size else 1
    return PaginatedResponse(
        items=[ProjectResponse.model_validate(p) for p in items],
        total=total, page=page, page_size=page_size, pages=pages,
    )
```

---

## Error HTTP codes used

| Situation | HTTP code | Exception class |
|-----------|-----------|-----------------|
| Not found | 404 | `NotFoundError` |
| Unauthenticated | 401 | `UnauthorizedError` |
| Forbidden (wrong role) | 403 | `ForbiddenError` |
| Validation failure | 422 | (auto from Pydantic) |
| Conflict (duplicate key) | 409 | `ConflictError` |
| Bad request | 400 | `BadRequestError` |

All custom exceptions are in `app/core/exceptions.py`. Response shape: `{ "detail": "human-readable message" }`.

### Public, non-enumerating endpoints (plan 048)

`POST /auth/forgot-password`, `POST /auth/reset-password`, and
`GET /auth/reset-password/validate` are intentionally **public** (no
`Depends(get_current_user)`). `forgot-password` returns **202** unconditionally
(no user enumeration); the reset/validate routes return a generic **400** for any
invalid/expired/used token rather than leaking account state. Still thin routers:
they call `password_token_service` / `user_service` / `email_service` and return a
schema — the only logic is consume-then-set ordering.
