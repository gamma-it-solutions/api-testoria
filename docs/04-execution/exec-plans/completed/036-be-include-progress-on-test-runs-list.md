# Execution Plan: Include per-run progress on `GET /test-runs` (opt-in) so the dashboard charts have data

**Date**: 2026-04-20
**Author**:
**Status**: Completed

---

## Goal

Add opt-in per-run `progress` on the list endpoint `GET /api/v1/test-runs` via a new `?include=progress` query param. When set, every item in the response carries a populated `TestRunProgress` (passed / failed / blocked / skipped / no_run / total / pass_rate), computed in one SQL round-trip. The dashboard's "Pass Rate Trend" and "Test Results Distribution" charts depend on this to render anything at all (web plan 060 consumes it).

---

## Context

The web dashboard (`/`) has two sections that are currently **blank**:

- **Pass Rate Trend** — weekly line chart over the last 6 weeks
- **Test Results Distribution** — doughnut chart of passed / failed / blocked / skipped totals

Both are computed client-side from the list of runs returned by `GET /test-runs`:

```ts
// web: src/views/dashboard/DashboardView.vue:110-119
runs.forEach((run) => {
  if (run.progress) {                 // ← always false today
    passed += run.progress.passed;
    …
  }
});
```

```ts
// web: src/views/dashboard/DashboardView.vue:157-158
const completedRuns = runs.filter(
  (r) => r.status === "completed" && r.progress && r.completed_at,  // ← filters out every run
);
```

The backend's `TestRunResponse` (`app/schemas/test_run.py:27-41`) does not include `progress`:

```py
class TestRunResponse(BaseModel):
    id: int
    project_id: int
    ...
    status: str
    completed_at: datetime | None
    # no progress field
```

So the web type `TestRun.progress?: TestRunProgress` is always `undefined` when populated from the list endpoint, and both dashboard computations short-circuit. Per-run progress is only available via a separate call (`GET /test-runs/{id}/progress`) — too expensive to fan out N times on the client.

The fix is to surface `progress` on the list response, but only when the caller asks for it. The test-runs list view (`TestRunListView.vue`) and the test-run dropdowns don't need it; the dashboard does. An opt-in param keeps the default cheap.

### Why not change `TestRunResponse` unconditionally?

- Every consumer of the list pays a join + aggregation cost on every call
- `TestRunListView` shows 50+ runs at a time with no need for exact counts (it has its own summary elsewhere)
- Opt-in signals intent and keeps caches / ETags stable for the default shape

### Why not add dedicated dashboard aggregation endpoints?

- Plan-053 added `bulk_project_stats` for summary metrics; adding another endpoint specifically for trend / distribution duplicates the run-enumeration work already happening
- The dashboard's trend math is straightforward client-side once per-run totals are available; server-side weekly bucketing is a separate optimisation, logged as follow-up
- Minimal-diff wins: one param, one SQL change, unblocks the UI

---

## Scope

### In scope

- New query param `include: Literal["progress"] | None = None` on `GET /api/v1/test-runs`
- When `include=progress`:
  - Every `TestRunResponse` in the response has `progress: TestRunProgress | None` populated
  - Computation in one query: single `SELECT` with a grouped `LEFT JOIN` on `TestResult`, counting by status per `test_run_id` for the page's runs
  - `no_run` in the progress counts uses the same synthesis rule as `GET /test-runs/{id}/progress` (assumes api plan 032 is merged — `no_run` is a first-class status)
  - `pass_rate` on the embedded progress follows api plan 035's contract (0..1 ratio over all statuses); `None` when `total == 0`
- Response schema: extend `TestRunResponse` with `progress: TestRunProgress | None = None` — `None` by default, populated only under the opt-in path
- Unit tests: service path computes progress consistently with `get_progress` on the same run
- Integration tests: `?include=progress` populates; no param leaves `progress` null; invalid `include=foo` → 400
- Performance check: paginated list of 50 runs with include=progress finishes in one query (or at most two: runs + progress aggregate); EXPLAIN shows no N+1
- Docs: `endpoints.md` documents the new param and the shape contract

### Out of scope

- A dashboard-specific trend / distribution endpoint — logged as follow-up; this plan is the minimal unblock
- `include=cases`, `include=suite`, or any other sibling include values — the shape is a single enum, extendable later
- Changing the list endpoint's pagination / sort / filters — unchanged
- Server-side weekly bucketing for the trend chart — client already does this; revisit if latency hurts
- Including progress on single-item `GET /test-runs/{id}` — already reachable via `/test-runs/{id}/progress`; add later if requested
- Making `progress` populated by default (explicitly rejected — consumers that don't need it shouldn't pay)
- Event-driven progress (Centrifugo push) — out of scope

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_run.py` | Add `progress: TestRunProgress \| None = None` to `TestRunResponse` |
| services | `app/services/test_run_service.py` | Extend `list_runs(…, include_progress: bool = False)`; when `True`, compute per-run progress for the page in one batched query and attach to each returned run |
| router | `app/api/v1/test_runs.py` | Add `include: Literal["progress"] \| None = None` query param on the list endpoint; forward as `include_progress=True` to the service |
| tests | `tests/unit/test_test_run_service.py` | Batch progress matches single-run progress on the same data; zero-case runs get progress with `total=0, pass_rate=None` |
| tests | `tests/integration/test_test_runs_api.py` | `?include=progress` populates; default omits; invalid value → 400; large-page N+1 guard |
| docs | `docs/06-generated/endpoints.md` | Document the new param + response shape |

### Key decisions

- **Opt-in via a typed enum param.** `include: Literal["progress"] | None` leaves room to add `include=cases` / `include=suite` later without renaming. Use a string list (`?include=progress,suite`) only if that second consumer actually arrives.
- **Batched query, not N separate calls.** One `SELECT test_run_id, status, count(*) FROM test_results WHERE test_run_id IN (…) GROUP BY test_run_id, status` — plus a case-set total for the `no_run` denominator. Attach in Python after the list query. `EXPLAIN` verified on a seed of 50+ runs.
- **`no_run` comes from the same resolver as `/progress`.** Use the shared helper (introduced by api plan 033 / 034's `_resolve_run_case_set`) so "progress" means the same thing on both endpoints. No ad-hoc no_run math in list_runs.
- **`pass_rate` conforms to api plan 035.** 0..1 ratio; denominator includes every status. If plan 035 isn't merged yet, coordinate release so web plan 060 sees consistent values.
- **Response default stays `progress: None`.** Clients that don't pass `include=progress` still deserialise today's shape fine; the field is optional.
- **Auth / role unchanged.** Same project-scoped read check as the existing list endpoint.
- **No caching layer.** If the numbers are ever stale, it's because the underlying data moved — no server-side caching is added here.

---

## Tasks

### Implementation
- [x] Confirm api plans 032 (no_run rename), 033 (flat run-cases completeness), and 035 (pass_rate contract) are merged or scheduled with this one; without 032/035 the embedded progress would diverge from `/progress`
- [x] Add `progress: TestRunProgress | None = None` to `TestRunResponse`
- [x] Extend `test_run_service.list_runs` signature: add `include_progress: bool = False`
- [x] When `include_progress`:
  - [x] Collect the page's `run_ids`
  - [x] Batch query: counts per `(test_run_id, result.status)` via one grouped SELECT
  - [x] Batch query (or single combined CTE): case-set totals per run (to resolve `no_run = total_cases - executed_cases`)
  - [x] Assemble a `TestRunProgress` per run using the shared helper; use `app/utils/stats.py::pass_rate` from api plan 035
  - [x] Attach as `run.progress` before returning
- [x] Extend router: add `include: Literal["progress"] | None = None` query param; pass `include_progress=(include == "progress")` to the service; return 400 if `include` is a non-empty string other than `"progress"`
- [x] Ensure the response_model still validates (TestRunResponse now has `progress` field)
- [x] OpenAPI: `include` param documented with its allowed value in `/docs`
- [x] Unit tests:
  - [x] Batch progress for a page of runs matches per-run `get_progress` for each
  - [x] Run with zero cases: `progress.total == 0`, `pass_rate is None`
  - [x] Run with every case `no_run`: `progress.no_run == progress.total`, `pass_rate == 0.0`
  - [x] Run with explicit-selection and suite-derived paths both resolve correctly
  - [x] `include=None` → `progress is None` on every run
- [x] Integration tests:
  - [x] `?include=progress` populates; field structure matches `TestRunProgress`
  - [x] Default call leaves `progress` null
  - [x] `?include=bogus` → 400
  - [x] 50-run page with `?include=progress` issues ≤ 3 SQL round-trips (assert via `sqlalchemy.events` in the test, or log a failure if N+1 detected)
  - [x] 403 for a user without project access (unchanged)
- [x] Manual smoke: hit `/api/v1/test-runs?include=progress` against a seeded project; spot-check values against `/test-runs/{id}/progress`

### Quality check
- [x] `pytest`
- [x] `ruff check app tests`
- [x] `mypy app`
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — document `include` param + the progress shape on the list endpoint
- [x] `docs/02-architecture/backend/api-layer.md` — note the "opt-in includes via enum param" pattern if it's new to the project
- [x] `docs/02-architecture/backend/service-layer.md` — document the batched-progress query next to `_resolve_run_case_set`
- [x] `docs/08-decisions/changelog.md` — record: opt-in `?include=progress` on the list endpoint; batched computation; rejected alternatives (always-on; dedicated dashboard aggregation endpoint)
- [x] `docs/04-execution/tech-debt.md` — log follow-ups: (a) `include=suite` / `include=cases` if future consumers need them, (b) a dedicated dashboard trend aggregation endpoint if client-side weekly bucketing becomes slow
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Batched query does not hit the existing `(test_run_id, status)` index and runs slowly on large result sets | Medium | `EXPLAIN` in CI / smoke; add an index if the query plan shows a seq scan; document the index in `db-schema.md` |
| N+1 reintroduced later by a well-meaning refactor | Medium | Add a test that counts SQL statements using SQLAlchemy event hooks; fails the suite if `include=progress` triggers more than the allowed threshold |
| Embedded progress drifts from `/progress` (two code paths for the same math) | Medium | Both paths go through `_resolve_run_case_set` + `pass_rate` helper; unit test asserts parity on the same run |
| Default response shape changes break a strict client | Low | Field is additive and optional; every deserialiser ignores unknown keys |
| Dashboard requests `?include=progress` on a very large page (1000 runs) and latency spikes | Medium | Existing list endpoint already paginates; document the recommended page size for include=progress (e.g. 100); follow-up plan for server-side aggregation if dashboards grow |
| Web plan 060 ships before this and the dashboard appears broken in a different way | Low | Both plans coordinated; web plan explicitly depends on this one |
| A future `include=progress,cases` combination is ambiguous | Low | Current enum is single-valued; expanding to list values is a separate decision with its own plan |

---

## Definition of done

- [x] `GET /api/v1/test-runs?include=progress` returns every run with a populated `TestRunProgress` (passed / failed / blocked / skipped / no_run / total / pass_rate)
- [x] `pass_rate` on the embedded progress is a 0..1 ratio over all statuses (per api plan 035)
- [x] No-param call returns runs with `progress: null` (regression guard)
- [x] Invalid `include` value returns 400
- [x] Batched computation issues ≤ 3 SQL statements regardless of page size (test-enforced)
- [x] Embedded progress matches `GET /test-runs/{id}/progress` for the same run on the same data
- [x] Auth unchanged; confirmed by integration test
- [x] Docs updated; changelog entry explains the opt-in
- [x] PR checklist completed
