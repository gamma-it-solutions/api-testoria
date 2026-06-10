# Execution Plan: Ensure `GET /test-runs/{id}/cases` is complete enough to back the detail-page "all cases" view

**Date**: 2026-04-20
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Make `GET /api/v1/test-runs/{run_id}/cases` fit-for-purpose as the single data source for the web detail page's "show every case, including cases that haven't been run" view (web plan 055): guarantee every field the UI needs is present on `TestCaseWithResult`, raise or replace the hard 500-row cap with explicit pagination, provide a stable sort order, and verify that cases without a result still come through cleanly.

---

## Context

The endpoint already exists and returns cases with a nullable result:

```py
# app/services/test_run_service.py:278-327
select(TestCase, TestResult)
  .outerjoin(TestResult, and_(TestResult.test_case_id == TestCase.id,
                              TestResult.test_run_id == run_id))
  ...
  .order_by(TestCase.id).limit(500)
```

```py
# app/schemas/test_run.py:64-76
class TestCaseWithResult(...):
    id: int
    title: str
    ...
    status: str
    tags: list[str]
    result: TestResultResponse | None = None

class TestRunWithCases(BaseModel):
    run: TestRunResponse
    cases: list[TestCaseWithResult]
```

Web plan 055 replaces `fetchResults(runId)` with this endpoint so the detail page shows every case — cases with no result are displayed with the new `no_run` status introduced by plans 054 + 032. Before that switch lands, a handful of small gaps need to be closed on the backend so the UI doesn't have to compensate:

1. **500-row hard cap**: `.limit(500)` truncates silently. Runs with > 500 cases exist in some projects. Truncation without a cursor or warning would make the UI say "this run has X cases" while the list shows 500.
2. **Sort order**: currently `ORDER BY TestCase.id`. For multi-suite runs this interleaves suites confusingly. A stable, UI-friendly order is `(suite_id, id)` or caller-chosen.
3. **Field completeness**: confirm `title`, `type`, `priority`, `automation_id`, `tags`, and (for the "not yet run" panel) `description` / `steps` are all serialisable without an extra round-trip. If `description` / `steps` are large, keep them off the list payload and expose via the existing single-case GET.
4. **`status` field on `TestCaseWithResult`**: this is the case's own status, not the result's — verify the name doesn't collide with `result.status` in a way that confuses consumers, and consider renaming to `case_status` for clarity in the response schema.
5. **Filters**: none today. Web may want `?status=no_run` server-side to avoid transferring every case for large runs. Minimal first pass: ship without filters; tech-debt log the optimisation.

This is the companion plan to web plan 055 and a follow-on to api plan 032 (`no_run` rename). It assumes 032 is merged: the `TestResultResponse.status` carries `"no_run"` where applicable; cases with null `result` represent the same "has not been touched" state and the web synthesises the `no_run` badge client-side.

---

## Scope

### In scope
- Raise the 500-row cap: either bump to a safe ceiling (e.g. 5000) **or** introduce explicit `limit` + `offset` query params (default `limit=500`, max `2000`); prefer explicit pagination if other list endpoints already use it
- Add `order_by` option: default `(suite_id, id)`; optional `?sort=(id|title|priority|suite)` with a documented default
- Verify every field `TestCaseWithResult` declares is populated by the service; fix any silent `None` where the column is non-nullable
- Include `automation_id` on `TestCaseWithResult` (plan 029 added this to the case model — confirm it's surfaced here)
- Include `tags` on `TestCaseWithResult` (already loaded via `selectinload(TestCase.tags)` — confirm serialised)
- Rename the ambiguous `status: str` on `TestCaseWithResult` → `case_status: str` if product/engineering agrees, with a deprecation window keeping both fields for one release
- Unit tests for: empty result (case never run), run with no explicit case selection (suite-derived), run with explicit selection, pagination boundary, sort order
- Integration tests for endpoint response shape + pagination params + sort param
- Docs: `endpoints.md` updated with the new query params and response shape

### Out of scope
- Server-side filtering (`?status=`, `?tag=`, search) — logged as tech debt; 055 works without it
- Cursor-based pagination — offset-based is sufficient for current run sizes
- Materialising empty `TestResult` rows for every case in a run (explicitly rejected; see web plan 055 "Key decisions")
- Changes to `GET /api/v1/test-results` — it remains the "executed only" endpoint
- Soft-delete interaction changes — existing filters on `deleted_at IS NULL` stay
- Any response-envelope restructuring — schema stays `{run, cases[]}`

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/test_run.py` | Add `automation_id: str \| None` and (if renaming) `case_status: str` fields to `TestCaseWithResult`; keep `status` during the deprecation window |
| schemas | `app/schemas/test_run.py` | New optional query-model or router params for `limit` / `offset` / `sort` on `get_with_cases` — wire through |
| services | `app/services/test_run_service.py` | `get_with_cases(db, run_id, *, limit, offset, sort)`; apply pagination and sort; return `{run, cases, total}` so the UI can show "N of M" if truncated |
| router | `app/api/v1/test_runs.py` | Add query params (`limit`, `offset`, `sort`) to the existing endpoint; pass through to the service; keep response shape compatible (now includes `total` alongside `cases`) |
| migration | — | No DB change |
| tests | `tests/unit/test_test_run_service.py` | Pagination, sort order, suite-derived vs explicit-selection paths, null-result path |
| tests | `tests/integration/test_test_runs_api.py` | Endpoint returns total; limit/offset work; sort param honoured; `case_status` present |

### Key decisions

- **Explicit pagination, not silent cap**. Returning 500 and truncating is a footgun. Accept `limit` (default 500, max 2000) and `offset` (default 0). Add `total` to the response so the UI can decide whether to page. If a cursor is needed later, it's additive.
- **Rename `status` → `case_status` with a compat window**. The field is the case's own `status`, but the same payload has `result.status`. "What does `case_status` mean?" is a one-line doc; "why does the result have `.status` but so does the case?" is a recurring confusion. One release of dual-field emission avoids breaking consumers. Deprecation notice in `endpoints.md`.
- **Default sort `(suite_id, id)`**. Matches how the frontend renders the suite tree; `id` alone interleaves suites randomly. Override via `?sort=`.
- **Keep `result: TestResultResponse \| None` as the contract**. Web plan 055 synthesises `no_run` client-side. The backend does not invent result rows.
- **Do not add `?status=` filtering in this plan**. Useful, but requires deciding how `status="no_run"` is interpreted when no result exists (synthetic match). Separate plan — logged as tech debt.
- **`description` / `steps` off the list payload** to keep the row size small for 500+-case runs. The "not yet run" detail panel can fetch the single-case endpoint on click if it needs the steps.
- **Total count computed in the same transaction** — use a `count()` subquery over the same base (case-set selection) rather than a separate round-trip, to avoid skew between the list and the count.
- **Auth / role**: unchanged. Reads require the existing project-scoped permission.

---

## Tasks

### Implementation
- [x] Confirm plan 032 (`no_run` rename + compat window) is merged before starting — `TestResultResponse.status` should already serialise `no_run` correctly
- [x] Audit `TestCaseWithResult`: for each declared field, confirm the service populates it; add `automation_id`; confirm `tags` serialised
- [x] Decide on `status` → `case_status` rename; if accepted, add `case_status` alongside `status` with a deprecation comment in the schema
- [x] Extend `get_with_cases` signature: `(db, run_id, *, limit=500, offset=0, sort="suite_id,id") -> {run, cases, total}`
- [x] Apply `order_by` based on `sort`; reject unknown values with 400
- [x] Apply `limit` / `offset`; enforce `limit <= 2000` (clamp or 400)
- [x] Compute `total` from a parallel `SELECT COUNT(*)` over the same case-set
- [x] Update router to accept and forward `limit`, `offset`, `sort`; update `response_model` if a new wrapper schema is introduced; otherwise extend `TestRunWithCases` with `total: int`
- [x] Update OpenAPI docstring / Pydantic field descriptions so the generated `/docs` makes the params discoverable
- [x] Unit tests:
  - `total` matches `cases.length` when the run fits under the default limit
  - `total > len(cases)` when `offset > 0` or limit is exceeded
  - Run with no results: every `cases[i].result is None`
  - Explicit-selection path and suite-derived path both honour pagination
  - Sort param: `(suite_id, id)` default; `id` alone; invalid → 400
- [x] Integration tests:
  - GET with no params returns `total`, `cases`, `run`
  - GET with `limit=10&offset=10` returns `cases[10:20]` in the expected sort
  - `case_status` present and equals the case's own status
  - `automation_id` present when set on the case
  - 403 for a user without project access (unchanged behaviour — just confirm)
- [x] Smoke test from the running api against a seeded run with 600 cases; verify pagination and total

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — add `limit`, `offset`, `sort` params; document new `total` field; note the deprecation of `status` in favour of `case_status` (if renamed)
- [x] `docs/06-generated/db-schema.md` — no change expected; verify
- [x] `docs/01-product/features/` — if the feature file for test runs enumerates payload contracts, update; otherwise skip
- [x] `docs/02-architecture/backend/api-layer.md` — update the list-endpoint conventions section if this plan changes the pagination pattern for the project
- [x] `docs/08-decisions/changelog.md` — record: chose offset-based pagination; optional rename of `status` → `case_status`; no backend synthesis of `no_run` rows (matches web plan 055 rationale)
- [x] `docs/04-execution/tech-debt.md` — add: (a) server-side `?status=` filter for this endpoint, (b) remove the deprecated `status` field after one release, (c) cursor-based pagination if case counts grow
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Existing consumers read the top-level response as `{run, cases}` and break on the added `total` | Low | Additive field; JSON parsers ignore unknown keys; but note in changelog |
| `case_status` rename breaks a consumer that reads `cases[i].status` | Medium | Emit both fields for one release; changelog entry "breaking in release X+2"; web plan 055 uses `case_status` from day one |
| `limit=2000` produces slow queries on large projects | Medium | Test on prod-like data; if slow, reduce cap or add an index on `(suite_id, id)` for `TestCase` (likely already covered by PK + fk) |
| `total` count adds a second SQL round-trip that inflates p95 latency | Low | Single connection, parallel subquery; if measurable, switch to a window-function `count(*) OVER ()` on the main query |
| Sort param `?sort=invalid` returns 500 instead of 400 | Low | Explicit allow-list in the service; unit tested |
| A case in an explicit selection has been soft-deleted since selection; the endpoint silently hides it | Low | Existing behaviour; document as a known gap in the endpoint doc; out of scope to fix here |
| Web plan 055 ships before this plan and falls back to the 500 cap | Low | 055 explicitly notes "accept 500 as current upper bound and log follow-up" — the two plans are independently shippable |

---

## Definition of done

- [x] Endpoint accepts `limit`, `offset`, `sort` query params; returns `{run, cases, total}`
- [x] Every field declared on `TestCaseWithResult` is populated by the service; `automation_id` and `tags` verified
- [x] Default sort is `(suite_id, id)`; invalid sort → 400
- [x] `limit` is clamped to `<= 2000`; default stays at 500 for back-compat
- [x] `total` reflects the full case-set, not the paginated slice
- [x] If adopted, `case_status` emitted alongside `status` with a deprecation note
- [x] Unit + integration tests cover pagination, sort, suite-vs-explicit, null-result, and (if renamed) `case_status`
- [x] Works against a seeded run of > 500 cases without truncation
- [x] Auth enforcement unchanged; confirmed by an integration test
- [x] Docs updated
- [x] PR checklist completed
