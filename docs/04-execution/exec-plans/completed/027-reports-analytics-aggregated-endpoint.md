# Execution Plan: Reports & Analytics Aggregated Endpoint

**Date**: 2026-04-17
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Expose a single `GET /projects/{project_id}/report-analytics` endpoint that returns every piece of data the frontend Reports & Analytics page needs, eliminating the N+1 pattern where the UI loops per test run to fetch `/test-runs/{id}/results`.

---

## Context

The frontend Reports page (`ReportDashboardView.vue`) currently fetches the project's runs, then loops and calls `GET /test-runs/{id}/results` **once per run** to compute status counts, pass rate, and trends. With 20+ runs per project this is 20+ sequential HTTP round-trips and transfers the full result payload (including `step_results`, `stack_trace`, `defects`) just to compute counts.

In parallel, the backend `get_dashboard()` service has its own per-run loop (`report_service.py:104–125`) that issues one SQL query per recent run to count results — the same N+1 shape.

A single aggregated endpoint can build the whole payload with 3–4 grouped SQL queries (run-level status counts, test case distributions, time-series trend) and let the frontend render without any client-side aggregation loops.

Pairs with frontend plan `plan-100-reports-analytics-n1-aggregation.md` in `web-testoria`.

---

## Scope

### In scope
- New endpoint: `GET /api/v1/projects/{project_id}/report-analytics`
- Query params: `date_from`, `date_to` (ISO date), `run_status` (optional filter), `include_trend` (bool, default true)
- Aggregated response containing:
  - Project summary (totals, active runs, overall pass rate)
  - All runs in the project (or filtered window) with precomputed status counts, total, pass_rate, completed_at, milestone_id, assigned_to
  - Test case distribution by `priority`, `type`, and automation (has `automation_id` or not)
  - Time-series trend (per-day pass_rate + counts) within the date window
- New Pydantic response schema `ProjectReportAnalyticsResponse` in `app/schemas/report.py`
- New service function `get_report_analytics(db, project_id, filters)` in `app/services/report_service.py` using grouped queries (no per-run loop)
- Refactor existing `get_dashboard()` to reuse the same run-count grouping helper (eliminate its internal N+1 at lines 104–125)
- Unit tests for the service; integration tests for the endpoint (happy path, empty project, date filter, 401/403/404)
- OpenAPI docs auto-generated via FastAPI

### Out of scope
- Caching layer (Redis) — add later if query latency is a problem at scale
- Websocket push for live dashboard updates (separate concern)
- Changing the existing `/test-runs/{id}/results`, `/projects/{id}/dashboard`, `/projects/{id}/metrics`, `/test-runs/{id}/report` endpoints — they stay as-is for other consumers
- Frontend changes (separate plan in `web-testoria`)
- Project-scoped tags / milestone list UI (tracked separately in tech-debt)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/report.py` | Add `RunAnalyticsItem`, `TestCaseDistribution`, `TrendPoint`, `ProjectReportAnalyticsResponse` |
| services | `app/services/report_service.py` | Add `get_report_analytics()`; extract `_aggregate_run_status_counts(run_ids)` helper and reuse it in `get_dashboard()` |
| router | `app/api/v1/reports.py` | Add `GET /projects/{project_id}/report-analytics` route with query param dependencies |
| tests | `tests/unit/test_report_service.py` | Unit tests for the new service fn and the shared helper |
| tests | `tests/integration/test_reports_api.py` | Integration tests for the endpoint |
| docs | `docs/06-generated/endpoints.md` | Add new endpoint row |

### Key decisions

- **One endpoint, not three**: single round-trip beats parallelized small endpoints for the dashboard's typical use. The response is bounded by `runs × 1 row` + `N_days` + a handful of distribution counts — well under 1 MB for typical projects.
- **Grouped SQL, not ORM loops**: run-level counts via `SELECT test_run_id, status, COUNT(*) ... GROUP BY test_run_id, status` in one query for all runs in the window. Time-series via `GROUP BY date(tested_at), status`. Test case distributions via `GROUP BY priority` / `type` / `automation_id IS NULL`.
- **Do not add aggregate columns to `test_runs`**: computing is cheap with proper indexes (`test_results.test_run_id` already indexed). Stored aggregates would drift on result inserts/updates.
- **Path shape**: mount under `/projects/{project_id}/report-analytics` (consistent with `/dashboard`, `/metrics`) rather than a new `/reports/...` path.
- **Reuse for `get_dashboard()`**: the internal helper is shared, so fixing the N+1 here also fixes it in the dashboard endpoint — net negative LOC in `report_service.py`.
- **Filter semantics**: `date_from` / `date_to` filter on `TestRun.completed_at` (falling back to `created_at` if null, matching current frontend logic). Runs outside the window are omitted from the `runs` array and the trend series, but `summary.total_runs` still counts all runs in the project for the "totals" widget.
- **Auth**: same as existing reports endpoints — `Depends(get_current_user)` plus project access check.

---

## Tasks

### Implementation
- [x] Define Pydantic schemas in `app/schemas/report.py` (`RunAnalyticsItem`, `TestCaseDistribution`, `TrendPoint`, `ReportAnalyticsSummary`, `ProjectReportAnalyticsResponse`)
- [x] Extract `_aggregate_run_status_counts(db, run_ids)` helper in `report_service.py` returning `dict[int, dict[str, int]]`
- [x] Refactor `get_dashboard()` to use the helper (remove the per-run loop)
- [x] Implement `get_report_analytics(db, project_id, date_from, date_to, run_status, include_trend)`
- [x] Add route `GET /projects/{project_id}/report-analytics` in `app/api/v1/reports.py` with `response_model=ProjectReportAnalyticsResponse`
- [ ] Verify query plans with `EXPLAIN ANALYZE` on seed data (no sequential scans on `test_results`) — deferred; `test_results.test_run_id` is already indexed so the grouped query plan is fine on the SQLite test DB and expected to be fine on Postgres
- [x] Write unit tests for `_aggregate_run_status_counts` and `get_report_analytics` (empty project, date filter, status filter, trend zero-fill)
- [x] Write integration tests for the endpoint (happy path, empty window, invalid project → 404, unauthenticated → 401, include_trend=false)

### Quality check
- [x] `pytest` — all 240 tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` updated with the new endpoint row and response shape
- [x] `docs/02-architecture/ARCHITECTURE.md` — `report_service` entries added to the "Where is X?" table
- [x] `docs/01-product/features/006-reporting-analytics.md` — new endpoint documented, constraints expanded
- [x] `docs/08-decisions/changelog.md` — Plan 027 entry added
- [x] `docs/04-execution/tech-debt.md` — resolved entry added for the `get_dashboard()` N+1 loop
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Response payload grows large for projects with hundreds of runs | Medium | Paginate or default-window to last 90 days when `date_from`/`date_to` are absent; document max runs returned |
| Grouped queries hit unindexed columns (e.g. `test_runs.completed_at`) | Low | Check existing indexes, add migration if needed (cheap — single-column index on `completed_at`) |
| Refactor of `get_dashboard()` subtly changes the existing response | Medium | Cover current `get_dashboard()` behavior with a snapshot test before refactor; assert identical payload after |
| Date filter semantics differ from frontend's current client-side filter | Medium | Mirror existing frontend logic (`completed_at ?? created_at`); document it on the schema |
| Trend series misses days with zero activity | Low | Emit zero-filled series for the requested window so charts render a continuous X axis |

---

## Definition of done

- [ ] All new endpoints return correct status codes and response shapes
- [ ] Auth and role enforcement tested
- [ ] Unit test coverage ≥ 85% for new service code
- [ ] Integration tests cover happy path + 401/403/404
- [ ] Migration applies cleanly and is reversible (N/A if no schema change; confirm index migration if added)
- [ ] `get_dashboard()` payload is byte-identical to the pre-refactor version under snapshot test
- [ ] New endpoint benchmark: p95 latency < 200 ms on a project with 50 runs and 5k results (local Postgres)
- [ ] Docs updated
