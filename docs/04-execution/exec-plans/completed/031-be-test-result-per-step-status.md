# Execution Plan: Per-Step Status on Test Results

**Date**: 2026-04-15
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Add a `step_results` JSON field to `TestResult` that records a per-step outcome (`passed | failed | blocked | skipped`, plus optional comment) for each step of the test case, so testers can mark individual steps during execution and reviewers can see exactly where a case went wrong.

---

## Context

A test case already has an ordered list of steps stored as JSON (`app/models/test_case.py` → `steps` column; shape `[{step, expected}, ...]`). Today a `TestResult` carries only one overall `status` string (`app/schemas/test_result.py:9`, `app/models/test_result.py:53`), so when a case with ten steps fails, the tester can only record *that* it failed — not *which* step failed.

The frontend execution view (`web-testoria/src/views/test-runs/TestRunExecutionView.vue:745–766`) renders the step list read-only from the test case. The companion frontend plan `plan-105-execution-per-step-status.md` adds a clickable status picker next to each step and submits per-step outcomes as part of the result payload. That plan cannot land without a backend that persists and returns the per-step data.

---

## Scope

### In scope
- New nullable `step_results: list[StepResult] | None` field on `TestResult` with shape `[{index: int, status: 'passed'|'failed'|'blocked'|'skipped', comment: str | None}]`, stored as a JSON column
- Accepted on `POST /test-runs/{id}/results` (create) and `PATCH /test-results/{id}` (update)
- Returned on every `TestResultResponse`
- Pydantic validation: each `index` is a non-negative int, each `status` is one of the four literals, `comment` is optional and length-capped
- Service-side validation: every `index` in `step_results` must refer to an existing step on the associated test case (i.e. `0 <= index < len(test_case.steps)`); reject the whole request on any out-of-range index
- Alembic migration adding the column (JSON, nullable, default `null`)
- Unit + integration tests for create / update / validation rejection / read round-trip

### Out of scope
- Auto-computing the overall `TestResult.status` from `step_results` — the overall status stays manually set by the tester for now (decision below)
- A separate `result_step` relational table — the JSON approach matches the existing `test_case.steps` pattern
- Step-level attachments (attachments remain result-level)
- History / audit of per-step status changes (the existing `ResultHistory` still tracks only overall status)
- Reordering steps on a case mid-execution (would invalidate indices; out of scope)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_result.py` | New `StepResult` BaseModel (`index`, `status`, `comment`); add `step_results: list[StepResult] \| None = None` to `TestResultCreate`, `TestResultUpdate`, `TestResultResponse` |
| models | `app/models/test_result.py` | Add `step_results: Mapped[list[dict[str, Any]] \| None] = mapped_column(JSON, nullable=True, default=None)` |
| migration | `alembic/versions/` | New revision `add_step_results_to_test_result`; upgrade adds column, downgrade drops it |
| services | `app/services/test_result_service.py` | On create/update, if `step_results` is supplied, load the associated `TestCase`, assert every `index` is in range `[0, len(case.steps))` and reject with `ValueError` / 422 otherwise; persist the list as-is |
| router | `app/api/v1/test_results.py` (or wherever results are created) | No structural change — the new field flows through the Pydantic schemas |
| tests | `tests/unit/test_test_result_service.py` | Valid `step_results`, empty list, out-of-range index, missing index for a subset of steps (allowed — partial coverage), status literal rejection |
| tests | `tests/integration/test_test_results_api.py` | Full round-trip on create + update, plus a 422 case |

### Key decisions

- **JSON column, not a relational table**: `test_case.steps` is already stored as JSON, steps are intrinsically ordered, each result has ≤ ~20 steps in practice, and no per-step joins are needed for filtering or aggregation. A relational table would add two FKs and migration cost for no query-time benefit.
- **`index`-based identification**, not `step_id`: the test case `steps` JSON has no stable ids — steps are a list, identified positionally. Matching the existing shape avoids a parallel id scheme.
- **Partial coverage allowed**: a tester might mark only the failing step and leave the rest blank. The schema accepts a `step_results` shorter than the case's step list. Missing indices mean "not reported" (distinct from `skipped`, which is an explicit tester action).
- **Overall `status` stays manually set**: we do *not* auto-derive `TestResult.status` from `step_results`. Reasons: (a) the tester is the authority on whether a partial failure makes the case fail overall — some failures are minor, some aren't; (b) auto-derivation creates surprising writes; (c) we can add a "suggest overall status" button on the frontend later without a schema change.
- **Out-of-range indices rejected, not silently dropped**: a client submitting `index: 99` on a 5-step case is a bug, not a no-op. Surface it via 422.
- **Nullable default, not `[]`**: `None` means "tester didn't use per-step mode", `[]` means "tester opened the per-step UI but marked nothing". Different semantics. Default is `None` so existing rows and older clients keep working unchanged.
- **No migration backfill**: existing results stay `null`. Per-step history is not reconstructed from nothing.
- **Comment length cap**: 1000 chars per step comment. Longer than the typical "Expected X, got Y" note, short enough to keep the JSON payload sane.

---

## Tasks

### Implementation
- [x] Define `StepResult` schema (with `Literal` status and `max_length` on comment) in `app/schemas/test_result.py`
- [x] Add `step_results: list[StepResult] | None = None` to `TestResultCreate`, `TestResultUpdate`, `TestResultResponse`
- [x] Add the `step_results` JSON column to `TestResult` in `app/models/test_result.py`
- [x] Generate Alembic migration: `alembic revision --autogenerate -m "add step_results to test_result"`
- [x] Inspect the migration — confirm nullable JSON column + clean downgrade
- [x] Apply locally (`alembic upgrade head`); confirm reversibility with `downgrade -1` then `upgrade head`
- [x] Implement `_validate_step_results(case, step_results)` in `app/services/test_result_service.py` — called from create and update paths
- [x] Wire it into `create_result` / `update_result`; persist the list as-is on the model
- [x] Unit tests for the validator (valid / out-of-range / empty / wrong literal)
- [x] Integration tests for `POST` + `PATCH` + `GET` round trip + 422 case

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — note the new field on result create/update/response
- [x] `docs/06-generated/db-schema.md` — add the `step_results` column to the `test_result` table row
- [x] `docs/01-product/features/` — update the test execution feature doc to describe per-step status
- [x] `docs/08-decisions/changelog.md` — record JSON-not-table, index-not-id, partial-coverage-allowed, overall-status-stays-manual, null-vs-empty, out-of-range-rejected
- [x] `docs/04-execution/tech-debt.md` — log "per-step status history" as a follow-up if product wants it
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Test case steps are reordered after a result is recorded, indices become meaningless | Medium | Document the limitation in the feature doc; long-term mitigation (stable step ids) is tracked as tech debt, not this plan |
| Clients submit duplicate indices in `step_results` | Low | Validator rejects duplicates; covered by a unit test |
| JSON column type varies across PostgreSQL versions (`JSON` vs `JSONB`) | Low | Match whatever `test_case.steps` already uses — stay consistent across the codebase |
| Comment field becomes a dumping ground for long logs | Low | 1000-char cap enforced in the Pydantic `StepResult` schema |
| Autogenerate misses the JSON type nuance | Medium | Manually inspect the generated revision before applying |

---

## Definition of done

- [x] `POST /test-runs/{id}/results` accepts `step_results` and persists them
- [x] `PATCH /test-results/{id}` accepts `step_results` and replaces the stored list
- [x] `GET /test-results/{id}` and list endpoints return the field (or `null`)
- [x] Out-of-range indices return 422 with a clear error message
- [x] Auth and role enforcement unchanged and tested
- [x] Unit test coverage ≥ 85% for the new validator path
- [x] Integration tests cover happy path + 422 + 401
- [x] Migration applies cleanly and is reversible
- [x] Docs updated
