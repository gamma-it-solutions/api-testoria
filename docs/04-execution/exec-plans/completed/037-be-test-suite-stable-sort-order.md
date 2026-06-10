# Execution Plan: Stable, single-source sort order for test suites (tree + flat)

**Date**: 2026-04-20
**Author**:
**Status**: Completed

---

## Goal

Make every endpoint that returns test suites — the flat `GET /test-suites`, the tree projection used by the web suite panel, the nested suites carried by `TestRunSuiteTree` (api plan 034) — return them in the **same, stable, documented order**. Today the flat list sorts by `created_at ASC`, the web tree side-panel inherits that order, and the cases-panel on the web sorts alphabetically — a visible inconsistency. Backend picks one order, all endpoints honour it, the web can mirror it without its own sort.

---

## Context

`GET /test-suites` currently sorts by `created_at ASC` (`app/services/test_suite_service.py:42`):

```py
result = await db.execute(query.order_by(TestSuite.created_at.asc()))
```

- No tie-breaker — suites created in the same millisecond (seed scripts, bulk imports) can swap order between requests.
- Tree projections derived from this list inherit the order implicitly, but that invariant is documented nowhere.
- No server-side knob for user-driven ordering — if the product wants drag-and-drop later, there's no column to persist a position.

The web consumer (`TestCaseTreeView.vue`) re-sorts by `name` for its "cases-panel" section (`visibleSuites` computed, line 169), which is why `/projects/:id/test-cases` shows the aside in one order and the main panel in another. Web plan 061 fixes the consumer, but the underlying contract should be crisp so this kind of drift doesn't recur in the next consumer.

### Two shapes of fix

1. **Minimal** — document and stabilise today's `created_at ASC` with an explicit `(created_at ASC, id ASC)` tiebreak across all endpoints, add integration tests asserting the order. No schema change.
2. **Future-proof** — add a nullable `display_order: int | None` column, sort by `(display_order NULLS LAST, created_at ASC, id ASC)`, and surface `display_order` on the response schema so a future drag-and-drop plan has a clear hook. Old data carries `NULL` and falls back to created-time order — no behaviour change on existing records.

This plan adopts shape 2 — the migration is tiny, the field is additive, and it saves a future plan from re-opening the same files.

---

## Scope

### In scope

- Add a nullable `display_order: Integer` column on `test_suites` (no default, no backfill; existing rows stay `NULL`)
- Alembic migration for the new column; a corresponding downgrade
- Update every suite query site to sort by `(display_order NULLS LAST, created_at ASC, id ASC)` — introduce a `SUITE_ORDER` constant or a helper `apply_suite_order(query)` to prevent drift:
  - `test_suite_service.list_suites` (flat)
  - Any tree-building code path (recursive CTE / closure fetch) introduced by api plan 034 for `TestRunSuiteTree`
  - Any projection that returns nested `children: list[TestSuite]` — children sorted by the same key
- Surface `display_order: int | None` on `TestSuiteResponse`
- Unit tests:
  - Order stable across runs on the same dataset (repeat-fetch test)
  - `NULL display_order` rows fall back to `created_at`
  - Mixed `NULL` + non-`NULL` values: explicit ordering wins, nulls go last
- Integration tests: flat list and tree projection return suites in the same order for the same project
- Docs: `endpoints.md` notes the sort order contract; `db-schema.md` gains the new column

### Out of scope

- Drag-and-drop UI or any `PATCH` endpoint to reorder suites — logged as a dedicated follow-up plan
- Backfilling `display_order` for existing data — stays `NULL`; the fallback order is already what users see today
- Sorting test cases within a suite — separate concern (plan 033 touches this for runs, not for the catalogue view)
- Changes to `GET /test-runs/{id}/cases?group_by=suite` beyond conforming to the new order helper (api plan 034's scope covers the tree projection; this plan ensures both projections use the same sort helper)
- Locale-sensitive sort by `name` — rejected here; if product wants alphabetical, it becomes an explicit `sort` query param on the list endpoint (follow-up)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| models | `app/models/test_suite.py` | Add `display_order: Mapped[int \| None] = mapped_column(Integer, nullable=True)` |
| migration | `alembic/versions/…_add_test_suite_display_order.py` | `op.add_column('test_suites', sa.Column('display_order', sa.Integer, nullable=True))`; downgrade drops it |
| util | `app/services/test_suite_service.py` (or `app/utils/ordering.py`) | `apply_suite_order(query)` helper: appends `order_by(TestSuite.display_order.asc().nulls_last(), TestSuite.created_at.asc(), TestSuite.id.asc())` |
| services | `app/services/test_suite_service.py` | `list_suites`, any tree/children builder — call the helper |
| services | `app/services/test_run_service.py` | Where api plan 034 builds `TestRunSuiteTree`, sort children via the same helper (or sort the loaded list before linking children) |
| schemas | `app/schemas/test_suite.py` | Add `display_order: int \| None = None` to `TestSuiteResponse`; also expose on the nested suite representation carried by `TestRunSuiteNode` |
| tests | `tests/unit/test_test_suite_service.py` | Ordering tests (stable, nulls-last, explicit-wins) |
| tests | `tests/integration/test_test_suites_api.py` | Flat list order assertion; repeat-call stability |
| tests | `tests/integration/test_test_runs_api.py` | Grouped-by-suite tree order matches flat list order |
| docs | `docs/06-generated/endpoints.md`, `docs/06-generated/db-schema.md` | Document the sort contract and the new column |

### Key decisions

- **One ordering helper, used everywhere.** `apply_suite_order(query)` is the single source. Every suite query goes through it; new endpoints opt in automatically.
- **`display_order` is nullable and unset by default.** No backfill, no implicit value. A future reorder UI writes integers; until then, the fallback (`created_at`, `id`) produces today's behaviour.
- **`NULLS LAST` on `display_order`.** Rows explicitly ordered appear first; unordered (legacy / not-yet-touched) fall to the tail of the parent. Intuitive for a drag-and-drop UI that only touches what the user moves.
- **`id` as final tiebreaker.** Deterministic across identical `created_at` timestamps (common after seed scripts).
- **No new sort param on the list endpoint.** Callers that want alphabetic or other orders can sort client-side (minor) or request a future `?sort=` param (separate plan). Keeping the endpoint's contract single-valued makes consumer logic simpler.
- **Postgres-specific `NULLS LAST`** — the project already targets Postgres; SQLAlchemy's `.nulls_last()` emits the correct SQL. Document the dependency in the service-layer doc.
- **Tree projection sorts children with the same key.** A future recursive-CTE approach is fine; the sort applies to each sibling set. No cross-level sort.

---

## Tasks

### Implementation
- [x] Add `display_order` column on `TestSuite` model; default `None`; nullable
- [x] Alembic autogenerate; review the migration (sanity-check the default and null clause); manual tweak if needed; apply with `alembic upgrade head` on a scratch DB
- [x] Add `apply_suite_order(query)` helper (or `SUITE_ORDER_BY` list of clauses)
- [x] Switch every suite query site to the helper
- [x] Update `TestSuiteResponse` to expose `display_order: int | None`
- [x] If api plan 034 is already in: ensure its tree builder sorts siblings via the helper; otherwise note the dependency in its plan
- [x] Unit tests:
  - [x] Two suites with equal `created_at` return a deterministic order (by `id`)
  - [x] Mixed `display_order = 1, None, None, 2` returns `[1, 2, then null-order by created_at+id]`
  - [x] All-null: falls back to `created_at ASC, id ASC`
  - [x] Repeat-call returns identical order on identical data
- [x] Integration tests:
  - [x] `GET /test-suites` returns suites in the documented order
  - [x] `GET /test-runs/{id}/cases?group_by=suite` children at each level match flat-list order for the same suites
  - [x] Unknown `display_order = 0` is treated as explicit (sorts before nulls) — document
- [x] Smoke against a seeded project: confirm the order is what we expect before merging

### Quality check
- [x] `pytest`
- [x] `ruff check app tests`
- [x] `mypy app`
- [x] `alembic upgrade head` + `alembic downgrade -1` + `alembic upgrade head` round-trip cleanly
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/db-schema.md` — `test_suites.display_order` documented (nullable int; no default; used for user-driven sort)
- [x] `docs/06-generated/endpoints.md` — suite list + grouped run tree documented with the sort contract
- [x] `docs/02-architecture/backend/service-layer.md` — document `apply_suite_order` as the canonical helper; note Postgres `NULLS LAST` dependency
- [x] `docs/08-decisions/changelog.md` — record: added `display_order` (nullable); unified suite sort to `(display_order NULLS LAST, created_at, id)`; no backfill
- [x] `docs/04-execution/tech-debt.md` — log follow-ups: (a) drag-and-drop reorder endpoint + UI, (b) optional `?sort=` param on list endpoint for alphabetic mode
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `NULLS LAST` not supported on the target DB dialect | Low | Project targets Postgres; assert in a service-layer doc; tests use the real DB |
| A suite query site is missed and keeps sorting by something else | Medium | Helper-only approach; grep for `order_by(TestSuite` and replace; add a unit test asserting list order against a stub that omits the helper |
| Api plan 034's tree children bypass the helper | Medium | Cross-link both plans; if 034 merged first, verify it uses the helper or adjust it; integration test asserts flat and tree match |
| `display_order = 0` confused with `NULL` | Low | Explicit test: `0` is treated as an explicit order (appears before nulls); documented |
| Clients (CLI, integrations) rely on a different order | Low | Today's default was never documented as a contract; any reliance was accidental; release note in changelog |
| Migration cost on a table with many rows | Low | Adding a nullable column is O(1) on Postgres; no backfill |

---

## Definition of done

- [x] `display_order` column exists on `test_suites`; migration applied
- [x] Every suite query uses `apply_suite_order` (or equivalent named list)
- [x] `GET /test-suites` returns suites in `(display_order NULLS LAST, created_at ASC, id ASC)` order, deterministic across repeat calls
- [x] `GET /test-runs/{id}/cases?group_by=suite` nests children in the same order
- [x] `TestSuiteResponse.display_order` surfaced on responses
- [x] Unit + integration tests cover ordering edge cases
- [x] Docs updated; changelog explains the stabilised contract
- [x] Alembic up / down round-trip verified
- [x] PR checklist completed
