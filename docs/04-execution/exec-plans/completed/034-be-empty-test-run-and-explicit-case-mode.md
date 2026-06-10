# Execution Plan: Allow creating an empty test run; make "explicit case selection" a first-class state

**Date**: 2026-04-20
**Author**:
**Status**: Completed

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Let the API represent a test run that has an explicit, empty case list — distinct from a run that derives its cases from `suite_id` / the project — so the frontend can create a run with zero cases and let the user add them later through the existing `PUT /test-runs/{run_id}/cases` endpoint.

---

## Context

Today the backend supports two case-selection modes, but only implicitly:

- **Auto-derived**: no rows in `test_run_test_cases`; `get_with_cases` walks `suite_id` / project to enumerate cases (`app/services/test_run_service.py:300-314`).
- **Explicit**: rows exist in `test_run_test_cases`; `get_with_cases` joins against them (`:282-298`).

The mode is inferred by row count (`_has_explicit_cases(run_id)` → `SELECT count(*) … > 0`). That inference collapses two different states into one:

1. "User didn't configure explicit cases" → expect derivation.
2. "User explicitly set an empty case list" → expect an empty run.

Both produce zero junction rows, so the user's empty selection is silently flipped back into derivation mode by the next `get_with_cases` call. An "empty test run" feature needs a way to distinguish them.

The web plan (058) asks for:
- Create a run with no cases ("create empty")
- Edit cases later via the existing `PUT /test-runs/{run_id}/cases`, reusing the same suite-tree selector the create wizard uses

Plans 054 / 032 / 055 / 033 / 056 / 057 are orthogonal and land independently.

---

## Scope

### In scope
- Add a column on `test_runs`: `cases_mode: ENUM('auto','explicit')` (or `VARCHAR` with a `Literal` check), default `'auto'`
  - Auto → existing derivation behaviour
  - Explicit → use the junction table verbatim, even if empty
- Alembic migration:
  - Add the column with default `'auto'`
  - Backfill: any run that currently has ≥ 1 row in `test_run_test_cases` → `'explicit'`; otherwise `'auto'`
  - Reversible downgrade
- `TestRunCreate` schema: keep `include_test_cases: list[int] | None`
  - `None` → mode stays `auto`
  - `[]` → mode becomes `explicit` (empty list)
  - `[1, 2, 3]` → mode becomes `explicit` (with rows)
- `create_run` service: set `cases_mode` from the rule above; persist junction rows when list is non-empty
- `set_run_cases` service: always flips mode to `'explicit'` on call (including when the incoming list is empty)
- `get_with_cases` service: branch on `cases_mode` (not on row count): `'explicit'` → join the junction table (returns empty list if no rows); `'auto'` → derive from `suite_id`/project
- `get_progress` service: same branch — when mode is explicit, `total` equals the junction row count (can be 0); when auto, count derives from suite/project
- `TestRunResponse` exposes `cases_mode` so the UI can label the run and decide whether to show a "switch to manual selection" hint
- An **optional** endpoint `POST /test-runs/{run_id}/cases-mode` to flip between `auto` and `explicit` without sending a case list (or fold this into `PUT /test-runs/{run_id}`) — **decision below**, prefer the simpler PUT route
- Unit tests:
  - Create with `include_test_cases=None` → mode `auto`
  - Create with `include_test_cases=[]` → mode `explicit`, junction empty
  - Create with `include_test_cases=[1,2]` → mode `explicit`, 2 junction rows
  - `set_run_cases(run, [])` flips mode to `explicit`, no junction rows
  - `get_with_cases(run)` returns [] when mode is `explicit` and no junction rows
  - `get_with_cases(run)` returns derived cases when mode is `auto`
  - Progress counts behave accordingly
- Integration tests:
  - `POST /projects/{pid}/test-runs` with empty `include_test_cases` → 201, GET returns `cases_mode: "explicit"`, `/cases` returns `{cases: [], total: 0}`
  - `PUT /test-runs/{id}/cases` with `test_case_ids: []` on an auto run → flips mode to `explicit`
  - Backward-compat: existing runs (pre-migration) still return correct case lists post-deploy

### Out of scope
- Deleting the junction table or redesigning the storage model
- Letting a user switch back from `explicit` to `auto` via an auto-flag on the run (UI can achieve the effect by clearing `suite_id` or re-sending a full derivation request) — logged as tech debt if requested
- Tag-based or query-based case selection (separate feature request)
- Changing permissions on the create/update/cases endpoints
- Changes to the `GET /test-runs/{run_id}/cases` pagination / field completeness covered by plan 033 — that plan stacks on top of this one's branching
- Frontend changes — covered by web plan 058

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| model | `app/models/test_run.py` | Add `cases_mode: Mapped[str]` with `server_default='auto'`, nullable=False; add a SA `CheckConstraint` on the column restricting to `'auto' \| 'explicit'` |
| migration | `alembic/versions/YYYYMMDD_add_cases_mode_to_test_runs.py` | Add column with default `'auto'`; backfill: `UPDATE test_runs SET cases_mode='explicit' WHERE id IN (SELECT test_run_id FROM test_run_test_cases)`; add the check constraint; downgrade drops column |
| schemas | `app/schemas/test_run.py` | Add `cases_mode: Literal["auto","explicit"]` to `TestRunResponse`; no field change on `TestRunCreate` / `TestRunUpdate` — inferred from `include_test_cases` on create and from the `set_run_cases` call on update |
| services | `app/services/test_run_service.py` | `create_run`: set `cases_mode='explicit'` when `include_test_cases is not None` (even empty), else `'auto'`; `set_run_cases`: always set `cases_mode='explicit'`; `get_with_cases` + `get_progress`: branch on `run.cases_mode` instead of the row-count helper; **remove** `_has_explicit_cases` (replaced by the explicit column) |
| tests | `tests/unit/test_test_run_service.py` | Every path listed in Scope |
| tests | `tests/integration/test_test_runs_api.py` | Integration coverage for the new mode |

### Key decisions

- **Explicit enum, not a boolean**. `has_explicit_cases: bool` would work today, but an enum leaves room for future modes (`tag_filter`, `saved_query`, etc.) without another migration. Cheap to add now, harder to add later.
- **Infer mode from `include_test_cases`, not from a separate field on `TestRunCreate`**. Adding `cases_mode` to the create payload is redundant: `include_test_cases=[]` already says "explicit and empty". Letting the API infer keeps the request shape compatible with existing clients (which pass `None` and get `auto`).
- **`set_run_cases` always flips to `explicit`**. Calling the endpoint is a user action that declares "I'm choosing the cases manually", regardless of how many. An empty list then means "I have chosen zero".
- **No new endpoint for mode flipping**. A run is auto until the first `set_run_cases` call; after that it's explicit forever (unless the frontend adds a "reset to derived" affordance later — logged as tech debt). This avoids proliferating tiny endpoints.
- **Branching lives in one helper**. Replace `_has_explicit_cases` with a single query path switch inside `get_with_cases` / `get_progress` that reads `run.cases_mode` once. Makes the invariant hard to accidentally invert.
- **Backfill is authoritative**. Pre-migration runs with zero junction rows **become `auto`** — matching the historical behaviour. Runs with any rows become `explicit`. No user-visible change on existing runs.
- **DB default via `server_default`**. Ensures rows inserted by other code paths (seed scripts, fixtures) always have a value; Pydantic default is a backup.
- **Check constraint**. Adds a small correctness guard at the DB level against a typo (`'explict'` etc.). Low cost.

---

## Tasks

### Implementation
- [x] Add `cases_mode` column on `TestRun` model with `server_default='auto'`; add SA `CheckConstraint`
- [x] Generate Alembic revision; edit to include the backfill `UPDATE` and verify reversible downgrade
- [x] Apply migration on a local copy and spot-check:
  - A run with existing cases is `explicit`
  - A run with no explicit rows is `auto`
- [x] Update `TestRunResponse` to include `cases_mode`
- [x] Update `create_run`: infer `cases_mode` from `data.include_test_cases is not None`
- [x] Update `set_run_cases`: set `run.cases_mode = 'explicit'` unconditionally; keep the DELETE-then-INSERT pattern
- [x] Replace `_has_explicit_cases` call sites (`get_with_cases`, `get_progress`) with `run.cases_mode == 'explicit'`
- [x] Remove `_has_explicit_cases` helper
- [x] Unit tests (all bullets in Scope)
- [x] Integration tests (all bullets in Scope)
- [x] Manual: call `POST /projects/{pid}/test-runs { "name": "empty", "include_test_cases": [] }`, then `GET /test-runs/{id}/cases` → `{cases: [], total: 0}`; then `PUT /test-runs/{id}/cases { "test_case_ids": [1,2] }` → GET returns the 2 cases
- [x] Manual: call create with `include_test_cases: null` → `cases_mode: "auto"`; verify `GET /cases` returns derived cases

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — document the new `cases_mode` field on `TestRunResponse`; update the create endpoint description ("empty `include_test_cases` yields an explicit empty selection")
- [x] `docs/06-generated/db-schema.md` — add the new column and constraint
- [x] `docs/01-product/features/<test-run feature>.md` — describe the "empty run" flow and the `auto` vs `explicit` distinction
- [x] `docs/02-architecture/ARCHITECTURE.md` — if the codemap documents the inference rule, update to cite the new column
- [x] `docs/08-decisions/changelog.md` — record: enum over boolean; no mode-flipping endpoint; backfill rule
- [x] `docs/04-execution/tech-debt.md` — log (a) "reset to auto" affordance if product asks, (b) future `tag_filter` / `saved_query` modes
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Migration backfill is slow on a large `test_runs` × `test_run_test_cases` | Low | Single `UPDATE … IN (SELECT …)` query; acceptable at expected scale; off-hours run |
| A client that hard-codes "no rows in junction = derive" breaks after the mode switch | Medium | The CLI / any other consumer reads through the API, not the DB — inference lives only in the service, which is the one this plan updates |
| Pre-existing runs with `suite_id=NULL`, no junction rows, and no derivation produce empty `get_with_cases` results | Low | Already the behaviour today; post-migration they're `auto` and `get_with_cases` still derives from the project (current logic) — no regression |
| `set_run_cases([])` silently wipes cases in an `auto` run that a client only wanted to "clear explicit" | Low | Existing behaviour — the endpoint has always done DELETE + INSERT; flipping mode to explicit matches user intent; changelog calls this out |
| `cases_mode` added to `TestRunResponse` breaks a client using strict schemas | Low | Additive field; clients that ignore unknown keys are unaffected; changelog flags it |
| The removed `_has_explicit_cases` helper is used somewhere outside the service layer | Low | `grep` before removal; keep an internal no-op alias for one release if needed |
| Migration check constraint fails in staging because of a NULL row | Low | `server_default='auto'` + NOT NULL column; add `NOT NULL` in the same migration |

---

## Definition of done

- [x] `test_runs.cases_mode` column exists, NOT NULL, defaults to `'auto'`, constrained to `'auto' \| 'explicit'`
- [x] Creating a run with `include_test_cases=[]` yields `cases_mode='explicit'` and an empty `/cases` response
- [x] Creating a run with `include_test_cases=None` yields `cases_mode='auto'` and a derived `/cases` response
- [x] `PUT /test-runs/{id}/cases` flips `cases_mode` to `'explicit'` regardless of list length
- [x] `get_with_cases` and `get_progress` branch on the column, not on junction-row count
- [x] Migration backfills existing data correctly and is reversible
- [x] `cases_mode` present on `TestRunResponse`
- [x] Unit + integration coverage ≥ 85% on the changed service paths
- [x] Docs updated
- [x] PR checklist completed
