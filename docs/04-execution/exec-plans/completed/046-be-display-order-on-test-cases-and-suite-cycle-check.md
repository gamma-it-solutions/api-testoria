# Execution Plan: `display_order` on test cases + parent-cycle check on suite re-parent (TES-69 BE side)

**Date**: 2026-05-11
**Author**: gabi
**Status**: Complete

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Make the test-suite tree drag-and-droppable by giving the API the two things it's missing today: (1) an explicit `display_order` column on `test_cases` so cases can be reordered within a suite, mirroring what `test_suites.display_order` already provides for sections, and (2) a cycle check on `PUT /test-suites/{id}` so re-parenting a suite into one of its own descendants is rejected at the API rather than corrupting the tree. Web plan-093 lands the drag-and-drop UX on top of this.

Linear: [TES-69](https://linear.app/testoria/issue/TES-69/drag-and-drop-non-functional-in-test-suite-tree) — Bug, Medium. Parent: TES-68. Ref: Alex's POC review 2026-04-28 — BUG-005.

---

## Context

Today, the frontend tree (cases-list view and the run-creation wizard's tree selector) has no drag-and-drop wiring. Surveying the backing API:

- `test_suites.display_order` (`Integer | None`) already exists (migration `e9f0a1b2c3d5`). `PUT /test-suites/{suite_id}` accepts `display_order` and `parent_suite_id` updates. `list_suites` orders by `(display_order NULLS LAST, created_at, id)` — stable and frontend-friendly.
- `test_cases` has **no** ordering column. `list_test_cases` orders by `created_at DESC`. There is no way to express "case B comes before case A in this suite" today, so reorder-within-suite would be lossy even if the frontend were to wire it.
- `update_suite` blocks `parent_suite_id == self.id` but does **not** block setting parent to one of the suite's own descendants. That would create an unreachable cycle in the `parent_suite_id` graph and break the recursive cascade CTE in plan-045's soft-delete. A defensive check belongs at the API.

The web plan (plan-093) will:
1. Mutate `test_suites.display_order` (already supported — no change needed) and `test_suites.parent_suite_id` (already supported) on suite drops.
2. Mutate `test_cases.display_order` (this plan) and `test_cases.suite_id` (already supported) on case drops.

This plan unblocks #2 and hardens #1 against tree corruption.

---

## Scope

### In scope

- Add a nullable `display_order: int | None` column to `test_cases`.
- Alembic migration with both `upgrade` (add column) and `downgrade` (drop column). Backfill is **not** required — `NULL` sorts last and existing case order is preserved by the secondary `(created_at, id)` key.
- Extend `TestCaseCreate`, `TestCaseUpdate`, `TestCaseResponse` Pydantic schemas with `display_order: int | None`.
- `create_test_case`: persist `display_order` when supplied.
- `update_test_case`: persist `display_order` when present in `model_fields_set` (mirrors how `update_suite` handles its `display_order`).
- Replace `order_by(TestCase.created_at.desc())` in `list_test_cases` with `(display_order NULLS LAST asc, created_at asc, id asc)` — same shape as `apply_suite_order`. Extract into a shared `apply_case_order` helper.
- `update_suite`: when `parent_suite_id` is in `model_fields_set` and non-null, walk descendant ids (reuse `_descendant_suite_ids` already in this file) and reject with `BadRequestError("Re-parenting would create a cycle")` if the requested parent is in that set.
- Tests:
  - Unit: a case created with `display_order=10` and another with `display_order=20` are returned in that order from `list_test_cases`. `NULL`-ordered cases sort last among siblings. `PUT /test-cases/{id}` with `display_order=0` is reflected on the next list.
  - Unit: `update_suite` raises `BadRequestError` when re-parenting suite A to one of A's descendants. The original parent is unchanged.
  - Integration: `PATCH`-style updates surface as expected over HTTP.

### Out of scope

- A bulk `POST /test-cases/reorder` or `POST /test-suites/reorder` endpoint. The single-PUT path is sufficient with gap-based ordering on the client; bulk reorder is a perf optimisation, not a correctness gap. Track as tech debt if the client ends up firing many PUTs in tight succession.
- Rebalancing display orders when the gap between siblings becomes too small (e.g. drop-between hitting fractional integer collisions). The client uses gap-based ordering (`(prev + next) // 2`) which collapses to `prev + 1` worst case; a rebalance routine is a follow-up if the gap ever reaches zero on a real workload.
- Auto-assigning `display_order` on create when not supplied. Existing behaviour (NULL → sorts last) keeps the create flow unchanged for the FE bulk-import path.
- WebSocket events for reorder. The current Centrifugo `publish_case_update("updated")` already fires on any `PUT /test-cases/{id}`, including a pure `display_order` change, so reorder is broadcast for free.

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| models | `app/models/test_case.py` | New `display_order: Mapped[int | None]` column |
| migration | `alembic/versions/f0a1b2c3d4e5_add_display_order_to_test_cases.py` | `upgrade` adds column nullable; `downgrade` drops it. Mirror `e9f0a1b2c3d5_add_display_order_to_test_suites.py` |
| schemas | `app/schemas/test_case.py` | Add `display_order: int | None = None` to `TestCaseCreate`, `TestCaseUpdate`, `TestCaseResponse` |
| service | `app/services/test_case_service.py` | `apply_case_order` helper; `list_test_cases` uses it; `create_test_case` persists `display_order`; `update_test_case` persists `display_order` via `model_fields_set` check |
| service | `app/services/test_suite_service.py` | `update_suite`: cycle check when `parent_suite_id` is in `model_fields_set` and non-null — walk descendants via `_descendant_suite_ids` and reject if the new parent is in the set |
| tests | `tests/unit/test_test_case_service.py` | Order is stable; PUT updates order; NULL sorts last |
| tests | `tests/unit/test_test_suite_service.py` | Cycle check raises `BadRequestError`; parent unchanged |
| tests | `tests/integration/test_test_cases_api.py` | PUT with `display_order` reflected in list |
| tests | `tests/integration/test_test_suites_api.py` | PUT with cyclic parent returns 400 |

### Key decisions

- **Reuse the `display_order` shape from suites.** `Integer | None`, `NULLS LAST`. Frontend gap-based math expects monotonic integers; making `NULL` sort last preserves existing create flows without a backfill step.
- **No backfill.** Cases that pre-date this migration retain `display_order = NULL` and sort by `(created_at, id)` — the same as today's `created_at DESC` for the sibling case but ascending so that the oldest-first frontend tree is consistent with how suites already render. The first reorder action on a sibling group materialises explicit values for the cases touched by the drop.
- **Single-PUT, gap-based reorder.** No bulk endpoint. Client computes `new_order = (prev + next) // 2` and PUTs just the moved case. Integer-only — small chance of collapse to `prev + 1` after many bisects, accepted as a known tech-debt item with a "rebalance helper" follow-up.
- **Cycle check uses the existing `_descendant_suite_ids` CTE.** Plan-045 already shipped that helper for cascade soft-delete; reusing it keeps the cycle predicate in one place and means the same Postgres recursion is the canonical "is this suite under that one" answer.
- **Reject, don't normalise.** A cyclic re-parent is a client bug, not user-recoverable state. Returning `400` is the right contract; silently dropping the change would leave the client UI inconsistent with the server.
- **`display_order` on `TestCaseResponse`** so the frontend can read the server's authoritative value back after a PUT and avoid drift between optimistic UI and DB state.

---

## Tasks

### Implementation
- [x] `app/models/test_case.py` — add `display_order: Mapped[int | None] = mapped_column(Integer, nullable=True)` next to other columns
- [x] Create Alembic migration `f0a1b2c3d4e5_add_display_order_to_test_cases.py` (down_revision = `e9f0a1b2c3d5`)
- [x] Apply migration: `alembic upgrade head`
- [x] `app/schemas/test_case.py` — add `display_order: int | None = None` to Create, Update, Response
- [x] `app/services/test_case_service.py` — add `apply_case_order` helper, use it in `list_test_cases`; persist `display_order` in `create_test_case` and `update_test_case`
- [x] `app/services/test_suite_service.py` — cycle check in `update_suite` using `_descendant_suite_ids`

### Tests
- [x] `tests/unit/test_test_case_service.py` — order/PUT/NULL-last cases
- [x] `tests/unit/test_test_suite_service.py` — cycle check raises
- [x] `tests/integration/test_test_cases_api.py` — PUT `display_order` reflected
- [x] `tests/integration/test_test_suites_api.py` — cyclic parent → 400

### Quality check
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — note `display_order` in case payloads (no new endpoints)
- [x] `docs/06-generated/db-schema.md` — add `display_order` row to `test_cases`
- [x] `docs/08-decisions/changelog.md` — plan-046 entry
- [x] `docs/04-execution/tech-debt.md` — add "Display-order rebalance helper" follow-up
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Existing FE list calls break if response shape change is incompatible | Very low | The field is optional in the response schema; clients that don't read it are unaffected |
| Migration locks `test_cases` on a large prod table | Low | `ADD COLUMN ... NULL` is metadata-only on Postgres ≥ 11 — no rewrite |
| Cycle check is too strict and rejects legitimate moves to a sibling | Very low | The descendant set is computed top-down from `self.id`; siblings are not descendants and pass through |
| Gap-based ordering collapses on many bisects | Low | Tracked as tech debt; rebalance helper is a follow-up if it ever bites |

---

## Definition of done

- [x] `display_order` exists on `test_cases` and round-trips through Create / Update / Response
- [x] `list_test_cases` orders by `(display_order NULLS LAST, created_at, id)`
- [x] `PUT /test-suites/{id}` returns 400 when `parent_suite_id` is a descendant of the suite
- [x] All unit + integration tests pass
- [x] Migration applies and rolls back cleanly
- [x] Docs updated
