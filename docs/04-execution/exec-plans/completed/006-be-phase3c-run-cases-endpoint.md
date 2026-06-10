# Execution Plan: 006 — Run Cases Endpoint

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: CRITICAL — blocks frontend test execution view

---

## Goal

Add `GET /test-runs/{id}/cases` so the execution view can load a test run with all its test cases and their current results in a single request.

---

## Context

`TestRunExecutionView` calls `getTestRunWithCases()` which maps to `GET /test-runs/:id/cases`. This endpoint does not exist. Without it, the entire test execution feature is broken — testers cannot open a test run to record results.

The response must include the test run, every test case in the run's suite (from the top level of the suite tree), and for each case its current `TestResult` in this run (or `null` if not yet tested).

---

## Scope

### In scope
- `GET /test-runs/{run_id}/cases` endpoint
- `TestRunWithCases` Pydantic response schema
- Service method `TestRunService.get_with_cases(db, run_id)`
- Integration tests

### Out of scope
- Nested suite hierarchy flattening (return top-level suite cases only — all cases in the suite and its descendants)
- Pagination (all cases returned; add limit of 500 for safety)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_run.py` | New `TestRunWithCases` response schema |
| services | `app/services/test_run_service.py` | `get_with_cases(db, run_id)` |
| router | `app/api/v1/test_runs.py` | `GET /{run_id}/cases` |
| tests | `tests/integration/test_test_runs_api.py` | New endpoint tests |
| docs | `docs/06-generated/endpoints.md` | Add endpoint row |

### Response shape

```python
class TestCaseWithResult(BaseModel):
    # All TestCase fields
    id: int
    suite_id: int
    title: str
    steps: list[TestStep]
    priority: str
    type: str
    # Plus current result for this run (null = untested)
    result: TestResultResponse | None = None

    model_config = ConfigDict(from_attributes=True)

class TestRunWithCases(BaseModel):
    run: TestRunResponse
    cases: list[TestCaseWithResult]
```

### Query strategy

Recursively fetch all test cases in the run's suite tree, then outer-join with results:

```python
@staticmethod
async def get_with_cases(db: AsyncSession, run_id: int) -> dict:
    run = await db.get(TestRun, run_id)
    if not run:
        return None

    # Get all test cases in the suite tree (recursive CTE or multiple queries)
    cases_q = (
        select(TestCase, TestResult)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .outerjoin(
            TestResult,
            and_(TestResult.test_case_id == TestCase.id,
                 TestResult.test_run_id == run_id)
        )
        .where(TestSuite.project_id == run.project_id)  # scoped to project
    )
    # If run has a suite_id scope, filter to that suite's subtree
    if run.suite_id:
        cases_q = cases_q.where(TestCase.suite_id == run.suite_id)

    cases_q = cases_q.order_by(TestCase.id).limit(500)
    rows = await db.execute(cases_q)

    return {
        "run": run,
        "cases": [
            {"result": res, **{c: getattr(tc, c) for c in tc.__table__.columns.keys()}}
            for tc, res in rows.all()
        ]
    }
```

---

## Tasks

### Implementation
- [ ] Add `TestCaseWithResult` and `TestRunWithCases` schemas to `app/schemas/test_run.py`
- [ ] Implement `TestRunService.get_with_cases(db, run_id)` in `test_run_service.py`
- [ ] Add `GET /{run_id}/cases` route to `app/api/v1/test_runs.py`
- [ ] Write integration test: create run, add result for one case, verify response has `result != null` for that case and `null` for others

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `docs/06-generated/endpoints.md` — add endpoint row
- [ ] `docs/04-execution/tech-debt.md` — mark resolved
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `GET /test-runs/{id}/cases` returns `{ run, cases[] }` where each case has `result: null` or a full result object
- [ ] Untested cases always appear (outer join — not just cases with results)
- [ ] 401 without auth, 404 when run does not exist
- [ ] 500 cases cap prevents memory issues on large suites
- [ ] Integration tests pass
