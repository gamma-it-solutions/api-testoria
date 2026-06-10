# Execution Plan: Test Run Lifecycle + Completed-Only Statistics

**Date**: 2026-04-22
**Author**: gabi
**Status**: Draft

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Formalise the test run lifecycle as `planned → active → completed` with an auto-transition to `active` on the first result write, and change every dashboard/analytics endpoint so that only **completed** runs contribute to pass-rate and result statistics.

---

## Context

A project with one freshly created run (status `planned`) and two test cases, where only one case has been executed and passed, currently shows **100% overall pass rate** on the dashboard. Users reasonably read this as "the project is green" when in fact nothing has been finished — the number is just a single in-flight data point.

Root cause: the dashboard, report-analytics, project-stats, and bulk-project-stats endpoints aggregate `TestResult` rows joined to `TestRun` **with no filter on run status**, so in-progress runs leak into the metric.

A second, related UX gap: the run status enum today is `planned | in_progress | completed | aborted`, and the transition from `planned` to `in_progress` never happens — nothing in the service layer writes it. Frontend components show `planned` forever unless the user closes the run. The status column is effectively a 2-state flag.

This plan fixes both together because they share the same invariant: statistics care about **finished work**, and we need a reliable signal that a run is finished vs still in flight.

Related prior work:
- plan-027 — `_aggregate_run_status_counts()` helper (N+1 fix) — we reuse this, just filter its input
- plan-035 — pass-rate unified as `passed / (passed + failed + blocked + no_run)` ratio, helper in `app/utils/stats.py`
- plan-034 — empty test run + explicit case-set mode; case set locked on completed runs (409 on `PUT /test-runs/{id}/cases` when completed)
- plan-036 — progress always included on runs list (opt-in param later removed)
- plan-038 — result history recorded only on meaningful change
- Web-side companion: `web-testoria` plan-070 consumes the new semantics in the dashboard, reports, and execution views.

---

## Scope

### In scope
- Rename status value `in_progress` → `active` (Alembic data migration + update of `TestRunStatus` Literal and all references)
- Auto-transition `planned → active` inside `test_result_service` on the first result create **or** update that changes status/comment for a run currently in `planned`
- Filter `TestRun.status == 'completed'` in every analytics query:
  - `report_service.get_dashboard()` — pass-rate section (the `active_runs` section already filters correctly and stays as-is)
  - `report_service.get_report_analytics()` — overall pass-rate, trend, per-suite/per-case breakdowns
  - `project_service.get_stats()` (single-project stats)
  - `project_service.get_bulk_stats()` (multi-project bulk stats used by the dashboard tiles)
- Keep `POST /test-runs/{id}/close` as the only way to reach `completed` (no auto-complete)
- Emit a WebSocket event on status transition (`TestRunStatusChanged`) so the frontend updates live — reuse the Centrifugo publisher introduced in plan-008
- Update integration and unit tests; add new cases for `planned`, `active`, and `completed` run coverage

### Out of scope
- `aborted` lifecycle review — stays as-is, off the happy path, not counted in statistics (same rationale as active/planned: it's not completed work). Any decision about when/how runs are aborted is deferred.
- Auto-complete when every case has a final status (deliberately out — user wants explicit completion to preserve the "I confirm this is done" semantic)
- Converting `test_runs.status` to a PostgreSQL ENUM type — separate tech-debt item, deferred
- Historical re-computation of analytics beyond what the filter change implies (no data migration on `TestResult`)
- UI changes — covered in web-testoria plan-070

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| models | `app/models/test_run.py` | Update `status` column default comment and docstring; add `TestRunStatus` Literal/type alias re-export if convenient |
| schemas | `app/schemas/test_run.py` | Update `TestRunStatus` Literal to `"planned" \| "active" \| "completed" \| "aborted"`; keep compat Literal accepting `"in_progress"` as an input alias for one release cycle, normalising to `"active"` on read |
| migration | `alembic/versions/<new>_rename_in_progress_to_active.py` | `UPDATE test_runs SET status='active' WHERE status='in_progress'`; downgrade flips the update back |
| services | `app/services/test_run_service.py` | Add `transition_to_active(run_id)` helper (idempotent: only writes if current status is `planned`); export for use from result service. No changes to `close_run()`. |
| services | `app/services/test_result_service.py` | In `submit()` / `update()`, after a successful write that changes status or comment, call `test_run_service.transition_to_active(run_id)`. Skip the call when the write is a no-op (same status + comment). |
| services | `app/services/report_service.py` | Add `WHERE TestRun.status == 'completed'` to the pass-rate / result-status queries in `get_dashboard()`, `get_report_analytics()`, and any helper used by both (`_aggregate_run_status_counts` consumers). The `active_runs` counter inside `get_dashboard()` keeps its existing `status.in_(["planned", "active"])` logic (renamed). |
| services | `app/services/project_service.py` | Same filter in `get_stats()` and `get_bulk_stats()` |
| realtime | `app/realtime/publisher.py` (or equivalent from plan-008) | Publish `TestRunStatusChanged` on `planned → active` and `active → completed` transitions |
| tests | `tests/unit/test_test_run_service.py` | Test `transition_to_active`: no-op when already active/completed; flips when planned |
| tests | `tests/unit/test_test_result_service.py` | Test submit/update triggers auto-transition exactly once and skips on no-op writes |
| tests | `tests/unit/test_report_service.py` | Test dashboard + analytics exclude non-completed runs |
| tests | `tests/unit/test_project_service.py` | Test stats + bulk stats exclude non-completed runs |
| tests | `tests/integration/test_reports_api.py` | Scenario: 1 project, 1 `active` run with 1 passed + 1 `no_run` case → `pass_rate == null`, `active_runs == 1` |
| tests | `tests/integration/test_test_runs_api.py` | Scenario: POST run returns `planned`; POST first result flips to `active`; POST close flips to `completed`; `PUT cases` still 409s on completed |
| docs | `docs/06-generated/endpoints.md` | Note the run-status filter applied to analytics endpoints; document `active` as the canonical status name |
| docs | `docs/06-generated/db-schema.md` | Document `status` allowed values: `planned | active | completed | aborted` |
| docs | `docs/01-product/features/` | Touch or create `NNN-test-run-lifecycle.md`; update any feature doc that references `in_progress` |
| docs | `docs/04-execution/tech-debt.md` | Resolve "dashboard pass-rate includes in-progress runs"; add "remove `in_progress` Literal compat alias after web migration" as new debt |
| docs | `docs/08-decisions/changelog.md` | Record: completed-only analytics, auto-transition on first result, rename `in_progress` → `active` |

### Key decisions

- **Rename `in_progress` → `active`** rather than adding `active` as a new value. Carrying both forever is more confusing than a one-shot rename with a short compat window. The schema Literal accepts `"in_progress"` on input for one release cycle to avoid breaking in-flight clients, but the DB and responses use `"active"`.
- **Auto-transition lives in `test_result_service`, not in a trigger or a router.** Routers stay thin (invariant #1); triggers hide behaviour from the Python layer and make integration tests harder. The service writes status inside the same async transaction as the result write, so observers see them atomically.
- **Auto-transition is a single write, guarded by current status.** It only runs when `run.status == 'planned'`. This avoids re-writes on every subsequent result, keeps history clean, and is safe under concurrent result submissions (last-write-wins on the same value is a no-op).
- **Completed is the only status that counts toward statistics.** Alternatives considered: include `active` with a "provisional" flag (rejected — adds UI complexity and doesn't fix the "100%" misleading display); include runs where all cases have any final status regardless of run.status (rejected — conflicts with the explicit-completion semantic the user asked for).
- **No schema migration to PG ENUM.** Keeping `String(50)` keeps the compat shim simple; the enum migration is a separate tracked debt item.
- **`active_runs` KPI on dashboard is unchanged.** It still counts `planned + active` runs. That's the "work in flight" counter, separate from pass-rate.

---

## Tasks

### Implementation
- [x] Update `TestRunStatus` Literal in `app/schemas/test_run.py` to include `active`; add input compat alias for `in_progress`
- [x] Add `transition_to_active()` to `app/services/test_run_service.py` (idempotent, status-guarded)
- [x] Call `transition_to_active()` from `app/services/test_result_service.py` after each meaningful result write
- [x] Add `TestRun.status == 'completed'` filter to `report_service.get_dashboard()` pass-rate query
- [x] Add the same filter to `report_service.get_report_analytics()`
- [x] Add the same filter to `project_service.get_stats()`
- [x] Add the same filter to `project_service.get_bulk_stats()`
- [x] Create Alembic revision `rename_in_progress_to_active` (upgrade + downgrade data SQL)
- [x] Review and apply migration (`alembic upgrade head`)
- [x] Publish `TestRunStatusChanged` Centrifugo event on both transitions
- [x] Write unit tests for `transition_to_active` (idempotency, guarded writes)
- [x] Write unit tests for result-service trigger (no-op writes don't transition)
- [x] Write unit tests for `report_service` + `project_service` (excludes `planned`/`active`/`aborted`)
- [x] Write integration test: create run → status `planned`; submit first result → `active`; close → `completed`
- [x] Write integration test: project with only non-completed runs → `pass_rate` is `null` (or `0` of 0, per existing stats util contract — confirm and match)

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` updated (run status values + completed-only semantics)
- [x] `docs/06-generated/db-schema.md` updated (status allowed values)
- [x] `docs/01-product/features/` — add/update lifecycle feature doc; correct any `in_progress` mentions
- [x] `docs/02-architecture/ARCHITECTURE.md` — no codemap change expected; verify "Where is X?" for status transitions is still correct
- [x] `docs/02-architecture/backend/service-layer.md` — note cross-service call from result → run status
- [x] `docs/08-decisions/changelog.md` entry
- [x] `docs/04-execution/tech-debt.md` — resolve "dashboard includes in-progress runs"; add compat-alias removal debt
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Existing clients (web, CLI) hard-code `"in_progress"` and break on the rename | Medium | Keep input compat alias in the schema for one release; web-testoria plan-070 ships before the alias is removed |
| Auto-transition runs inside the result write transaction and causes extra lock contention on `test_runs` row | Low | Transition only fires when status is `planned` (once per run); guarded `UPDATE ... WHERE status='planned'` is a single row, single write |
| Dashboard tiles suddenly show `null` / `—` for projects that previously showed a value (because those projects have no completed runs) | Medium | This is the correct, intended behaviour — document it in the changelog and in the feature doc so support knows. Web side renders "No completed runs yet" instead of a blank cell |
| Race: two testers submit results simultaneously on a `planned` run → two transition attempts | Low | Both issue `UPDATE ... WHERE status='planned'`; the second is a no-op. No duplicate WS events if the second update sees `rowcount == 0` — publisher is gated on `rowcount > 0` |
| Analytics query performance regresses because we add another `WHERE` clause | Very low | `status` is a small cardinality column; existing indexes on `(project_id, deleted_at)` still drive the plan. Add a composite index on `(project_id, status)` if `EXPLAIN` shows a seq scan after deploy |

---

## Definition of done

- [x] `POST /projects/{id}/test-runs` returns `status: "planned"`
- [x] First `POST /test-runs/{id}/results` or `PUT /test-results/{id}` that changes status/comment flips run to `status: "active"` in the same transaction
- [x] `POST /test-runs/{id}/close` still the only path to `status: "completed"`
- [x] `GET /projects/{id}/dashboard` returns pass-rate computed only from completed runs; `active_runs` counter still includes `planned + active`
- [x] `GET /projects/{id}/report-analytics` summary and trend data computed only from completed runs
- [x] `GET /projects/stats` (bulk) returns `pass_rate == null` when a project has no completed runs
- [x] Centrifugo publishes `TestRunStatusChanged` on each transition
- [x] Alembic migration applies cleanly and is reversible; existing `in_progress` rows become `active`
- [x] All new tests pass; overall coverage for touched services ≥ 85%
- [x] Docs updated, including the changelog entry
