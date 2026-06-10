# Execution Plan: 012 — Backend Phase 8: Advanced Features (RBAC, Audit Log, Import/Export)

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: MEDIUM
**Dependency**: 013-be-phase2-core-test-management, 014-be-phase3-test-execution must be complete

---

## Goal

Implement advanced features: full RBAC permission enforcement across all endpoints, audit logging for security-critical actions, and bulk CSV/Excel import/export for test cases and results.

---

## Context

Phase 8 of `backend-implementation.md`. Phases 1–7 implement the features; this phase hardens them. RBAC is partially applied (require_role by string) in earlier phases — this plan makes the permission system explicit via a Permission enum and a Role→Permission mapping. Audit logging records every CREATE/UPDATE/DELETE/LOGIN event for compliance.

---

## Scope

### In scope

**RBAC:**
- `app/core/permissions.py` — Permission enum, ROLE_PERMISSIONS map, `has_permission()`, `require_permission()` dependency

**Audit logging:**
- `app/models/audit_log.py` — AuditLog model (user_id, action, entity_type, entity_id, changes JSONB, ip_address, user_agent, created_at)
- `app/services/audit_service.py` — `log_action()` helper called from service methods
- Alembic migration for `audit_logs` table

**Import/Export (CSV/Excel):**
- `app/services/import_service.py` — finalize: CSV + Excel (.xlsx) parsing → bulk create TestCases
- `app/services/export_service.py` — finalize: query TestCases → CSV bytes; TestResults → Excel bytes

**Bulk operations:**
- `POST /projects/{id}/test-cases/import` — already in Phase 2 router; wire to finalized ImportService
- `GET /projects/{id}/test-cases/export` — CSV/Excel
- `GET /test-runs/{id}/report?format=excel` — already in Phase 5; ensure it calls ExportService

**User management endpoints:**
- `app/api/v1/users.py` — list users, get user, update user (admin only), deactivate user

### Out of scope
- Per-project role assignments (global role only in this phase)
- Custom field implementation (can be Phase 8.x extension)

---

## Technical approach

### Permission enum

```python
class Permission(str, Enum):
    VIEW_PROJECT    = "view_project"
    EDIT_PROJECT    = "edit_project"
    DELETE_PROJECT  = "delete_project"
    CREATE_TEST_CASE = "create_test_case"
    EDIT_TEST_CASE  = "edit_test_case"
    DELETE_TEST_CASE = "delete_test_case"
    EXECUTE_TEST    = "execute_test"
    VIEW_REPORTS    = "view_reports"
    MANAGE_USERS    = "manage_users"

ROLE_PERMISSIONS: dict[str, list[Permission]] = {
    "admin":           list(Permission),
    "project_manager": [VIEW_PROJECT, EDIT_PROJECT, CREATE_TEST_CASE, EDIT_TEST_CASE, EXECUTE_TEST, VIEW_REPORTS],
    "tester":          [VIEW_PROJECT, EXECUTE_TEST, VIEW_REPORTS],
    "viewer":          [VIEW_PROJECT, VIEW_REPORTS],
}
```

### Audit logging

```python
# app/services/audit_service.py
@staticmethod
async def log_action(
    db: AsyncSession,
    user_id: int,
    action: str,             # "CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT"
    entity_type: str,        # "Project", "TestCase", "TestRun", "TestResult", "User"
    entity_id: int | None,
    changes: dict | None = None,
    request: Request | None = None,
):
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        changes=changes,
        ip_address=request.client.host if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    db.add(entry)
    # Called inside service methods before flush — no separate commit needed
```

Audit calls are added to service methods for: Project create/update/delete, TestCase create/update/delete, TestRun create/close/delete, User login/logout.

### Import service (finalized)

```python
@staticmethod
async def import_csv(db: AsyncSession, project_id: int, content: bytes, user_id: int) -> dict:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    created = 0
    for row in reader:
        # Find or create suite by name
        # Create TestCase from row fields
        created += 1
    return {"created": created}

@staticmethod
async def import_excel(db: AsyncSession, project_id: int, content: bytes, user_id: int) -> dict:
    wb = openpyxl.load_workbook(BytesIO(content))
    ws = wb.active
    # Similar to CSV but reads from worksheet rows
```

### User management endpoints

```python
# app/api/v1/users.py
GET  /users              # admin: list all users
GET  /users/{id}         # admin or self
PUT  /users/{id}         # admin: update role, is_active
DELETE /users/{id}       # admin: deactivate (set is_active=False, never hard delete)
```

---

## Tasks

### RBAC
- [ ] Write `app/core/permissions.py` — Permission enum, ROLE_PERMISSIONS, `has_permission()`, `require_permission()` FastAPI dependency
- [ ] Audit existing routers and replace bare `require_role("tester")` strings with `require_permission(Permission.EXECUTE_TEST)` where appropriate

### Audit logging
- [ ] Write `app/models/audit_log.py` — AuditLog model with INET column for ip_address
- [ ] `alembic revision --autogenerate -m "Add audit_logs"` — review and apply
- [ ] Write `app/services/audit_service.py` — `log_action()` helper
- [ ] Add `log_action()` calls to: AuthService (login/logout), ProjectService (create/update/delete), TestCaseService (create/update/delete), TestRunService (create/close/delete)

### Import/Export
- [ ] Finalize `app/services/import_service.py` — CSV and Excel (xlsx) parsing, bulk TestCase insert
- [ ] Finalize `app/services/export_service.py` — TestCase CSV export, TestResult Excel export
- [ ] Wire import/export services into test_cases router and reports router

### User management
- [ ] Write `app/api/v1/users.py` — list, get, update (admin), deactivate (admin)
- [ ] Register `users.router` in `app/main.py`

### Tests
- [ ] `tests/integration/test_permissions.py`:
  - viewer cannot POST /projects → 403
  - tester cannot DELETE /projects/{id} → 403
  - admin can do everything
- [ ] `tests/integration/test_audit_log.py`:
  - login creates audit entry action="LOGIN"
  - project create creates audit entry action="CREATE" entity_type="Project"
- [ ] `tests/integration/test_import_export.py`:
  - CSV import creates correct number of test cases
  - Excel import creates test cases
  - CSV export returns correct columns and row count
- [ ] `tests/integration/test_users_api.py`:
  - admin can list/update users
  - non-admin cannot update another user's role → 403

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `api/docs/06-generated/endpoints.md` — add user management rows
- [ ] `api/docs/06-generated/db-schema.md` — add audit_logs table
- [ ] Move to `completed/`

---

## Definition of done

- [ ] Permission enum maps all four roles to correct permission sets
- [ ] viewer cannot submit results or create projects (403)
- [ ] Audit log entry created for every login, project create/update/delete, test case create/update/delete
- [ ] CSV import accepts a file, creates test cases, returns created count
- [ ] Excel (.xlsx) import works identically to CSV import
- [ ] Admin can update user roles and deactivate accounts
- [ ] Integration tests pass with >80% coverage on these features
