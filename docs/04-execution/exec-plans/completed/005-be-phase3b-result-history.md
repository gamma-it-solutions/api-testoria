# Execution Plan: 005 — Result History

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: CRITICAL — blocks frontend integration

---

## Goal

Create the `result_history` table and append a row every time a `TestResult` status changes, then expose `GET /test-results/{id}/history` so the frontend can render the audit trail.

---

## Context

The frontend `TestResultHistory` type and `getTestResultHistory()` API call exist and are wired into `stores/testResults`. Without this endpoint, the result history panel returns 404 and the feature is broken at integration time. There is currently no `result_history` table, no model, no service method, and no endpoint.

---

## Scope

### In scope
- `result_history` table via Alembic migration
- `ResultHistory` SQLAlchemy model
- `record_history()` service method called on every result status change
- `get_history()` service method returning sorted history
- `GET /test-results/{id}/history` endpoint
- Integration tests

### Out of scope
- Modifying how history is displayed in the frontend
- History retention / purging policy (future)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| models | `app/models/result_history.py` | New `ResultHistory` model |
| migration | `alembic/versions/` | New revision: `result_history` table |
| schemas | `app/schemas/test_result.py` | New `TestResultHistoryResponse` schema |
| services | `app/services/test_result_service.py` | `record_history()` + `get_history()` |
| router | `app/api/v1/test_results.py` | `GET /{result_id}/history` |
| tests | `tests/integration/test_test_results_api.py` | History endpoint tests |
| docs | `docs/06-generated/endpoints.md` | Add endpoint row |
| docs | `docs/06-generated/db-schema.md` | Add `result_history` table |

### Schema

```sql
CREATE TABLE result_history (
    id SERIAL PRIMARY KEY,
    test_result_id INTEGER REFERENCES test_results(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL,
    comment TEXT,
    changed_by INTEGER REFERENCES users(id),
    changed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_result_history_result_id ON result_history(test_result_id);
```

### History write trigger

Every call to `TestResultService.submit()` or `update()` that changes `status` appends a `ResultHistory` row:

```python
@staticmethod
async def record_history(db, result_id, status, comment, user_id):
    entry = ResultHistory(
        test_result_id=result_id, status=status,
        comment=comment, changed_by=user_id
    )
    db.add(entry)
    # flushed as part of the parent transaction
```

---

## Tasks

### Implementation
- [ ] Create `app/models/result_history.py`
- [ ] Import `ResultHistory` in `alembic/env.py`
- [ ] `alembic revision --autogenerate -m "add result_history table"`
- [ ] Review generated migration; apply with `alembic upgrade head`
- [ ] Add `TestResultHistoryResponse` schema to `app/schemas/test_result.py`
- [ ] Implement `TestResultService.record_history()` in `test_result_service.py`
- [ ] Implement `TestResultService.get_history(db, result_id)` returning sorted rows
- [ ] Call `record_history()` in `TestResultService.submit()` on status change
- [ ] Add `GET /{result_id}/history` to `app/api/v1/test_results.py`

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `docs/06-generated/endpoints.md` — add history endpoint row
- [ ] `docs/06-generated/db-schema.md` — add `result_history` table
- [ ] `docs/04-execution/tech-debt.md` — mark this item resolved
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `GET /test-results/{id}/history` returns rows ordered by `changed_at ASC`
- [ ] A row is written every time a result's status changes (including initial submission)
- [ ] History rows survive result updates — they are append-only
- [ ] Migration applies cleanly and rolls back with `alembic downgrade -1`
- [ ] Auth enforced: 401 without token
- [ ] 404 when `result_id` does not exist
