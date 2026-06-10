# Execution Plan: Rename `skipped` status → `no_run` and make it the default

**Date**: 2026-04-20
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Rename the `TestResult.status` literal `"skipped"` → `"no_run"` across Pydantic schemas, services, CI import, and reporting; migrate existing rows in `test_results`, `test_result_history`, and `step_results` JSON from `"skipped"` to `"no_run"`; treat `no_run` as the default status when a status is omitted on result creation.

---

## Context

The frontend (see `web-testoria plan-054-rename-skipped-to-no-run-default-status.md`) is renaming the `skipped` result status to `no_run` for clarity. "Skipped" was ambiguous against the derived `untested` bucket (case with no result row). Calling it `no_run` makes the intent explicit: "the tester chose not to run this case", and it becomes the **default** status when a result is submitted without an explicit pick.

Current backend surface carrying `"skipped"`:

- `app/schemas/test_result.py:9,15,25` — `Literal["passed","failed","blocked","skipped"]` on `StepResult`, `TestResultCreate`, `TestResultUpdate`
- `app/schemas/test_run.py:48` — `RunProgress.skipped: int`
- `app/schemas/report.py:15,56,68,121,139` — status-count fields on multiple report schemas
- `app/services/test_run_service.py:271` — progress counter
- `app/services/report_service.py` — many call sites: `_RUN_STATUS_KEYS` tuple, SQL `case()` branches, dict keys, response assembly (lines 34, 62, 178, 297, 370–371, 395, 451, 488, 510–511, 529, 559–560, 585, 702)
- `app/services/ci_service.py:42,54,59,68,69,90` — JUnit `<skipped>` element is mapped to `status="skipped"` on import

Data in motion:
- `test_results.status` (text column) — rows with value `"skipped"`
- `test_result_history.status` — same
- `test_results.step_results` JSON — objects with `{ "status": "skipped" }`

DB column is `String`, not an enum type, so no PG enum to alter — but rows need a data migration.

The api and web changes must land in the same release window. This plan includes a **short-lived compatibility window** where both values are accepted on input, normalised to `no_run`, so the frontend rollout doesn't hard-break if a stale client sends `"skipped"`.

---

## Scope

### In scope
- Add `"no_run"` to the Pydantic `Literal` on `StepResult`, `TestResultCreate`, `TestResultUpdate`
- Temporarily keep `"skipped"` in the Literal for **one release** (compat window), normalised to `"no_run"` in the service before persisting
- Rename all `skipped` → `no_run` fields in `RunProgress`, report schemas (`PassRate`, `RunSummary`, `ProjectSummary`, etc.)
- Update `report_service.py` SQL `case()` branches, dict keys, tuple `_RUN_STATUS_KEYS`, response assembly
- Update `test_run_service.py` progress counter
- Update `ci_service.py` — JUnit `<skipped>` element now maps to `status="no_run"`
- Alembic data migration: `UPDATE test_results SET status='no_run' WHERE status='skipped'`; same for `test_result_history`; JSON field update on `step_results`
- Default-on-omit: if `TestResultCreate` is submitted without `status` (not currently allowed — it's required), add `status: Literal["..."] = "no_run"` default. Confirm with product whether making it optional is desired; otherwise just document that the frontend sends `"no_run"` explicitly
- Unit tests updated; new test covering (a) `skipped` still accepted during compat window, (b) persisted as `no_run`, (c) default fill, (d) JUnit `<skipped>` maps to `no_run`
- Integration tests: report endpoints return `no_run` counts; history endpoint exposes `no_run`
- Docs: `endpoints.md`, `db-schema.md` if schema description enumerates statuses, feature doc `006-test-execution.md`, changelog

### Out of scope
- Introducing a PG enum type for status (tech debt — still a plain `String` column after this plan)
- Merging `untested` (derived) with `no_run` — they stay distinct
- Changing `RunProgress.untested` semantics
- Any CLI-side change (tracked in web plan 054 / separate CLI plan if needed)
- Long-term deprecation of the `"skipped"` compat: this plan opens the window; a follow-up plan removes it after the frontend rollout is confirmed

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_result.py` | `Literal["passed","failed","blocked","no_run","skipped"]` during compat window; `skipped` marked deprecated in a comment; `TestResultCreate.status` default = `"no_run"` (if product agrees to make it defaultable) |
| schemas | `app/schemas/test_run.py` | `RunProgress.skipped: int` → `no_run: int` |
| schemas | `app/schemas/report.py` | Every `skipped: int` field renamed to `no_run: int` (5 occurrences) |
| services | `app/services/test_result_service.py` (if exists) | Normalise incoming `"skipped"` → `"no_run"` before persist; same on update |
| services | `app/services/test_run_service.py` | `counts.get("skipped", 0)` → `counts.get("no_run", 0)` in progress return |
| services | `app/services/report_service.py` | `_RUN_STATUS_KEYS` tuple: replace `"skipped"` with `"no_run"`; SQL `case((TestResult.status == "no_run", TestResult.id))` branches; dict keys; assembly kwargs; text report line |
| services | `app/services/ci_service.py` | JUnit `<skipped>` → `status="no_run"`; rename local counter variable for readability |
| migration | `alembic/versions/YYYYMMDD_rename_skipped_status_to_no_run.py` | `op.execute("UPDATE test_results SET status='no_run' WHERE status='skipped'")`; same for `test_result_history`; JSON update for `step_results` (Postgres `jsonb_set` or a SQLAlchemy `text()` with `jsonb` array operators) |
| tests | `tests/unit/test_*_service.py` | Replace `"skipped"` fixtures with `"no_run"`; add compat-window test; add JUnit mapping test |
| tests | `tests/integration/test_*.py` | Same; verify report endpoints carry `no_run` key |

### Key decisions

- **Wire value `no_run` (snake_case)**: matches repo convention (`test_run_id`, `step_results`), Pythonic, identical to what the frontend sends.
- **DB column stays `String`**: column is already `String` (not a PG enum), so no enum migration needed — only a data-value rewrite. Logged as tech debt to convert to a proper PG enum later.
- **Compat window, not hard break**: during the overlap between api deploy and web deploy, old clients (or replayed requests) may still send `"skipped"`. Keep it in the Literal for one release; **normalise at the service boundary** so every stored row is `no_run` regardless of what was sent. Follow-up plan removes the compat after the frontend is fully rolled out and we've confirmed no `"skipped"` has been persisted for N days.
- **JUnit `<skipped>` → `no_run`**: semantically a test that wasn't run. Label is different (XML tag is still `<skipped>`, a JUnit convention we don't control); the mapping to our internal status is what changes.
- **`step_results` JSON migration**: the column is `JSON`/`JSONB`. Migrate each `step_results[i].status == "skipped"` to `no_run`. Use Postgres JSONB set / a Python-side loop in the migration (read → transform → write) to avoid brittle JSON-path SQL. Prefer the Python-side loop for correctness; it runs once per row in the migration, well below the DB size where performance is a concern.
- **Response shape rename is breaking for report consumers**: `RunProgress.skipped` → `no_run` is a JSON-shape change. The web frontend is updating in lockstep (plan-054). No other documented consumers. Record as breaking change in the changelog. If CLI consumes reports it must update too.
- **Reversible migration**: Alembic down-grade runs the inverse `UPDATE` and reverses the JSON transformation. Test both directions.
- **No new index, no new column, no new constraint**: text column, plain UPDATE. Migration expected to be fast — but wrap in a single transaction and run off-hours given `test_results` is the largest table.
- **Auth / role**: no change; existing `require_role` on endpoints unchanged.

---

## Tasks

### Implementation
- [x] Update `app/schemas/test_result.py`: `Literal` includes both `"no_run"` and `"skipped"` during compat window; add comment explaining; if product approves, set `TestResultCreate.status` default = `"no_run"`
- [x] Update `app/schemas/test_run.py`: `RunProgress.skipped` → `no_run`
- [x] Update `app/schemas/report.py`: rename every `skipped` field to `no_run` (5 schemas)
- [x] Add a `_normalise_status` helper (or inline) in the service layer that maps `"skipped"` → `"no_run"` before persist (create + update + step_results + JUnit import path)
- [x] Update `app/services/test_run_service.py` (`counts.get`)
- [x] Update `app/services/report_service.py`: tuple, SQL `case()` branches, dict keys, response kwargs, text-report template string
- [x] Update `app/services/ci_service.py`: JUnit `<skipped>` handler returns `"no_run"`; rename local counter
- [x] Create Alembic revision `rename_skipped_status_to_no_run`:
  - `UPDATE test_results SET status='no_run' WHERE status='skipped'`
  - `UPDATE test_result_history SET status='no_run' WHERE status='skipped'`
  - Python-side loop: for each `test_results` row with non-null `step_results`, load JSON, map `status="skipped"` → `"no_run"`, write back
  - Downgrade: reverse all three
- [x] Run `alembic upgrade head` against a copy of prod-like data; measure time; verify counts (`SELECT status, count(*) FROM test_results GROUP BY status` before/after)
- [x] Update unit tests:
  - Service persist: creating with `"skipped"` still works; row lands as `"no_run"`
  - Service persist: creating with `"no_run"` lands as `"no_run"`
  - JUnit import: `<skipped>` element yields `status="no_run"`
  - Default: creating with omitted status lands as `"no_run"` (if the defaultable change was accepted)
  - Report service: counts returned under `no_run` key
- [x] Update integration tests:
  - `POST /test-results` with `"skipped"` → 2xx, GET returns `"no_run"`
  - `POST /test-results` with `"no_run"` → 2xx, GET returns `"no_run"`
  - `GET /reports/*` surfaces `no_run` field (not `skipped`)
- [x] Verify no service code still branches on the string `"skipped"` outside the normalisation helper

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors (check that the `Literal` narrowing still type-checks call sites)
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — update status enum values in request/response examples; note `no_run` as default
- [x] `docs/06-generated/db-schema.md` — if the `status` column's value list is documented, swap `skipped` → `no_run`; note `step_results.status` same
- [x] `docs/01-product/features/006-test-execution.md` (or equivalent) — describe the renamed status and new default
- [x] `docs/02-architecture/ARCHITECTURE.md` — if the codemap enumerates status values, update
- [x] `docs/08-decisions/changelog.md` — record: renamed for clarity vs `untested`; compat window chosen over a hard break; JUnit `<skipped>` mapping; breaking JSON-shape change for report consumers
- [x] `docs/04-execution/tech-debt.md` — add two items: (1) remove `"skipped"` from Literal after rollout confirmed, (2) convert `test_results.status` to a PG enum type
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Migration is slow on a large `test_results` table (JSON step_results loop) | Medium | Run off-hours; batch by id range; measure on prod-like data; if slow, switch to a pure-SQL JSONB update |
| A report consumer (CLI, external dashboard) breaks on the `skipped` → `no_run` key rename | Medium | Changelog entry flagged "breaking"; coordinate with web plan 054 merge window; add a one-release API-version-header fallback if a third-party consumer is discovered |
| Compat window removed too early; stale clients start failing with 422 | Medium | Follow-up removal plan waits for dashboards/logs to show zero `"skipped"` traffic for ≥ 2 weeks |
| Downgrade runs against rows already rolled back; data becomes inconsistent | Low | Downgrade is idempotent (flips based on status value); dry-run on staging |
| Step-results JSON migration misses nested structures / null JSON | Low | Migration checks `step_results IS NOT NULL` and iterates safely; unit-tested on sample payloads |
| CI importer test fixtures still say `"skipped"` and silently break | Low | Explicit test case covers JUnit `<skipped>` → `"no_run"` |
| Literal narrowing in mypy breaks call sites that pattern-match on status string | Low | mypy run in quality check catches this; adjust the helper signature to return the normalised `Literal["passed","failed","blocked","no_run"]` |

---

## Definition of done

- [x] Pydantic schemas accept `"no_run"` and `"skipped"` (compat); service persists `"no_run"` either way
- [x] `grep -r '"skipped"' app/` returns only the normalisation helper and the compat Literal line — no other status logic references the old value
- [x] `RunProgress`, `PassRate`, `RunSummary`, `ProjectSummary`, and history responses all use the `no_run` key
- [x] JUnit `<skipped>` element maps to `status="no_run"` on import
- [x] Alembic migration runs cleanly up and down; row counts match expectations; `step_results` JSON entries updated
- [x] Unit test coverage ≥ 85% on changed service code; integration tests pass
- [x] Web plan 054 merged in the same release window
- [x] Changelog flags the breaking JSON-shape change
- [x] Tech debt logged for (a) compat removal, (b) enum type conversion
- [x] Docs updated
- [x] PR checklist completed
