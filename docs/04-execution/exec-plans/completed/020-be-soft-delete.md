# Execution Plan: Soft Delete for All Entities

**Date**: 2026-04-08
**Author**: Claude
**Status**: Draft

---

## Goal

Replace hard deletes with soft deletes across all domain entities so that deleted data can be recovered and audit trails remain intact.

---

## Context

Every `DELETE` operation today issues `await db.delete(obj)`, which permanently removes the row and cascade-deletes children. This is destructive and irreversible — a misclick or API call permanently erases test history, results, and attachments. Soft delete preserves data integrity, supports "undo" / restore workflows, and keeps foreign-key references valid for historical reports and audit logs.

---

## Scope

### In scope

- Add `deleted_at` column (nullable `DateTime(timezone=True)`) to: **projects, test_suites, test_cases, test_runs, test_results, milestones, users**
- Add a reusable `SoftDeleteMixin` on `Base` to standardize the column + helper properties
- Single Alembic migration adding `deleted_at` to all seven tables (with index)
- Update all service `delete_*` functions to set `deleted_at = now()` instead of `db.delete()`
- Update all service `list_*` and `get_*` queries to filter out soft-deleted rows by default
- Add `include_deleted: bool = False` query parameter to list endpoints that need it (projects, test cases, test runs)
- Add `restore` service functions + `POST /{id}/restore` endpoints for entities that benefit from it (projects, test_suites, test_cases, test_runs, milestones)
- Cascade soft-delete: deleting a project soft-deletes its suites; deleting a suite soft-deletes its cases
- Audit log records soft-delete and restore actions
- Update Pydantic response schemas to include `deleted_at` field
- Unit and integration tests for soft-delete, restore, and filtering

### Out of scope

- Purge / permanent delete endpoint (deferred — future admin tool)
- Scheduled cleanup job to hard-delete records older than N days
- Soft-delete for **tags** (lightweight, shared entities — keep hard delete)
- Soft-delete for **result_attachments** (tied to result lifecycle, cascade is fine)
- Soft-delete for **result_history** (append-only audit trail — never deleted)
- Soft-delete for **audit_logs** (compliance trail — never deleted)
- Frontend UI changes (separate web-testoria plan)

---

## Technical approach

### 1. SoftDeleteMixin

Create `app/models/mixins.py`:

```python
from datetime import datetime
from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
```

Apply to: `Project`, `TestSuite`, `TestCase`, `TestRun`, `TestResult`, `Milestone`, `User`.

### 2. Query filtering helper

Create a helper in `app/models/mixins.py`:

```python
from sqlalchemy import ColumnElement

def not_deleted(model: type[SoftDeleteMixin]) -> ColumnElement[bool]:
    return model.deleted_at.is_(None)
```

All list/get queries add `.where(not_deleted(Model))` unless `include_deleted=True`.

### 3. Service changes pattern

Every `delete_*` function changes from:

```python
await db.delete(obj)
```

to:

```python
obj.deleted_at = func.now()
await db.flush()
```

Every `list_*` / `get_*` function adds `not_deleted()` filter. `get_*` functions take an optional `allow_deleted: bool = False` parameter for internal use (e.g., restore needs to find deleted rows).

### 4. Cascade soft-delete

- `delete_project` → also soft-deletes all suites in the project → which cascades to cases
- `delete_suite` → also soft-deletes all cases in the suite
- `delete_test_run` → also soft-deletes all results in the run

Cascade is done explicitly in the service layer (query + bulk update), not via DB triggers, keeping logic visible and testable.

### 5. Restore logic

- Restore sets `deleted_at = None`
- Restoring a project does **not** auto-restore children (explicit choice — user restores what they need)
- Restoring a child whose parent is deleted raises `400 Bad Request` ("restore the parent first")
- Audit log records `RESTORE` action

### 6. Remove CASCADE deletes from FK constraints

Current FK constraints use `ondelete="CASCADE"` which would still trigger hard deletes if a row were somehow removed. Since we're moving to soft delete, change cascade FKs on soft-deletable children to `ondelete="SET NULL"` or `ondelete="RESTRICT"` where appropriate, to prevent accidental hard cascades. This is done in the same migration.

**Changes:**

| FK | Current | New | Rationale |
|----|---------|-----|-----------|
| `test_suites.project_id` | CASCADE | RESTRICT | Prevent accidental hard delete; service handles cascade soft-delete |
| `test_suites.parent_suite_id` | CASCADE | SET NULL | Orphaned child suites become root suites if parent is hard-removed |
| `test_cases.suite_id` | CASCADE | RESTRICT | Service handles cascade soft-delete |
| `test_runs.project_id` | CASCADE | RESTRICT | Service handles cascade soft-delete |
| `test_results.test_run_id` | CASCADE | RESTRICT | Service handles cascade soft-delete |
| `test_results.test_case_id` | CASCADE | RESTRICT | Keep result even if case is soft-deleted |

FKs on non-soft-deletable entities (result_attachments, result_history) keep CASCADE since they follow their parent's lifecycle.

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| mixin | `app/models/mixins.py` (new) | `SoftDeleteMixin`, `not_deleted()` helper |
| models | `app/models/project.py`, `test_suite.py`, `test_case.py`, `test_run.py`, `test_result.py`, `milestone.py`, `user.py` | Add `SoftDeleteMixin` to class bases |
| migration | `alembic/versions/` | Add `deleted_at` column to 7 tables + indexes, alter FK ondelete constraints |
| schemas | `app/schemas/project.py`, `test_suite.py`, `test_case.py`, `test_run.py`, `test_result.py`, `milestone.py`, `user.py` | Add `deleted_at: datetime \| None` to response schemas |
| services | `app/services/project_service.py`, `test_suite_service.py`, `test_case_service.py`, `test_run_service.py`, `test_result_service.py`, `milestone_service.py`, `user_service.py` | Replace `db.delete()` with soft delete; add `not_deleted()` filters; add `restore_*` functions; add cascade soft-delete |
| routers | `app/api/v1/projects.py`, `test_suites.py`, `test_cases.py`, `test_runs.py`, `milestones.py` | Add `include_deleted` query param to list endpoints; add `POST /{id}/restore` endpoints |
| tests | `tests/unit/`, `tests/integration/` | Soft-delete, restore, cascade, filter tests |

### Key decisions

- **Mixin over Base class column**: Mixin is explicit — only opt-in models get `deleted_at`. Keeps tags, audit_logs, and other system tables clean.
- **Application-level cascade, not DB triggers**: Keeps cascade logic visible, testable, and consistent with the existing service-layer-owns-logic invariant.
- **`deleted_at` timestamp over boolean `is_deleted`**: Timestamp records *when* deletion occurred, useful for time-based purge policies and audit. `is_deleted` is derivable via the `is_deleted` property.
- **No auto-restore of children**: Prevents surprise data resurrection. Users explicitly restore what they need. Safer default.
- **FK constraint tightening (CASCADE → RESTRICT)**: Prevents accidental hard deletes from bypassing the soft-delete service layer. Any code that tries `db.delete()` on a parent with children will get a DB error — a safety net.
- **Index on `deleted_at`**: Most queries filter `WHERE deleted_at IS NULL`. A partial index on `deleted_at IS NOT NULL` would be optimal but standard index is simpler and Alembic-friendly. Can optimize later if needed.

---

## Tasks

### Implementation

- [ ] Create `app/models/mixins.py` with `SoftDeleteMixin` and `not_deleted()` helper
- [ ] Add `SoftDeleteMixin` to `Project`, `TestSuite`, `TestCase`, `TestRun`, `TestResult`, `Milestone`, `User` models
- [ ] Create Alembic migration: add `deleted_at` column (nullable, indexed) to 7 tables + alter FK ondelete constraints
- [ ] Review and apply migration (`alembic upgrade head`)
- [ ] Update `app/schemas/` — add `deleted_at: datetime | None` to all response schemas for affected entities
- [ ] Update `project_service.py` — soft delete, cascade to suites, `not_deleted` filters, `restore_project`
- [ ] Update `test_suite_service.py` — soft delete, cascade to cases, `not_deleted` filters, `restore_suite`
- [ ] Update `test_case_service.py` — soft delete, `not_deleted` filters, `restore_test_case`
- [ ] Update `test_run_service.py` — soft delete, cascade to results, `not_deleted` filters, `restore_run`
- [ ] Update `test_result_service.py` — soft delete, `not_deleted` filters (no restore endpoint — results follow run lifecycle)
- [ ] Update `milestone_service.py` — soft delete, `not_deleted` filters, `restore_milestone`
- [ ] Update `user_service.py` — soft delete, `not_deleted` filters (no restore endpoint — use `is_active` reactivation or admin action)
- [ ] Update `app/api/v1/projects.py` — `include_deleted` param, `POST /{id}/restore`
- [ ] Update `app/api/v1/test_suites.py` — `include_deleted` param, `POST /{id}/restore`
- [ ] Update `app/api/v1/test_cases.py` — `include_deleted` param, `POST /{id}/restore`
- [ ] Update `app/api/v1/test_runs.py` — `include_deleted` param, `POST /{id}/restore`
- [ ] Update `app/api/v1/milestones.py` — `include_deleted` param, `POST /{id}/restore`
- [ ] Update `report_service.py` — ensure dashboard/metrics queries exclude soft-deleted entities
- [ ] Update `ci_service.py` — ensure bulk import/badge queries exclude soft-deleted entities
- [ ] Write unit tests: soft delete sets `deleted_at`, `not_deleted` filter works, cascade soft-delete, restore, restore-blocked-by-deleted-parent
- [ ] Write integration tests: DELETE returns 204 + row still in DB, GET excludes deleted, `include_deleted=True` includes them, POST restore returns 200, 400 on restore with deleted parent, 404 on deleted entity GET

### Quality check

- [ ] `pytest` — all tests pass
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update

- [ ] `docs/06-generated/endpoints.md` — add restore endpoints, document `include_deleted` params
- [ ] `docs/06-generated/db-schema.md` — add `deleted_at` column to 7 tables, update FK constraints
- [ ] `docs/02-architecture/ARCHITECTURE.md` — update codemap with `mixins.py`, add soft-delete to invariants
- [ ] `docs/02-architecture/backend/data-layer.md` — document `SoftDeleteMixin` and `not_deleted()` pattern
- [ ] `docs/02-architecture/backend/service-layer.md` — document soft-delete and restore service patterns
- [ ] `docs/03-engineering/patterns/service-patterns.md` — add soft-delete pattern example
- [ ] `docs/08-decisions/changelog.md` — record soft-delete decisions (mixin vs base, app cascade vs trigger, RESTRICT FKs, no auto-restore)
- [ ] `docs/04-execution/tech-debt.md` — add item for future purge/permanent-delete job
- [ ] `docs/01-product/features/` — add or update soft-delete feature doc
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Queries that forget `not_deleted()` leak deleted data | Medium | Code review; grep for raw `select(Model)` without filter; consider adding a linter rule or service-layer base query builder |
| FK RESTRICT blocks legitimate operations | Low | Only applied where service cascade handles soft-delete; test all delete paths |
| Migration on large tables locks writes | Low (dev stage) | For production: use `ADD COLUMN ... DEFAULT NULL` which is non-blocking in Postgres; index creation uses `CONCURRENTLY` if needed |
| Cascade soft-delete misses a relationship | Low | Enumerate all FK relationships in migration review; integration tests verify children are soft-deleted |
| Existing reports/stats include deleted data after migration | Medium | Explicit audit of all `select()` queries in report_service, ci_service, and stats functions |

---

## Definition of done

- [ ] All seven models have `deleted_at` column via `SoftDeleteMixin`
- [ ] Migration applies cleanly and is reversible (`alembic downgrade -1`)
- [ ] All `delete_*` services set `deleted_at` instead of hard-deleting
- [ ] All `list_*` and `get_*` services exclude soft-deleted rows by default
- [ ] Cascade soft-delete works for project→suites→cases and run→results
- [ ] Restore endpoints work; restoring child with deleted parent returns 400
- [ ] `include_deleted` query param works on list endpoints
- [ ] Reports and stats exclude soft-deleted entities
- [ ] Audit log records SOFT_DELETE and RESTORE actions
- [ ] Unit test coverage ≥ 85% for new/changed service code
- [ ] Integration tests cover soft-delete, restore, cascade, filtering, and error cases
- [ ] All quality checks pass (pytest, ruff, mypy)
- [ ] Docs updated (endpoints, db-schema, architecture, patterns, changelog)
