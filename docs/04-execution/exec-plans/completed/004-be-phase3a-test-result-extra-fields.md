# Execution Plan: 004 — Test Result Extra Fields

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: CRITICAL — blocks frontend integration

---

## Goal

Add `message` and `stack_trace` columns to `test_results` so the frontend can store and display short error messages and automation failure stack traces.

---

## Context

The frontend `TestResult` type declares `message: string | null` and `stack_trace: string | null`. The current backend model has neither. When the frontend submits a result with these fields they are silently dropped; when it reads a result they come back `undefined`. This breaks the automated test result submission flow (used by the CLI tool and CI/CD integration).

---

## Scope

### In scope
- `message` (TEXT, nullable) column on `test_results`
- `stack_trace` (TEXT, nullable) column on `test_results`
- Alembic migration
- Pydantic schema updates (Create, Update, Response)
- Pass-through in `TestResultService.submit()` / `update()`

### Out of scope
- Frontend display changes
- Any character length limits (TEXT is unbounded; future constraint if needed)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| models | `app/models/test_result.py` | Add `message`, `stack_trace` columns |
| migration | `alembic/versions/` | New revision: add 2 nullable TEXT columns |
| schemas | `app/schemas/test_result.py` | Add fields to Create, Update, Response |
| docs | `docs/06-generated/db-schema.md` | Update `test_results` table |

### Model change

```python
# app/models/test_result.py
message = Column(Text, nullable=True)
stack_trace = Column(Text, nullable=True)
```

### Schema change

```python
# app/schemas/test_result.py
class TestResultCreate(BaseModel):
    # ... existing fields
    message: str | None = None
    stack_trace: str | None = None

class TestResultUpdate(BaseModel):
    # ... existing fields
    message: str | None = None
    stack_trace: str | None = None

class TestResultResponse(TestResultCreate):
    # ... existing fields — message and stack_trace inherited
    model_config = ConfigDict(from_attributes=True)
```

### Key decision

Both columns are nullable with no default. Adding nullable columns to an existing PostgreSQL table does not require a full table rewrite — migration will be fast even on large tables.

---

## Tasks

### Implementation
- [ ] Add `message` and `stack_trace` columns to `app/models/test_result.py`
- [ ] `alembic revision --autogenerate -m "add message stack_trace to test_results"`
- [ ] Review migration (should be two `op.add_column` calls); apply with `alembic upgrade head`
- [ ] Add `message` and `stack_trace` fields to `TestResultCreate`, `TestResultUpdate`, `TestResultResponse` in `app/schemas/test_result.py`
- [ ] Verify `TestResultService.submit()` passes new fields through via `data.model_dump()` (should be automatic)
- [ ] Write integration test: submit result with `message` + `stack_trace`, verify they appear in response

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `docs/06-generated/db-schema.md` — update `test_results` table (add 2 rows)
- [ ] `docs/04-execution/tech-debt.md` — mark this item resolved
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `POST /test-runs/{run_id}/results` accepts `message` and `stack_trace` and returns them
- [ ] `PUT /test-results/{id}` accepts partial update of these fields
- [ ] `GET /test-results/{id}` returns `message` and `stack_trace` (null if not set)
- [ ] Migration applies and rolls back cleanly
- [ ] Integration test passes
