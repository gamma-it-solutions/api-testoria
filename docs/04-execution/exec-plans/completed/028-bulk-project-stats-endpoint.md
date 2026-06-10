# Execution Plan: Bulk Project Stats Endpoint

**Date**: 2026-04-17
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Expose `GET /projects/stats` returning per-project aggregate counts (cases, suites, runs) and pass rate in a **single round-trip**, so the home dashboard can show its "Overall Pass Rate" widget without fanning out over every project.

---

## Context

The web dashboard (`web-testoria/src/views/dashboard/DashboardView.vue`) currently fetches the full runs list **and** the full test cases list for every active project — `testRunsStore.fetchAllRuns(projectIds)` + `testCasesStore.fetchAllCases(projectIds)` — purely to compute per-project pass rates client-side via `composables/usePassRateAggregation`. On a workspace with 10 projects × ~20 runs × ~200 cases this is 20 round-trips, each carrying a payload much larger than the single float the view actually displays.

The existing `GET /projects/{id}/stats` already returns the right numbers per project, but there's no bulk flavour. The dashboard therefore either (a) loops the stats endpoint (still N round-trips, but small payloads) or (b) keeps its current fan-out.

A single `GET /projects/stats` built on top of the **same** grouped SQL queries used in `get_dashboard()` / `get_report_analytics()` removes the loop entirely and gives the frontend a stable contract. This is the fix called out in web-testoria `tech-debt.md` ("Bulk project stats endpoint (plan-050)").

Pairs with the frontend dashboard rewire in `web-testoria/plan-053` (next).

---

## Scope

### In scope

- New endpoint: `GET /api/v1/projects/stats`
- Query params:
  - `include_archived` (bool, default `false`) — mirrors `GET /projects` filter semantics
  - `project_ids` (repeated int, optional) — restrict to a caller-supplied subset (used by the Reports overview and any future per-workspace filter)
- New Pydantic response schemas in `app/schemas/project.py`:
  - `ProjectStatsItem` — one row: `project_id`, `name`, `is_archived`, `total_test_cases`, `total_test_suites`, `total_test_runs`, `active_runs`, `pass_rate` (float | null)
  - `ProjectStatsBulkResponse` — `items: list[ProjectStatsItem]`, `total: int`
- New service function `project_service.get_bulk_stats(db, *, include_archived, project_ids)` using four grouped queries (one per counted dimension), no per-project loop
- Refactor `project_service.get_stats()` to reuse the same internal counting helpers so the single- and bulk-project paths stay byte-identical
- Unit tests for the service; integration tests for the endpoint (happy path, empty workspace, `project_ids` filter, archived handling, 401/403)

### Out of scope

- Removing the existing `GET /projects/{id}/stats` — it stays; the two live side by side.
- Any schema / migration changes — the counts are cheap enough with existing indexes.
- Caching layer (Redis) — revisit if p95 breaches the budget below.
- Pagination — stats payload is ~120 bytes per project; a workspace with 1000 projects is still well under 150 KB.
- Frontend changes — tracked in the paired web-testoria plan.

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/project.py` | Add `ProjectStatsItem`, `ProjectStatsBulkResponse`; `ProjectStatsItem` is a superset of the existing `ProjectStats` plus `project_id`, `name`, `is_archived`, `active_runs` |
| services | `app/services/project_service.py` | Add `get_bulk_stats(...)`; extract shared helper(s) so `get_stats()` keeps its current behaviour |
| router | `app/api/v1/projects.py` | Add `GET /projects/stats` **before** `/{project_id}` routes (FastAPI matches the first route that fits — `stats` must not be interpreted as a project id) |
| tests | `tests/integration/test_projects_api.py` | New tests for the bulk endpoint: no projects, multiple projects with mixed run/result counts, `project_ids=` filter, `include_archived` |
| tests | `tests/unit/test_project_service.py` (create if missing) | Unit tests for `get_bulk_stats` covering grouped-count correctness and empty input |
| docs | `docs/06-generated/endpoints.md` | New row + response shape |

### Key decisions

- **Single grouped query per dimension**, not per project:
  - `SELECT p.id, COUNT(s.id) FROM projects p LEFT JOIN test_suites s ... GROUP BY p.id` — suite counts for every project in one round-trip
  - `SELECT p.id, COUNT(tc.id) FROM projects p LEFT JOIN test_suites s ... LEFT JOIN test_cases tc ... GROUP BY p.id` — case counts
  - `SELECT tr.project_id, tr.status, COUNT(*) FROM test_runs tr ... GROUP BY tr.project_id, tr.status` — run counts + active-run counts in one go
  - `SELECT tr.project_id, tr_result.status, COUNT(*) FROM test_results tr_result JOIN test_runs tr ... GROUP BY tr.project_id, tr_result.status` — pass rate inputs
  - Total queries: **4** regardless of project count (vs. `4 × N` for a loop).
- **Pass rate semantics**: `passed_results / total_results`, returning `None` when `total_results == 0`. Same formula used by `get_stats()` today — bulk output must match `get_stats()` row-by-row for the same project.
- **Route ordering**: FastAPI route matching is first-registered-wins. `GET /projects/stats` must be declared **before** `GET /projects/{project_id}` in `projects.py`; otherwise "stats" is interpreted as a project id and the handler returns 422. Confirmed by the same trick used in `test_runs.py` for `/test-runs/progress`-style sibling routes.
- **Auth**: reuse `_VIEWER` — same as `list_projects` and `get_project_stats`. No new role.
- **Filtering**: when `project_ids` is supplied but is empty after URL parsing, treat as "no filter" to match `fastapi.Query(default=None)` semantics; when it's a non-empty list, hard-filter every subquery to it.
- **Archive filter is consistent with `/projects` list**: `include_archived=false` hides archived projects entirely (not just from the count; they are not rows in the response either).
- **Sorting**: `ORDER BY name ASC` — same default the frontend already sorts by after aggregation.

### Performance budget

- ~4 grouped queries, each bounded by `len(projects)` rows at worst. On a workspace of 1,000 projects with 20,000 runs and 100,000 results: p95 < 150 ms on local Postgres with existing indexes (`test_runs.project_id`, `test_results.test_run_id`). Verify with `EXPLAIN ANALYZE`.

---

## Tasks

### Implementation

- [x] Add `ProjectStatsItem`, `ProjectStatsBulkResponse` to `app/schemas/project.py`
- [x] Implement `project_service.get_bulk_stats(...)` using four grouped queries
- [x] Extract shared counting internals so `get_stats()` stays a thin wrapper and returns identical numbers
- [x] Add `GET /projects/stats` route **above** `/{project_id}` in `app/api/v1/projects.py`
- [x] Verify query plans on seed data (no sequential scans on `test_results` / `test_runs`)
- [x] Unit tests for `get_bulk_stats` (empty, happy path, `project_ids` filter, archived)
- [x] Integration tests (happy path, empty, `project_ids` filter, 401, 403 where applicable)

### Quality check

- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — clean
- [x] `mypy app` — clean
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update

- [x] `docs/06-generated/endpoints.md` — new row and response shape
- [x] `docs/02-architecture/ARCHITECTURE.md` — "Where is X?" entry for project_service if the table references it
- [x] `docs/08-decisions/changelog.md` — entry for the bulk stats decision
- [x] `docs/04-execution/tech-debt.md` — cross-reference resolution (the debt lives on the frontend, but note the enabler here)
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Route `/projects/stats` shadowed by `/projects/{id}` path | High if registered in wrong order | Integration test hits `/projects/stats` and asserts 200, not 422; code review checks route declaration order |
| Grouped joins skip projects with zero suites / runs / results | Medium | `LEFT JOIN` on every dimension; coerce missing values to 0 in the service before returning |
| `project_ids` array serialized inconsistently by callers (repeated param vs CSV) | Medium | Mirror the Axios `paramsSerializer: { indexes: null }` pattern already used elsewhere; accept `list[int] \| None` via `Query(default=None)` |
| `get_stats()` and `get_bulk_stats()` diverge over time (drifting pass rate semantics) | Medium | Shared internal helpers + a regression test asserting per-project equality between the two endpoints for the same project |

---

## Definition of done

- [x] `GET /projects/stats` returns 200 with a bounded-shape `ProjectStatsBulkResponse`
- [x] For any project, bulk response and `GET /projects/{id}/stats` produce identical counts and pass rate (regression test)
- [x] Auth and role enforcement verified (401 unauthenticated, 200 for any viewer role)
- [x] Query count: 4 round-trips to Postgres regardless of project count (verified with query counter in a unit test)
- [x] Docs updated
