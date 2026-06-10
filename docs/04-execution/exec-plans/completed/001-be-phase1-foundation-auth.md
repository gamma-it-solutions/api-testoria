# Execution Plan: 001 — Backend Phase 1: Foundation & Authentication

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: CRITICAL
**Dependency**: None — this is the base layer everything else builds on

---

## Goal

Bootstrap the FastAPI application: project structure, database connection, Alembic migrations, User model, JWT authentication endpoints, and the test fixture foundation.

---

## Context

Phase 1 of `backend-implementation.md`. Nothing else can be built until this is in place. Covers the entire application scaffold: settings, async SQLAlchemy engine, session factory, dependency injection, security helpers, and the auth router.

---

## Scope

### In scope
- `app/main.py` — FastAPI app, CORS, GZip middleware, router wiring, health check
- `app/config.py` — Pydantic Settings (DATABASE_URL, SECRET_KEY, REDIS_URL, CORS_ORIGINS, UPLOAD_DIR, pagination)
- `app/database.py` — async engine, `AsyncSessionLocal`, `Base`, `get_db` dependency
- `app/dependencies.py` — `get_current_user`, `require_role()`
- `app/core/security.py` — `verify_password`, `get_password_hash`, `create_access_token`, `create_refresh_token`, `decode_token`
- `app/core/exceptions.py` — custom exception classes
- `app/models/user.py` — `User` model (id, username, email, hashed_password, full_name, role, is_active, timestamps)
- `app/schemas/user.py` — `UserCreate`, `UserUpdate`, `UserResponse`
- `app/schemas/token.py` — `Token`, `TokenPayload`
- `app/api/v1/auth.py` — `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`, `POST /auth/logout`
- `alembic/` setup — `alembic init`, `env.py`, initial migration
- `tests/conftest.py` — test DB engine, `db_session`, `client`, `test_user`, `admin_user`, `auth_headers` fixtures
- `tests/integration/test_auth_api.py` — login success/failure, refresh, /me, protected endpoint returns 401
- `.env.example`
- `pyproject.toml` / `requirements.txt`

### Out of scope
- Any domain beyond users and auth (projects, test cases, etc.)
- Redis/Celery (wired in config but not used yet)

---

## Technical approach

### Key files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app with CORSMiddleware, GZipMiddleware, router includes |
| `app/config.py` | `class Settings(BaseSettings)` with `.env` loading |
| `app/database.py` | `create_async_engine` + `AsyncSessionLocal` + `get_db` |
| `app/dependencies.py` | `get_current_user` decodes JWT, loads User from DB; `require_role(role)` checks user.role |
| `app/core/security.py` | bcrypt via passlib, JWT via python-jose |
| `app/models/user.py` | SQLAlchemy User model |
| `app/api/v1/auth.py` | OAuth2PasswordRequestForm login, token refresh, /me |
| `alembic/env.py` | Async-compatible migration env with `run_migrations_online` |

### Auth flow

```
POST /auth/login (form: username + password)
  → verify_password → create_access_token + create_refresh_token → Token response

POST /auth/refresh (body: refresh_token)
  → decode_token (type=refresh) → new access + refresh tokens

GET /auth/me
  → get_current_user dependency → UserResponse

POST /auth/logout
  → get_current_user → {"message": "Successfully logged out"}
  (token blocklist via Redis is a tech-debt item, not Phase 1)
```

### RBAC pattern

```python
def require_role(*roles: str):
    async def checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user
    return checker
```

---

## Tasks

### Setup
- [ ] Initialize Python project: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`
- [ ] Create directory structure: `app/`, `app/api/v1/`, `app/models/`, `app/schemas/`, `app/core/`, `app/services/`, `app/tasks/`, `app/utils/`, `tests/`, `alembic/`
- [ ] Write `.env.example` with all required variables
- [ ] Initialize Alembic: `alembic init alembic`

### Core files
- [ ] Write `app/config.py` — `Settings(BaseSettings)` with all config variables
- [ ] Write `app/database.py` — async engine, session factory, `get_db`, `Base`
- [ ] Write `app/core/security.py` — password hashing + JWT create/decode/verify
- [ ] Write `app/core/exceptions.py` — custom exception classes
- [ ] Write `app/models/user.py` — `User` SQLAlchemy model
- [ ] Write `app/schemas/user.py` — `UserCreate`, `UserUpdate`, `UserResponse`
- [ ] Write `app/schemas/token.py` — `Token`, `TokenPayload`
- [ ] Write `app/dependencies.py` — `get_current_user`, `require_role`
- [ ] Write `app/api/v1/auth.py` — login, refresh, /me, logout
- [ ] Write `app/main.py` — FastAPI app, middleware, router includes, health check

### Database
- [ ] Configure `alembic/env.py` for async SQLAlchemy
- [ ] `alembic revision --autogenerate -m "Initial schema: users"` — review migration output
- [ ] `alembic upgrade head` — apply migration
- [ ] Verify table created: `\d users` in psql

### Tests
- [ ] Write `tests/conftest.py` — test DB engine, session, client, user fixtures, auth_headers
- [ ] Write `tests/integration/test_auth_api.py`:
  - `test_login_success` — 200, access_token + refresh_token in response
  - `test_login_wrong_password` — 401
  - `test_login_inactive_user` — 400
  - `test_refresh_token` — valid refresh → new access token
  - `test_refresh_with_access_token_fails` — 401
  - `test_get_me` — 200, returns user info
  - `test_protected_endpoint_without_token` — 401
  - `test_logout` — 200

### Quality check
- [ ] `pytest tests/integration/test_auth_api.py` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean
- [ ] `uvicorn app.main:app` starts without errors
- [ ] `/docs` renders OpenAPI UI

### Docs
- [ ] `api/docs/06-generated/endpoints.md` — verify auth section rows are accurate
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `uvicorn app.main:app --reload` starts successfully
- [ ] `GET /api/v1/health` returns `{"status": "healthy"}`
- [ ] `POST /api/v1/auth/login` returns access + refresh tokens for a valid user
- [ ] `GET /api/v1/auth/me` returns user info with valid access token
- [ ] Any endpoint with invalid/missing token returns 401
- [ ] `alembic upgrade head` applies cleanly; `alembic downgrade -1` rolls back cleanly
- [ ] All auth integration tests pass
- [ ] OpenAPI docs accessible at `/docs`
