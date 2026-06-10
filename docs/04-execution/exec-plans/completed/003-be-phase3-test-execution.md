# Execution Plan: 003 — Backend Phase 3: Test Execution

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: CRITICAL
**Dependency**: 013-be-phase2-core-test-management must be complete

---

## Goal

Implement the test execution domain: Milestones, Test Runs, Test Results (with upsert semantics), file attachments, and run progress calculation.

---

## Context

Phase 3 of `backend-implementation.md`. This is the core execution loop — testers record pass/fail against test cases within a run. The frontend's execution view depends entirely on these endpoints being available and correctly shaped.

Note: Phase 3 Amendment gaps (message/stack_trace fields, result_history, run/cases endpoint, attachment delete) are tracked separately in plans 001–004 and should be implemented alongside or immediately after this phase.

---

## Scope

### In scope

**Models & migrations:**
- `app/models/milestone.py` — Milestone
- `app/models/test_run.py` — TestRun (project_id, suite_id, milestone_id, config JSONB, status, assigned_to)
- `app/models/test_result.py` — TestResult (UNIQUE test_run_id + test_case_id, status, comment, execution_time, defects JSONB, tested_by)
- `app/models/result_attachment.py` — ResultAttachment (filename, file_path, file_size, mime_type)
- Alembic migration for all new tables

**Schemas:**
- `app/schemas/milestone.py` — MilestoneCreate, MilestoneUpdate, MilestoneResponse
- `app/schemas/test_run.py` — TestRunCreate, TestRunUpdate, TestRunResponse, TestRunProgress
- `app/schemas/test_result.py` — TestResultCreate, TestResultUpdate, TestResultResponse

**Services:**
- `app/services/test_run_service.py` — CRUD + close + progress calculation
- `app/services/test_result_service.py` — submit (upsert), update, get, progress

**Routers:**
- `app/api/v1/milestones.py`
- `app/api/v1/test_runs.py` — includes progress endpoint
- `app/api/v1/test_results.py` — includes attachment upload

**Tests:**
- `tests/integration/test_milestones_api.py`
- `tests/integration/test_test_runs_api.py`
- `tests/integration/test_test_results_api.py`

### Out of scope
- result_history (plan 001)
- message/stack_trace fields (plan 002)
- GET /test-runs/{id}/cases (plan 003)
- DELETE attachment (plan 004)
- Real-time publish calls (plan 005)

---

## Technical approach

### Endpoints

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| GET | `/projects/{id}/milestones` | viewer | List milestones |
| POST | `/projects/{id}/milestones` | project_manager | Create milestone |
| PUT | `/milestones/{id}` | project_manager | Update milestone |
| DELETE | `/milestones/{id}` | project_manager | Delete milestone |
| GET | `/projects/{id}/test-runs` | viewer | List runs (filter by status) |
| POST | `/projects/{id}/test-runs` | tester | Create run |
| GET | `/test-runs/{id}` | viewer | Get run details |
| PUT | `/test-runs/{id}` | tester | Update run (name, status, config) |
| DELETE | `/test-runs/{id}` | project_manager | Delete run |
| POST | `/test-runs/{id}/close` | tester | Set status=Completed, set completed_at |
| GET | `/test-runs/{id}/progress` | viewer | Pass/fail/blocked/skipped counts + % |
| GET | `/test-runs/{id}/results` | viewer | All results for a run |
| POST | `/test-runs/{id}/results` | tester | Submit result (upsert) |
| GET | `/test-results/{id}` | viewer | Get single result |
| PUT | `/test-results/{id}` | tester | Update result |
| POST | `/test-results/{id}/attachments` | tester | Upload file |

### Upsert semantics for TestResult

TestResult has `UNIQUE(test_run_id, test_case_id)`. Submit is an upsert:

```python
@staticmethod
async def submit(db, run_id, data, user_id):
    existing = await db.execute(
        select(TestResult).where(
            TestResult.test_run_id == run_id,
            TestResult.test_case_id == data.test_case_id
        )
    )
    result = existing.scalar_one_or_none()
    if result:
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(result, k, v)
        result.tested_by = user_id
        result.tested_at = datetime.utcnow()
    else:
        result = TestResult(**data.model_dump(), test_run_id=run_id, tested_by=user_id)
        db.add(result)
    await db.flush()
    await db.refresh(result)
    return result
```

### Progress calculation

```python
@staticmethod
async def get_progress(db, run_id):
    rows = await db.execute(
        select(TestResult.status, func.count(TestResult.id))
        .where(TestResult.test_run_id == run_id)
        .group_by(TestResult.status)
    )
    counts = {row.status: row.count for row in rows}
    total_cases = await db.scalar(...)  # total test cases in suite
    return {
        "passed": counts.get("Passed", 0),
        "failed": counts.get("Failed", 0),
        "blocked": counts.get("Blocked", 0),
        "skipped": counts.get("Skipped", 0),
        "untested": total_cases - sum(counts.values()),
        "total": total_cases,
    }
```

### File attachment upload

```python
@router.post("/{result_id}/attachments", status_code=201)
async def upload_attachment(
    result_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("tester")),
):
    # Save file to UPLOAD_DIR/{result_id}/
    # Create ResultAttachment row
    # Return attachment response
```

---

## Tasks

### Models & migration
- [ ] Write `app/models/milestone.py`
- [ ] Write `app/models/test_run.py` (config as JSONB)
- [ ] Write `app/models/test_result.py` (UNIQUE constraint, defects JSONB)
- [ ] Write `app/models/result_attachment.py`
- [ ] `alembic revision --autogenerate -m "Add milestones test_runs test_results attachments"` — review and apply

### Schemas
- [ ] Write `app/schemas/milestone.py`
- [ ] Write `app/schemas/test_run.py` — include TestRunProgress
- [ ] Write `app/schemas/test_result.py` — TestResultCreate, TestResultUpdate, TestResultResponse

### Services
- [ ] Write `app/services/test_run_service.py` — CRUD, close (set status + completed_at), progress
- [ ] Write `app/services/test_result_service.py` — submit (upsert), update, get, progress counts

### Routers
- [ ] Write `app/api/v1/milestones.py`
- [ ] Write `app/api/v1/test_runs.py` — all run endpoints + close + progress
- [ ] Write `app/api/v1/test_results.py` — submit, update, get, attachment upload
- [ ] Register all three routers in `app/main.py`

### Tests
- [ ] `tests/integration/test_milestones_api.py` — CRUD, 401/403
- [ ] `tests/integration/test_test_runs_api.py`:
  - Create run → 201
  - Close run → status=Completed, completed_at set
  - Progress returns correct counts
  - 404 for unknown run
- [ ] `tests/integration/test_test_results_api.py`:
  - Submit result → 201
  - Submit same case again → 200 (upsert, status updated)
  - Upload attachment → 201, file on disk
  - 401/403/404 cases

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `api/docs/06-generated/endpoints.md` — verify milestones/runs/results rows
- [ ] `api/docs/06-generated/db-schema.md` — add milestones, test_runs, test_results, result_attachments tables
- [ ] Move to `completed/`

---

## Definition of done

- [ ] Test runs can be created, listed, updated, closed
- [ ] `POST /test-runs/{id}/results` creates on first call, updates on second (upsert by test_case_id)
- [ ] `GET /test-runs/{id}/progress` returns accurate pass/fail/blocked/skipped/untested counts
- [ ] File upload stores file on disk and creates DB row
- [ ] `POST /test-runs/{id}/close` sets status=Completed and completed_at timestamp
- [ ] 401 without auth; 403 for viewer trying to submit; 404 for unknown IDs
- [ ] Integration tests pass with >85% coverage on execution endpoints
