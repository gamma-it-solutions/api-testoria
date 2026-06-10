# Execution Plan: User Roles & User Management

**Date**: 2026-03-24
**Author**: Claude
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Implement 5 predefined user roles with enforced permission levels, a full user management API (CRUD, bulk create, export, search/filter), and a registration endpoint.

---

## Context

The auth layer (login/refresh/me/logout) exists but there is no registration endpoint and no `app/api/v1/users.py` implementation. The `users.role` column stores a plain string with no constraint; current docs reference role names (`admin`, `project_manager`, `tester`, `viewer`) that do not match the product-defined role taxonomy. `require_role()` is defined but not called anywhere yet. This plan formalises the role model and delivers the complete user management surface.

---

## Role definitions

| Role | Slug | Default for new users | Can be deleted | Permission level |
|------|------|-----------------------|----------------|-----------------|
| No Access | `no_access` | No | Yes | Blocked at every protected route |
| Read Only | `read_only` | No | Yes | GET on all resources; no write operations |
| Tester | `tester` | No | Yes | Create/edit test cases, suites, results |
| Lead | `lead` | **Yes** | **No** (default fallback role) | Full resource access; no user management |
| Admin | `admin` | No | Yes | Full access including user management |

**Permission hierarchy (lowest → highest):** `no_access` < `read_only` < `tester` < `lead` < `admin`

### Role-to-operation mapping

| Operation | no_access | read_only | tester | lead | admin |
|-----------|-----------|-----------|--------|------|-------|
| View projects / suites / cases / runs / results | — | Yes | Yes | Yes | Yes |
| Create / edit test cases, suites | — | — | Yes | Yes | Yes |
| Add test results | — | — | Yes | Yes | Yes |
| Create / manage test runs | — | — | — | Yes | Yes |
| Delete test cases / suites / runs | — | — | — | Yes | Yes |
| Manage projects (create, edit, archive, delete) | — | — | — | Yes | Yes |
| User management (all `/users` endpoints) | — | — | — | — | Yes |

### Role rename from existing docs

The current `endpoints.md` documents roles `project_manager` and `viewer`. These are renamed:

| Old slug | New slug |
|----------|----------|
| `viewer` | `read_only` |
| `project_manager` | `lead` |
| `tester` | `tester` (unchanged) |
| `admin` | `admin` (unchanged) |

The Alembic migration must UPDATE existing rows to the new slugs.

---

## Scope

### In scope
- `app/core/roles.py` — `UserRole` enum with 5 values + role hierarchy helper
- Alembic migration: rename existing role slugs, set DEFAULT to `lead`, add CHECK constraint
- `app/schemas/user.py` — update `UserCreate.role` to `UserRole` enum; add `UserBulkCreate`, `UserExportRow`, `UserListFilters` schemas
- `app/services/user_service.py` — create, get, list (search + filter), update, delete, bulk_create, export_csv
- `app/api/v1/users.py` — all user management endpoints (see endpoint table below)
- `app/api/v1/auth.py` — add `POST /auth/register` (creates user, assigns `lead` as default role)
- `app/main.py` — wire users router
- `app/dependencies.py` — update `require_role()` to accept `UserRole` values; add `no_access` block at `get_current_user` level
- Unit tests: `tests/unit/test_user_service.py`
- Integration tests: `tests/integration/test_users_api.py`, `tests/integration/test_auth_register.py`

### Out of scope
- User Groups (`POST /groups`, group membership) — deferred to separate plan
- Custom Roles (`POST /roles`, persisted custom role definitions) — deferred to separate plan
- "Edit selected / edit all in view" bulk update — frontend concern; backend handles individual `PUT /users/{id}` calls
- Password reset / forgot-password flow — separate plan
- Role-based field visibility (hiding UI fields per role) — frontend concern

---

## New and changed endpoints

### Auth (`app/api/v1/auth.py`) — addition

| Method | Path | Auth | Input | Output |
|--------|------|------|-------|--------|
| POST | `/auth/register` | None | `UserCreate` | `UserResponse` 201 |

- Assigns `lead` as default role if `role` field omitted
- Returns 409 if username or email already taken
- Password is bcrypt-hashed before storage

### Users (`app/api/v1/users.py`) — new file

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/users` | admin | `search?, status?, role?, page?, page_size?` | `PaginatedResponse[UserResponse]` |
| POST | `/users` | admin | `UserCreate` | `UserResponse` 201 |
| POST | `/users/bulk` | admin | `UserBulkCreate` | `{ created: int, errors: [{ index, detail }] }` |
| GET | `/users/export` | admin | `format: csv\|excel` | file download |
| GET | `/users/{id}` | admin | — | `UserResponse` |
| PUT | `/users/{id}` | admin | `UserUpdate` | `UserResponse` |
| DELETE | `/users/{id}` | admin | — | 204 |

**Deletion constraint:** `DELETE /users/{id}` returns 409 if the target user holds the `lead` role AND it is the system default (i.e. `role == "lead"` with `is_default_role = True`). In practice: block delete if `user.role == "lead"` — the Lead role is the protected fallback. All other roles including `admin` can be deleted.

**Search / filter params for `GET /users`:**
- `search` — case-insensitive ILIKE on `username` OR `email` OR `full_name`
- `status` — `active` | `inactive` (maps to `is_active`)
- `role` — any `UserRole` slug
- `page`, `page_size` — standard pagination (default `page=1`, `page_size=20`)

### Roles (static, no DB table)

| Method | Path | Auth | Output |
|--------|------|------|--------|
| GET | `/roles` | Bearer | `RoleResponse[]` |

Returns the 5 predefined roles with metadata (slug, label, is_default, is_deletable, description). No DB persistence needed — response is built from `UserRole` enum.

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| core | `app/core/roles.py` | New `UserRole` StrEnum + `ROLE_HIERARCHY` dict |
| schemas | `app/schemas/user.py` | `role` field typed to `UserRole`; add `UserBulkCreate`, `RoleResponse` |
| models | `app/models/user.py` | `role` default changed to `"lead"` |
| migration | `alembic/versions/` | Rename role slugs + new DEFAULT + CHECK constraint |
| services | `app/services/user_service.py` | New — full CRUD + bulk + export |
| router (users) | `app/api/v1/users.py` | New — all user management endpoints + `/roles` |
| router (auth) | `app/api/v1/auth.py` | Add `POST /auth/register` |
| main | `app/main.py` | Register users router |
| dependencies | `app/dependencies.py` | `require_role()` accepts `UserRole`; block `no_access` in `get_current_user` |
| tests | `tests/unit/test_user_service.py` | Service unit tests |
| tests | `tests/integration/test_users_api.py` | API integration tests |
| tests | `tests/integration/test_auth_register.py` | Register endpoint tests |

### Key decisions

1. **`UserRole` as `StrEnum`** — subclassing `str` keeps it JSON-serialisable and compatible with SQLAlchemy's `String` column without a mapping layer. No DB enum type change needed.

2. **`no_access` blocked at `get_current_user`** — rather than sprinkling `no_access` checks across every router, `get_current_user` raises `ForbiddenError` if `user.role == UserRole.NO_ACCESS`. This means a `no_access` user can authenticate (get a token) but every subsequent protected call is rejected.

3. **Lead role deletion protection via service layer** — `user_service.delete()` checks `user.role == UserRole.LEAD` and raises a `ConflictError` ("Cannot delete a user with the Lead role"). This is enforced in the service, not in the router, per the layer invariant.

4. **CHECK constraint on `users.role`** — added in migration as `CHECK (role IN ('no_access','read_only','tester','lead','admin'))`. If custom roles are added later, this constraint is dropped in a subsequent migration.

5. **Bulk create** — processed in a single transaction; partial failures collected and returned as `errors` list (index + detail); successfully-created users are committed regardless of failures in other rows (i.e. best-effort, not all-or-nothing). If all-or-nothing semantics are needed, a flag `strict: bool = False` is added to `UserBulkCreate`.

6. **Export** — CSV uses Python `csv` stdlib; Excel uses `openpyxl`. Both stream via `StreamingResponse`. Columns: `id, username, email, full_name, role, is_active, created_at`.

7. **Register endpoint** — open (no auth required) to allow self-service sign-up. Assigns `lead` as default if no role provided. If the product requires admin-only user creation, the register endpoint can be disabled via config flag `REGISTRATION_OPEN=false` (returns 403). This flag defaults to `true` for now.

8. **Role rename migration** — uses `op.execute("UPDATE users SET role = ... WHERE role = ...")` to remap `project_manager` → `lead` and `viewer` → `read_only` before adding the CHECK constraint.

---

## Tasks

### Implementation

- [x] Create `app/core/roles.py` — `UserRole` StrEnum (`no_access`, `read_only`, `tester`, `lead`, `admin`) + `ROLE_HIERARCHY` dict + `ROLE_METADATA` (label, is_default, is_deletable, description)
- [x] Update `app/schemas/user.py` — `role: UserRole` on `UserCreate`/`UserUpdate`; add `UserBulkCreate`, `UserBulkCreateResult`, `RoleResponse`, `UserListFilters`
- [x] Update `app/models/user.py` — change `default="tester"` → `default=UserRole.LEAD`
- [x] Create Alembic migration — rename slugs (`project_manager`→`lead`, `viewer`→`read_only`), update DEFAULT, add CHECK constraint
- [x] Apply migration (`alembic upgrade head`)
- [x] Update `app/dependencies.py` — `get_current_user` blocks `no_access`; `require_role()` accepts `UserRole` values
- [x] Create `app/services/user_service.py`:
  - [x] `create_user(db, data: UserCreate) -> User` — hash password, check uniqueness
  - [x] `get_user(db, user_id: int) -> User`
  - [x] `list_users(db, filters: UserListFilters) -> tuple[list[User], int]`
  - [x] `update_user(db, user_id: int, data: UserUpdate) -> User`
  - [x] `delete_user(db, user_id: int) -> None` — blocks Lead role deletion
  - [x] `bulk_create_users(db, data: UserBulkCreate) -> UserBulkCreateResult`
  - [x] `export_users_csv(db, filters) -> AsyncGenerator[str, None]`
  - [x] `export_users_excel(db, filters) -> bytes`
- [x] Create `app/api/v1/users.py` — all endpoints per table above
- [x] Add `POST /auth/register` to `app/api/v1/auth.py` (calls `user_service.create_user`)
- [x] Add `GET /roles` endpoint in `app/api/v1/users.py`
- [x] Wire users router in `app/main.py`
- [x] Write unit tests `tests/unit/test_user_service.py`
- [x] Write integration tests `tests/integration/test_users_api.py`
- [x] Write integration tests `tests/integration/test_auth_register.py`

### Quality check

- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update

- [x] `docs/06-generated/endpoints.md` — add `/auth/register`, `/users/bulk`, `/users/export`, `/roles`; update role names in Min Role column across all tables
- [x] `docs/06-generated/db-schema.md` — update `users.role` notes column to new slugs + CHECK constraint
- [x] `docs/02-architecture/ARCHITECTURE.md` — update role list in auth section; add `app/core/roles.py` to codemap
- [x] `docs/08-decisions/changelog.md` — record role rename decision and Lead protection
- [x] `docs/04-execution/tech-debt.md` — no new debt expected; confirm
- [x] `docs/01-product/features/002-user-management.md` — create feature doc
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Existing seeded/test users have `role = 'project_manager'` or `'viewer'` — CHECK constraint fails at migration | Medium | Migration UPDATE runs before ALTER TABLE adds constraint |
| `openpyxl` not in requirements — Excel export fails | Low | Add `openpyxl` to `requirements.txt` before implementing export |
| `no_access` block in `get_current_user` breaks tests that create users without specifying role (default was `tester`, now `lead`) | Low | Update test fixtures to use explicit role or rely on new default |
| Bulk create with large payloads (1000+ users) blocks event loop | Low | Add `BULK_CREATE_MAX = 100` limit; document in schema validator |

---

## Definition of done

- [x] All 5 roles exist as `UserRole` enum values; `require_role()` and `get_current_user` enforce them correctly
- [x] `Lead` role users cannot be deleted — returns 409
- [x] `no_access` users receive 403 on every protected route after login
- [x] `POST /auth/register` creates a user with `lead` as default role; returns 409 on duplicate
- [x] `GET /users` supports `search`, `status`, `role` filter and returns paginated results
- [x] `POST /users/bulk` creates multiple users and returns per-row error detail for failures
- [x] `GET /users/export?format=csv` and `?format=excel` return downloadable files
- [x] `GET /roles` returns all 5 role definitions
- [x] Alembic migration applies cleanly, is reversible, and correctly renames existing slugs
- [x] Unit test coverage >= 85% for `user_service.py`
- [x] Integration tests cover: happy path, 401 (no token), 403 (wrong role / no_access), 404, 409 (duplicate user / lead delete)
- [x] All docs updated; plan moved to `completed/`
