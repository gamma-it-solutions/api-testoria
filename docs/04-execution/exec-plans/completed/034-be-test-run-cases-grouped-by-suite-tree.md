# Execution Plan: Expose test-run cases + results grouped by the suite tree

**Date**: 2026-04-20
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Serve a single backend projection that returns every case in a test run together with its optional `TestResult`, grouped into the run's suite tree, so the web detail page (`/test-runs/:id`) and execution page (`/test-runs/:id/execute`) can render the same source tree without re-fetching or re-joining client-side.

---

## Context

Web plan 056 replaces two ad-hoc client-side data sources with one shared "suite tree with results" model:

- `TestRunDetailView.vue` today calls `fetchResults(runId)` (executed-only `GET /test-results?run_id=`) and has no suite grouping — web plan 055 already moves it toward `GET /test-runs/{id}/cases`.
- `TestRunExecutionView.vue` today fetches **all** project cases via `testCasesStore.fetchTestCases(projectId)`, then separately fetches results, and re-groups client-side by `suite_id` using a flat list of suites (`TestRunExecutionView.vue:90-126, 530-536`). This pulls cases that are *not* part of the run and silently depends on the project's flat suite list.

Both pages want the same logical shape:

```
run
├── suite A
│   ├── case 1  [status, result?]
│   ├── case 2  [status, result?]
│   └── suite A.1 (nested)
│       └── case 3
└── suite B
    └── case 4
```

Api plan 033 already covers pagination + field completeness on the flat `GET /test-runs/{id}/cases`. This plan builds on that: add an opt-in `?group_by=suite` projection (or a sibling endpoint) that returns the same cases **nested under the run's suite hierarchy**, with per-suite progress counters so the UI can render the tree in one pass.

Assumes merged/in-flight:
- Plan 032 — `no_run` status literal on `TestResultResponse`
- Plan 033 — `GET /test-runs/{id}/cases` pagination + field completeness
- Web plan 055 — detail page consumes flat cases-with-results
- Web plan 056 — the consumer of this plan

---

## Scope

### In scope

- New response shape `TestRunSuiteTree` exposing nested suites with their cases + optional results for a single run
- Delivered as an opt-in projection on the existing endpoint: `GET /api/v1/test-runs/{run_id}/cases?group_by=suite` — response type varies by param (documented)
- Only suites that contain at least one case belonging to the run's case-set are included (no empty branches)
- Each node carries:
  - `suite: { id, name, parent_id, order }`
  - `progress: { total, passed, failed, blocked, no_run, other }` — per-suite rollup over that suite's own cases only (not its descendants)
  - `cases: TestCaseWithResult[]` — cases directly in this suite
  - `children: TestRunSuiteNode[]` — nested suites with at least one case in the run
- Flat `?group_by=` omitted preserves today's `{run, cases, total}` response (back-compat)
- Pagination: grouped projection is **not paginated** in this plan — the run's case-set is already capped by plan 033 (`limit <= 2000`); if grouped and flat share the same cap, both fit in one response. Document the cap.
- Sort within a suite: `(id)`; suite children sorted by `(order, id)`
- Unit tests for: nested trees, single-suite run, empty-result path (`result: null`), progress rollup, soft-deleted suite exclusion, soft-deleted case exclusion
- Integration tests for the projection on both the "suite-derived" and "explicit case selection" run paths
- Docs: `endpoints.md` updated with `group_by` param, response variants, and per-suite progress field

### Out of scope

- Server-side recursive rollup of progress across descendants — UI can sum on render; keeping the response flat-at-each-node avoids confusion
- A separate `/test-runs/{id}/tree` URL — keep the projection on the existing endpoint via `group_by` to avoid endpoint sprawl; can split later if needed
- Suite ordering changes, drag-and-drop, or persistence — read-only projection
- Filtering (`?status=`, `?tag=`) — logged as tech debt; web 056 filters client-side within the tree
- Materialising `no_run` `TestResult` rows server-side (explicitly rejected — see plan 033)
- Pagination of the grouped projection
- Changes to `GET /test-results`

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_run.py` | Add `TestRunSuiteNode` (suite + progress + cases + children) and `TestRunSuiteTree` (run + roots + total); extend the router's response-model union with the new shape under `group_by=suite` |
| services | `app/services/test_run_service.py` | New `get_suite_tree(db, run_id)` that (a) resolves the run's case-set (same logic as `get_with_cases`), (b) eagerly loads those cases' `TestResult` for this run, (c) loads the involved `TestSuite` rows with their `parent_id`, (d) assembles a nested structure keyed by `suite_id`, (e) computes per-suite progress from the loaded results |
| router | `app/api/v1/test_runs.py` | `group_by: Literal["suite"] \| None = None` query param; when `"suite"`, delegate to `get_suite_tree` and return `TestRunSuiteTree`; when absent, keep today's `TestRunWithCases` |
| migration | — | No DB change |
| tests | `tests/unit/test_test_run_service.py` | Tree assembly, progress rollup, null-result handling, soft-delete filters, suite-derived vs explicit selection parity |
| tests | `tests/integration/test_test_runs_api.py` | `group_by=suite` response shape; nested children; per-suite `progress`; `case_status` + `automation_id` + `tags` present on each case |
| docs | `docs/06-generated/endpoints.md` | New param + variant response |

### Key decisions

- **One endpoint, two projections**. Adding `?group_by=suite` keeps the URL space clean and signals "same data, different shape". The router branches on the param and returns a different `response_model`; OpenAPI documents both via a discriminated union or two operations (whichever renders cleanly in `/docs`).
- **No recursive progress rollup on the backend**. Rolling up across descendants is a UI concern (collapsing, filtering changes the denominators). The backend returns per-node progress over *this node's own cases*; the UI sums for display. This avoids two sources of truth.
- **Suites with no cases in the run are omitted**. The run defines the case-set; suites that contribute zero cases should not appear. Web plan 056 depends on this — it drives the rendered tree directly.
- **Flat vs grouped share the same case-set resolver**. A private `_resolve_run_case_set(db, run_id)` helper returns `(cases, results_by_case_id)`; `get_with_cases` and `get_suite_tree` both call it. Prevents drift between the two projections.
- **Default sort: suite by `(order, id)`, cases within a suite by `id`**. Matches web 056's rendering and plan 033's default flat order so users don't see a reshuffle when toggling group-by.
- **Nesting depth is whatever the DB has**. No artificial cap; the UI handles deep trees with a collapse state.
- **Auth / role**: unchanged — same permission check as the flat endpoint.

---

## Tasks

### Implementation
- [x] Confirm plans 032 + 033 are merged before starting — `TestCaseWithResult` must already carry `automation_id`, `tags`, and `case_status` (if renamed)
- [x] Factor out `_resolve_run_case_set(db, run_id) -> tuple[list[TestCase], dict[int, TestResult]]` in `test_run_service.py`
- [x] Refactor `get_with_cases` to use the helper (no behaviour change; covered by existing tests)
- [x] Implement `get_suite_tree(db, run_id) -> TestRunSuiteTree`:
  - [x] Call `_resolve_run_case_set`
  - [x] Collect the distinct `suite_id`s touched
  - [x] Load those suites plus every ancestor via a single recursive CTE or a closure-table query (document whichever the schema supports)
  - [x] Build `{suite_id: node}` map; attach cases + results
  - [x] Link children to parents by `parent_id`; collect roots (nodes whose parent is null *or* whose parent is not in the set)
  - [x] Compute `progress` per node from its own cases (passed/failed/blocked/no_run via `result.status` or `"no_run"` when result is null)
- [x] Add Pydantic schemas: `TestRunSuiteNode`, `TestRunSuiteTree`
- [x] Wire `group_by` query param on the existing endpoint; branch response-model
- [x] Reject `group_by=anything_else` with 400
- [x] OpenAPI: ensure `/docs` shows both response shapes (openapi_extra with oneOf if needed)
- [x] Unit tests:
  - [x] Run with a single suite, no nesting
  - [x] Run with nested suites (A > A.1 > A.1.1)
  - [x] Run with cases spread across sibling suites
  - [x] Every case has no result → each node's `progress.no_run == progress.total`
  - [x] Soft-deleted cases excluded; soft-deleted suites excluded
  - [x] Explicit selection run and suite-derived run yield the same tree shape for the same case-set
  - [x] A suite whose only case is soft-deleted is not in the tree
- [x] Integration tests:
  - [x] `GET …?group_by=suite` returns `TestRunSuiteTree` with `run`, `roots`, `total`
  - [x] Per-suite `progress` totals sum to `total` across roots (leaf-level sum)
  - [x] `case_status`, `automation_id`, `tags` present on each case
  - [x] Without `group_by`, response shape unchanged (regression guard)
  - [x] Invalid `group_by=foo` → 400
  - [x] 403 for user without project access
- [x] Smoke against a seeded run with nested suites; eyeball the tree

### Quality check
- [x] `pytest`
- [x] `ruff check app tests`
- [x] `mypy app`
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — `group_by=suite` param + `TestRunSuiteTree` response shape; link the two projections
- [x] `docs/06-generated/db-schema.md` — no change expected; verify
- [x] `docs/02-architecture/backend/api-layer.md` — note the projection-via-query-param pattern if it's new to the project
- [x] `docs/02-architecture/backend/service-layer.md` — document `_resolve_run_case_set` as the shared resolver for any future projection
- [x] `docs/01-product/features/` — update the test-run feature file to mention the grouped projection
- [x] `docs/08-decisions/changelog.md` — record: one endpoint + `group_by` vs separate `/tree` URL; per-node (not rollup) progress; suites with zero run-cases omitted
- [x] `docs/04-execution/tech-debt.md` — add: (a) server-side `?status=`/`?tag=` filters on the grouped projection, (b) optional recursive-rollup flag if the UI ever asks for it, (c) pagination of the grouped projection if run sizes grow past the flat cap
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Loading ancestor suites via recursive CTE isn't portable across the project's SQLAlchemy dialect | Low | Postgres supports recursive CTEs; use `text()` with a documented query; integration-tested |
| Two projections drift on field set (case fields appear in flat but not grouped) | Medium | Both call `_resolve_run_case_set` and serialise via the same `TestCaseWithResult` schema; single source of truth |
| Per-suite progress counters confuse the UI when it also sums across children | Low | Documented explicitly: progress is per-node, not rolled up; web 056 sums on render |
| Large runs with deep nesting produce a big payload | Low | Same cap as plan 033 (2000 cases); tree overhead is small (suite rows <= distinct suite count) |
| `group_by` variant response breaks OpenAPI clients that assume a single shape | Low | Default (no param) keeps the original shape; clients opt-in |
| Explicit-selection runs where the selected case's suite has been reparented since selection | Low | Read current `parent_id` at query time; document as a known characteristic; out of scope |
| Per-step status from plan 031 not reflected in `progress` rollup | Low | Rollup uses `result.status` (aggregate); per-step detail is available on the case payload for the UI |

---

## Definition of done

- [x] `GET /api/v1/test-runs/{run_id}/cases?group_by=suite` returns `TestRunSuiteTree` with nested `roots`, each node carrying `suite`, `progress`, `cases`, `children`
- [x] Per-node `progress` sums (across all leaf nodes) equal `total`
- [x] `case_status`, `automation_id`, `tags`, `result | null` present on each case in the tree
- [x] Cases without a `TestResult` are returned with `result: null`
- [x] Suites with zero run-cases are absent from the tree
- [x] Flat projection (`group_by` omitted) is unchanged (regression test)
- [x] Invalid `group_by` → 400
- [x] Auth unchanged; confirmed by integration test
- [x] Unit + integration tests cover nested trees, null-result rollup, soft-delete exclusion, suite-derived vs explicit selection parity
- [x] Works against a seeded run with nested suites
- [x] Docs updated
- [x] PR checklist completed
