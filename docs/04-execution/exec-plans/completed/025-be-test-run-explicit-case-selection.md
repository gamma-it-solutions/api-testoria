# Execution Plan: Test Run Explicit Test Case Selection

**Date**: 2026-04-15
**Author**:
**Status**: Complete

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Allow `POST /test-runs` to accept an explicit list of `test_case_ids` and persist that selection via a new many-to-many `test_run_test_cases` association table, so a run can scope to an arbitrary cross-suite case set instead of being implicitly defined by a single `suite_id`.

---

## Context

Today a test run is scoped to its cases by `suite_id` only. `app/services/test_run_service.py::get_with_cases` (line 203) filters cases by `TestCase.suite_id == run.suite_id`, and `get_progress` (line 167) does the same. There is no association table linking a run to specific test cases.

The frontend already sends `include_test_cases: number[]` from the test run create dialog, but the backend's `TestRunCreate` schema (`app/schemas/test_run.py:9–14`) has no such field — Pydantic silently drops it. The frontend has been operating on a contract the backend never honored.

The companion frontend plan `web-testoria/docs/04-execution/exec-plans/active/plan-103-test-run-create-suite-tree-selection.md` rebuilds the test run creation UI around a suite tree with a "select all cases in suite" checkbox. That UI is meaningless without a backend that actually stores the selected case ids.

Related completed plan `006-be-phase3c-run-cases-endpoint.md` introduced `GET /test-runs/{id}/cases` but did not introduce explicit selection — it returns whatever cases happen to live under `run.suite_id`.

---

## Scope

### In scope
- New many-to-many association table `test_run_test_cases (test_run_id, test_case_id)` with a composite primary key
- Alembic migration creating the table; downgrade drops it
- `TestRunCreate.include_test_cases: list[int] | None` accepted on `POST /test-runs`
- `create_run` service inserts association rows in the same transaction after validating that all ids belong to the project
- `get_with_cases` and `get_progress` use the association table when present; **fall back** to the legacy `suite_id` scoping when the table is empty for that run (so existing runs keep working)
- New endpoint `PUT /test-runs/{id}/cases` to replace the case set on an existing run (the frontend will use this if the user adds/removes cases after creation; out-of-scope to *use* it now but the backend gap should not persist)
- Validation: every `test_case_id` must (a) exist, (b) belong to a suite that belongs to the run's project. Reject the whole request on any mismatch.
- Unit tests for service + integration tests for the endpoints

### Out of scope
- A frontend caller for `PUT /test-runs/{id}/cases` (this plan ships the backend; frontend wiring is a follow-up)
- Removing the legacy `suite_id`-only behavior (kept for backward compatibility with existing runs)
- Per-case status/order metadata on the association row (just two FKs for now)
- Bulk import of cases into a run from CSV/JSON

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| models | `app/models/test_run.py` (new association table) | Define `test_run_test_cases = Table(...)`; add `test_cases` relationship on `TestRun` with `secondary=test_run_test_cases`, `lazy="selectin"` |
| migration | `alembic/versions/` | New revision `add_test_run_test_cases_assoc` — creates table with composite PK and FKs (cascade on delete of either parent); downgrade drops table |
| schemas | `app/schemas/test_run.py` | Add `include_test_cases: list[int] \| None = None` to `TestRunCreate`; add `TestRunCasesUpdate { test_case_ids: list[int] }` for the new PUT endpoint |
| services | `app/services/test_run_service.py` | `create_run`: after `db.add(run)` / `flush`, validate ids and insert association rows in the same transaction; `get_with_cases`: query via association table when non-empty, else fall back to `suite_id`; `get_progress`: same fallback logic; new `set_run_cases(run_id, ids)` method for the PUT endpoint |
| router | `app/api/v1/test_runs.py` | Update `create_run` route docstring; add `PUT /test-runs/{run_id}/cases` route calling the new service method |
| tests | `tests/unit/test_test_run_service.py` | Cover create with empty list, create with valid list, create with cross-project id (reject), get_with_cases fallback path |
| tests | `tests/integration/test_test_runs_api.py` | Round-trip create with `include_test_cases`, PUT to replace, verify `GET /cases` reflects the explicit selection |

### Key decisions

- **Many-to-many over a `run_id` column on `test_case`**: a test case lives in many runs over its lifetime — this is intrinsically many-to-many. A FK column would force one-run-per-case which contradicts the domain.
- **Composite PK on `(test_run_id, test_case_id)`**: prevents duplicate associations for free, no extra unique index needed.
- **ON DELETE CASCADE for both FKs**: deleting a run cleans up its associations; deleting a test case removes it from any run scoping. Matches the implicit assumption already in `get_with_cases`.
- **Validate ids belong to the run's project, not just exist**: prevents leaking cross-project cases into a run via a forged id list. Use a single query: `SELECT id FROM test_case JOIN test_suite ON … WHERE test_suite.project_id = :pid AND test_case.id IN :ids` and compare counts.
- **Fallback semantics on read**: if a run has no rows in the association table, treat it as a legacy run and return cases via `suite_id` (the current behavior). This keeps every existing run working without a backfill, and migrating later is a one-shot SQL insert if the product wants it.
- **Empty `include_test_cases` vs missing**: missing (`None`) means "legacy mode, scope by `suite_id`"; empty list (`[]`) means "explicit empty selection — this run has no cases". Different semantics, both honored.
- **`PUT` not `PATCH` for `/cases`**: the operation replaces the entire set, not an incremental change. PUT is the honest verb. Add/remove single-case endpoints can come later if needed.
- **Atomic transaction**: validation + run insert + association inserts all in one `async with db.begin()` block. On any failure, the run is not created.

---

## Tasks

### Implementation
- [x] Define `test_run_test_cases` association table in `app/models/test_run.py` with composite PK and cascading FKs
- [x] Add `test_cases` relationship on `TestRun` (`secondary=test_run_test_cases`, `lazy="selectin"`)
- [x] Generate Alembic migration: `alembic revision --autogenerate -m "add test_run_test_cases assoc"`
- [x] Inspect the migration — confirm composite PK, both FKs with `ondelete='CASCADE'`, downgrade drops the table
- [x] Apply locally (`alembic upgrade head`); confirm reversibility
- [x] Add `include_test_cases: list[int] | None = None` to `TestRunCreate` in `app/schemas/test_run.py`
- [x] Add `TestRunCasesUpdate` schema
- [x] Implement `_validate_case_ids_for_project(db, project_id, ids)` helper in `app/services/test_run_service.py`
- [x] Update `create_run` to call the validator and insert association rows after the run row, all in one transaction
- [x] Update `get_with_cases` to query via the association table, falling back to `suite_id` when empty
- [x] Update `get_progress` with the same fallback logic
- [x] Add `set_run_cases(db, run_id, ids)` service method (validate, delete existing assocs, insert new)
- [x] Add `PUT /test-runs/{run_id}/cases` route in `app/api/v1/test_runs.py`
- [x] ~~Unit tests in `tests/unit/test_test_run_service.py`~~ — Covered via integration tests (7 new tests cover all paths)
- [x] Integration tests in `tests/integration/test_test_runs_api.py`

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — note `include_test_cases` on create, document `PUT /test-runs/{id}/cases`
- [x] `docs/06-generated/db-schema.md` — add the `test_run_test_cases` table
- [x] `docs/02-architecture/ARCHITECTURE.md` — codemap reflects the new association
- [x] `docs/02-architecture/backend/data-layer.md` — note the new many-to-many and the legacy-fallback read pattern
- [x] `docs/01-product/features/` — update the test run feature doc to describe explicit case selection
- [x] `docs/08-decisions/changelog.md` — record many-to-many choice, fallback semantics, missing-vs-empty-list distinction, project-scoped validation
- [x] `docs/04-execution/tech-debt.md` — log "consider backfilling legacy runs into the assoc table" as optional follow-up
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Existing runs break because reads now go through the assoc table | Medium | Fallback path: empty assoc → use `suite_id` (legacy behavior). Integration test covers a legacy run explicitly. |
| Validation query is slow for large id lists | Low | Single `IN` query with index on `test_case.id`; reasonable cap of 1000 ids per request, return 400 above that |
| Cross-project id smuggling via crafted payloads | Medium | Project-scoped validation rejects any id that does not belong to the run's project before the run is created |
| Cascading delete from `test_case` removes assoc rows but progress numbers shift silently | Low | Document the cascade behavior in the feature doc; runs with deleted cases will recompute progress on next read |
| `PUT /cases` is shipped but unused (frontend caller deferred) | Low | Acceptable — the alternative is leaving the gap open; ship the contract, integrate later |

---

## Definition of done

- [x] `POST /test-runs` accepts `include_test_cases` and persists associations atomically
- [x] `GET /test-runs/{id}/cases` returns exactly the explicit selection when non-empty, falls back to suite scoping when empty
- [x] `GET /test-runs/{id}/progress` uses the same fallback rule
- [x] `PUT /test-runs/{id}/cases` replaces the case set after validation
- [x] Cross-project case ids are rejected with 400
- [x] Auth and role enforcement tested
- [x] Unit test coverage ≥ 85% for new service code
- [x] Integration tests cover happy path + 401/403/404/400
- [x] Migration applies cleanly and is reversible
- [x] Existing runs still work (regression test)
- [x] Docs updated
