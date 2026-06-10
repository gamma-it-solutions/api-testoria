# Execution Plan: Cross-Project Report Analytics Endpoint

**Date**: 2026-05-08
**Author**: gabi
**Status**: Complete

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Expose a new `GET /api/v1/reports/analytics` endpoint that returns the same Reports page payload (summary, runs, distributions, trend) but aggregated across **all projects** (or a caller-supplied subset), so the frontend Reports view can render cross-project metrics when the user picks "All projects" instead of one.

---

## Context

`GET /projects/{project_id}/report-analytics` (plan 027) is hard-scoped to one project: the path carries `project_id` and `report_service.get_report_analytics()` filters every query by `TestRun.project_id == project_id`. The frontend Reports view (`/reports`) therefore only renders when a single project is selected — there is no "All projects" mode for the Reports page even though the home Dashboard (`/`) already supports it via `GET /projects/stats` (plan 028 / 040).

Users want to see global pass rate, distribution, trend, and run list across all projects they can see, plus a per-project breakdown row so they can compare. This is the cross-project mirror of plan 027.

Pairs with frontend plan `plan-082-reports-all-projects-aggregated-view.md` in `web-testoria`.

---

## Scope

### In scope

- New endpoint: `GET /api/v1/reports/analytics`
- Query params:
  - `project_ids?` (repeated; defaults to "all visible projects")
  - `date_from?`, `date_to?` (ISO date)
  - `run_status?` (optional filter on the runs list)
  - `include_trend?` (bool, default `true`)
  - `include_archived?` (bool, default `false` — mirrors `/projects/stats`)
- New Pydantic schemas in `app/schemas/report.py`:
  - `PerProjectAnalyticsRow` — one summary line per project in scope (project id, name, completed run count, overall pass rate, total results)
  - `CrossProjectReportAnalyticsResponse` — `{ project_ids, date_from, date_to, summary, runs, test_case_distribution, trend, per_project }`
- Additive change to existing `RunAnalyticsItem`: add `project_id: int` (always populated) and `project_name: str | None = None` (populated only by the cross-project endpoint to avoid an N+1 join in the per-project case).
- New service function `report_service.get_cross_project_report_analytics(db, project_ids, date_from, date_to, run_status, include_trend, include_archived)` that mirrors `get_report_analytics` but without the per-project filter, reusing `_aggregate_run_status_counts` and `test_run_service.batch_run_progress`.
- Aggregation rules — keep the same conventions established by plans 035/039/041:
  - `summary.overall_pass_rate` = arithmetic mean of each completed run's own `pass_rate` across **every** run in scope (not per-project then averaged) — empty completed runs (`pass_rate = null`) don't contribute.
  - `summary.result_distribution` / `total_results` count results from `TestRun.status == 'completed'` only.
  - `summary.active_runs` counts `planned + active`.
  - `summary.total_test_cases` / `total_test_suites` / `total_test_runs` are sums across the projects in scope (live rows only — `not_deleted()` everywhere, same as the per-project endpoint).
  - `runs[]` lists every run in scope (subject to `date_from`/`date_to`/`run_status` filters), each carrying its `project_id` so the UI can group/colour by project.
  - `test_case_distribution` aggregates across all in-scope projects.
  - `trend` is a single zero-filled daily series across all in-scope completed runs.
  - `per_project[]` — one row per project in scope (sorted by `project_id` for deterministic order). Each row's `overall_pass_rate` follows the same per-project mean-of-run-rates rule (plan 041) so the breakdown agrees with `GET /projects/stats`.
- Auth: `Depends(get_current_user)` + role gate `read_only`. If/when project-level visibility lands, restrict `project_ids` to the caller's visible set; today every authenticated user can see every project, so the gate is just role-based.
- Tests:
  - Unit tests for `get_cross_project_report_analytics` (empty scope, single-project scope, multi-project scope, date filter, status filter, trend zero-fill, archived inclusion).
  - Integration tests for the endpoint (happy path, empty result, `project_ids=[unknown]` → empty payload, role 401/403, malformed date param → 422).

### Out of scope

- Caching layer (Redis) — same call as plan 027.
- Project-level visibility/ACL changes — current behaviour is "everyone sees every project"; revisit when visibility is added.
- Changes to `/projects/{id}/report-analytics`, `/projects/{id}/dashboard`, `/projects/{id}/metrics`, `/test-runs/{id}/report` — they stay unchanged for existing consumers.
- A unified `GET /reports/analytics?project_id=N` collapse of the two endpoints — kept separate to avoid widening plan 027's response and to keep path semantics consistent with `/projects/stats` (per-project) vs `/projects/{id}/stats` (single).
- Per-project trend overlay — `trend` stays a single aggregated series; per-project trend is a future enhancement.
- Frontend changes (separate plan in `web-testoria`).

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/report.py` | Add `project_id` + `project_name` to `RunAnalyticsItem`; add `PerProjectAnalyticsRow` and `CrossProjectReportAnalyticsResponse` |
| services | `app/services/report_service.py` | Add `get_cross_project_report_analytics()`; extract `_resolve_project_scope(db, project_ids, include_archived)` returning `list[int]` of project ids actually used; have `get_report_analytics()` populate `RunAnalyticsItem.project_id` (it already knows the project) — no behaviour change for existing callers |
| router | `app/api/v1/reports.py` | Add `GET /reports/analytics` route with the same date/status param dependencies the per-project version uses; reuse `parse_iso_date` helper if one exists, otherwise mirror the per-project parsing |
| tests | `tests/unit/test_report_service.py` | Unit tests for `get_cross_project_report_analytics` and `_resolve_project_scope` |
| tests | `tests/integration/test_reports_api.py` | Integration tests for `GET /reports/analytics` |
| docs | `docs/06-generated/endpoints.md` | Add the new endpoint row + `CrossProjectReportAnalyticsResponse` shape; note the additive `project_id` on `RunAnalyticsItem` |

### Key decisions

- **New endpoint, not an overload.** Keeping `/projects/{id}/report-analytics` typed to a single project keeps the existing schema untouched (no `project_id: int | None` foot-gun) and matches the precedent set by `GET /projects/stats` vs `GET /projects/{id}/stats`. The path `/reports/analytics` lines up with the existing `/reports/custom`.
- **Same aggregation rules as plan 041.** `overall_pass_rate` is the mean of *every* completed run's own pass rate across the in-scope projects — not the mean of per-project means. This keeps the cross-project headline consistent with the Dashboard's overall pass-rate tile (plan-080), where "every completed run is one data point" is the rule. The per-project breakdown rows separately apply the rule per project so each row matches `/projects/stats` for that project.
- **Add `project_id` to `RunAnalyticsItem` unconditionally.** It's always derivable from `TestRun.project_id`; making it optional or branching by endpoint just complicates the schema. `project_name` stays optional and is populated only by the cross-project path (the per-project endpoint already knows the project from the URL — extra join not worth it).
- **`project_ids` defaults to all visible projects, not all projects.** When the param is omitted, the service resolves the caller's visible project set (today: every non-deleted, optionally non-archived project). This is the same default `/projects/stats` uses and preserves a future visibility/ACL hook.
- **`include_archived=false` by default.** Mirrors `/projects/stats`. Cross-project Reports almost always wants to ignore archived noise; users can opt in via `?include_archived=true`.
- **Grouped SQL only — no per-project loop.** Reuse the existing `_aggregate_run_status_counts(db, run_ids)` helper for run-level counts; do test-case distribution and trend with single grouped queries that join through `TestSuite → TestRun → TestResult` filtered by `project_id IN (...)`. Per-project breakdown rows are derived from the same fetched data — one extra `GROUP BY project_id` query for `total_test_runs` / `total_results`, then in-Python attribution of the already-loaded run rates per project.
- **Date filter semantics match plan 027.** `effective_date = COALESCE(completed_at, created_at)`. Runs outside the window are omitted from `runs` and `trend` but `summary.total_test_runs` / `summary.total_test_cases` / `summary.total_test_suites` and `per_project[*]` totals stay project-wide so the totals widget doesn't oscillate as the user drags the date range.
- **Auth.** `Depends(get_current_user)` + `Depends(require_role("read_only"))` (same role gate as the per-project endpoint).

---

## Tasks

### Implementation
- [ ] Define Pydantic schemas in `app/schemas/report.py` (`PerProjectAnalyticsRow`, `CrossProjectReportAnalyticsResponse`); add `project_id: int` and `project_name: str | None = None` to `RunAnalyticsItem`
- [ ] Add `_resolve_project_scope(db, project_ids, include_archived) -> list[int]` helper in `report_service.py`
- [ ] Implement `get_cross_project_report_analytics(db, project_ids, date_from, date_to, run_status, include_trend, include_archived)`
- [ ] Update `get_report_analytics()` to populate `RunAnalyticsItem.project_id` (the run object already carries it — no extra query)
- [ ] Add route `GET /reports/analytics` in `app/api/v1/reports.py` with `response_model=CrossProjectReportAnalyticsResponse`
- [ ] Verify query plans on seed data — make sure `test_results.test_run_id`, `test_runs.project_id`, `test_runs.status` indexes are all used; add a single-column index on `test_runs.completed_at` only if `EXPLAIN ANALYZE` shows a sequential scan for the trend/runs query
- [ ] Write unit tests for `_resolve_project_scope` and `get_cross_project_report_analytics` (empty scope, single project, multi-project, date filter, status filter, trend zero-fill, `include_archived` toggle, per-project breakdown ordering)
- [ ] Write integration tests for the endpoint (happy path, empty result, unknown id in `project_ids`, unauthenticated → 401, read_only allowed, malformed date → 422, `include_trend=false` omits trend)

### Quality check
- [ ] `pytest` — all tests pass (existing + new)
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [ ] `docs/06-generated/endpoints.md` updated — new endpoint row, `CrossProjectReportAnalyticsResponse` shape, additive `project_id` / `project_name` on `RunAnalyticsItem`
- [ ] `docs/01-product/features/006-reporting-analytics.md` — add the cross-project endpoint, note the per-project breakdown rule
- [ ] `docs/02-architecture/ARCHITECTURE.md` — `report_service` entries in the "Where is X?" table updated if codemap shifts
- [ ] `docs/08-decisions/changelog.md` — Plan 043 entry: cross-project Reports endpoint, mirrors plan 027 across all projects, mean-of-run-rates rule preserved
- [ ] `docs/04-execution/tech-debt.md` — add an entry if a per-project trend overlay is deferred (likely yes)
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Response payload is large for users with many projects (every run from every project, no paginations) | Medium | Same posture as plan 027 — bound by `runs × 1 row`. Default-window the query to last 90 days when neither date bound is set; document a soft cap and add pagination only when a customer crosses it |
| Cross-project trend query scans every result row across every completed run | Medium | One grouped query with `WHERE TestRun.project_id IN (...) AND TestRun.status = 'completed' AND not_deleted(...)`. Confirm `EXPLAIN ANALYZE` uses `test_results.test_run_id` index plus `test_runs.status` partial filter; add `test_runs.completed_at` index if the date predicate falls back to a scan |
| `RunAnalyticsItem.project_id` addition silently breaks an existing consumer that strict-parses the schema | Low | Field is additive, server-populated, never `null`. Pydantic clients tolerate extra fields by default. CLI / web both decode permissively today |
| Mean-of-run-rates rule produces a counter-intuitive overall when one project has 1 completed run and another has 50 | Medium | Document the rule in the response schema and feature doc — same guidance plan 080 used. Per-project breakdown rows make the imbalance visible |
| `include_archived` mismatch between `/reports/analytics` and `/projects/stats` if defaults drift | Low | Both default to `false`; lock the default in the route signature and assert it in an integration test |
| Visibility/ACL added later changes who-sees-what without updating this endpoint | Medium | Resolve the project scope through a single `_resolve_project_scope` helper so when visibility lands there is exactly one place to plug it in |

---

## Definition of done

- [ ] `GET /reports/analytics` returns the documented schema for: empty scope, single project, multi-project, date-windowed, status-filtered
- [ ] Auth and role enforcement tested (401 unauthenticated, 200 read_only+)
- [ ] Unit test coverage ≥ 85% for `get_cross_project_report_analytics` and `_resolve_project_scope`
- [ ] Integration tests cover happy path + 401/422 + empty payload
- [ ] `summary.overall_pass_rate` numerically equals the mean of every completed run's own `pass_rate` in scope under a snapshot fixture
- [ ] `per_project[*].overall_pass_rate` numerically agrees with `/projects/stats` for the same project under the same fixture
- [ ] `RunAnalyticsItem.project_id` populated on both endpoints; existing per-project endpoint payload byte-identical otherwise (snapshot test)
- [ ] Endpoint p95 latency < 300 ms locally with 5 projects × 50 runs × 5k results (Postgres)
- [ ] Docs updated; plan moved to `completed/`
