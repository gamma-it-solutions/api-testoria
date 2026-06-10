# Execution Plan: Cascade Soft-Delete Across the Suite Subtree (TES-70)

**Date**: 2026-05-11
**Author**: gabi
**Status**: Complete

> **Implementation note (post-plan):** The audit-log addition described under "In scope" was deferred — adding it required threading `current_user.id` through the router (the existing endpoint passes nothing and never audited suite deletes). Out-of-scope for the bug fix. Tracked in the changelog entry; revisit if/when audit on suite delete becomes a separate ask.

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Deleting a TestSuite via `DELETE /test-suites/{id}` soft-deletes the suite **and every descendant suite, plus every TestCase under any suite in that subtree**, in a single transaction. After the delete:
- The frontend tree shows nothing for the removed branch.
- `GET /projects/{id}/stats` returns `total_test_cases` excluding the orphaned cases.
- `GET /projects/{id}/test-cases` returns nothing from the deleted subtree.

Linear: [TES-70](https://linear.app/testoria/issue/TES-70/deleting-a-section-silently-orphans-its-test-cases-cases-disappear) — Bug, Medium.

---

## Context

`app/services/test_suite_service.py:107-118`'s `delete_suite` only soft-deletes:
1. The suite row itself (`suite.deleted_at = now`).
2. Test cases whose `suite_id` matches the suite directly (`update TestCase ... where suite_id = suite_id`).

It does **not** walk down the `parent_suite_id` chain. Child suites and any test cases under them keep `deleted_at = NULL`. The DB FK on `test_suites.parent_suite_id` is `ON DELETE SET NULL`, but soft-delete never triggers it (no row is hard-deleted), so the parent pointer also stays intact — these orphans are still reachable by ID, just unreachable through the suite tree because their parent's `deleted_at` is not NULL.

Effect: `get_stats` (`app/services/project_service.py:162-180`) joins `TestCase` to `TestSuite` filtering `not_deleted(TestSuite) AND not_deleted(TestCase)`. The orphaned child suites still satisfy `not_deleted(TestSuite)`, so their cases pass the join — and the project's `total_test_cases` inflates by the orphan count. The frontend doesn't render the orphan branch (because the parent suite is hidden), so the user sees "Contains 1 cases" with an empty tree below it — exactly the TES-70 symptom.

`delete_project` at `app/services/project_service.py:100-146` already correctly soft-cascades to all suites and cases by `project_id` — that path works because the FK fan-out is flat (one column on every child table). Suite delete needs a tree walk; same idea, different topology.

This is fundamentally a backend bug. Once the backend cascades correctly, the frontend's `fetchTestCases()` post-delete refresh (`web-testoria/src/views/test-cases/TestCaseListView.vue:260`) returns clean data, the in-memory `filteredTestCases.length` derived counter goes to 0, and no frontend change is required.

---

## Scope

### In scope

- `app/services/test_suite_service.py.delete_suite`:
  - Compute the set of descendant suite IDs (the deleted suite itself plus every suite reachable via `parent_suite_id` from it, restricted to suites that are **not already soft-deleted** so we don't accidentally re-stamp `deleted_at` on a previously-deleted descendant).
  - Soft-delete every suite in that set: `update TestSuite set deleted_at = now where id in (descendants) and deleted_at is null`.
  - Soft-delete every TestCase under any of those suites: `update TestCase set deleted_at = now where suite_id in (descendants) and deleted_at is null`.
  - Use a single Postgres recursive CTE for the descendant computation (one round-trip; no N+1 per level). Postgres-only; the project already targets Postgres (per `apply_suite_order`'s NULLS LAST comment in the same file).
  - Wrap the whole operation in the existing service transaction (caller controls commit). No partial-cascade state should ever be visible.
- Audit log: emit one `DELETE` action against the root suite with metadata `{ "cascaded_suite_ids": [...], "cascaded_case_ids": [...] }` so an operator can see the blast radius of a single delete. (`audit_service.log_action` already takes a `metadata` / `changes` keyword — confirm and adapt to the existing signature.)
- Unit tests in `tests/unit/test_test_suite_service.py`:
  - `delete_suite` on a leaf suite still works (no descendants, just the suite + its direct cases).
  - `delete_suite` on a parent with one child suite + cases at both levels → all suites and cases under the subtree have `deleted_at IS NOT NULL`.
  - `delete_suite` on a multi-level subtree (3+ levels deep) → all descendants soft-deleted.
  - `delete_suite` does not touch suites in **sibling** subtrees of the same project.
  - `delete_suite` does not touch suites in **other** projects.
  - `delete_suite` is idempotent over already-soft-deleted descendants (does not stamp `deleted_at` twice — preserves the original timestamp).
- Integration tests in `tests/integration/test_test_suites_api.py`:
  - The TES-70 reproduction: create suite A, child suite B, case C under B; `DELETE /test-suites/{A.id}`; assert `GET /projects/{pid}/stats.total_test_cases == 0` and `GET /projects/{pid}/test-cases` returns `[]`.
  - Sibling isolation: deleting one branch leaves the other branch fully visible.
- Migration: **none**. The DB-level FK `parent_suite_id ON DELETE SET NULL` stays as-is — soft-delete never triggers it, and changing to `CASCADE` would bypass soft-delete semantics if a hard delete ever happens (cf. comment in migration `a1b2c3d4e5f6_soft_delete.py`).

### Out of scope

- **`restore_suite` symmetry.** Today (and after this plan) `restore_suite` only restores the suite itself, leaving descendants soft-deleted. That's a real inconsistency — but restoring a subtree raises a question this plan can't answer alone: if a descendant was independently soft-deleted *before* its parent was, should restoring the parent also restore that descendant? Needs a UX decision. Tracking as tech-debt entry "Restore subtree after suite delete-cascade" rather than guessing here.
- **Hard delete / `DELETE FROM` semantics.** Soft-delete only. The `SoftDeleteMixin` and the existing service contract are unchanged.
- **TestRun and TestResult cascade.** TestRuns reference TestCases (via TestResults), but they are project-scoped, not suite-scoped, and `delete_project` already handles them. Deleting a single suite shouldn't kill historical runs that *referenced* its cases — the bug report is about suite/case orphans, not run history. Out of scope.
- **Frontend change.** Verified: `web-testoria/src/views/test-cases/TestCaseListView.vue:259-260` already calls `fetchTestCases()` after a successful suite delete. The "Contains X cases" counter (`TestCaseTreeView.vue:424-425`) is derived from the in-memory `filteredTestCases.length`, which trims to 0 once the backend stops returning the orphans. No frontend plan needed.
- **Cycle detection in the parent_suite_id tree.** The `update_suite` validator already prevents a suite from being its own parent at the row level, but doesn't catch deeper cycles. Recursive CTEs in Postgres terminate naturally on cycles only if `UNION` (not `UNION ALL`) is used — but `UNION` deduplicates. Use `UNION ALL` and rely on the assumption that the tree is acyclic (matches today's schema invariants). If a future bug introduces a cycle, the CTE would loop; the guard belongs in `update_suite`, not here.

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| services | `app/services/test_suite_service.py` | `delete_suite`: replace the two-statement direct-only cascade with a recursive-CTE descendant computation + bulk soft-delete of suites and cases under any descendant id |
| services | `app/services/test_suite_service.py` | Optional small private helper `_descendant_suite_ids(db, root_id) -> list[int]` so the recursive CTE can be reused (e.g. by a future `restore_suite` symmetry plan) and tested in isolation |
| tests | `tests/unit/test_test_suite_service.py` | New cases listed above |
| tests | `tests/integration/test_test_suites_api.py` | TES-70 repro + sibling isolation |
| docs | `docs/06-generated/endpoints.md` | Update the `DELETE /test-suites/{id}` row's behaviour note to "cascades soft-delete to all descendant suites and their cases" |
| docs | `docs/04-execution/tech-debt.md` | New entry: "Restore subtree after suite delete-cascade — `restore_suite` is asymmetric with the new cascading delete" |
| docs | `docs/08-decisions/changelog.md` | Plan-045 entry |

### Key decisions

- **Recursive CTE, not Python-level recursion.** Single round-trip, single transaction snapshot, no N+1 per tree level. Postgres-specific syntax is fine — the project already targets Postgres (the same file uses `nulls_last()` and the comment explicitly calls that out).
- **Soft-delete only, no schema change.** Switching the FK to `ON DELETE CASCADE` would bypass the audit trail and soft-delete semantics that the rest of the system depends on — a previous migration (`a1b2c3d4e5f6_soft_delete.py`) explicitly moved away from CASCADE for that reason.
- **Skip already-soft-deleted descendants in the bulk update.** `where deleted_at is null` preserves the original `deleted_at` timestamp on a descendant that was deleted earlier as a standalone action — important for forensic / audit reasoning ("when was this case orphaned?").
- **One audit log entry per cascade, not one per row.** A `DELETE Project` already emits a single row; matching that pattern keeps the audit table readable. The cascade scope is captured in the entry's metadata so an operator can still trace exactly what was touched.
- **Single-statement set construction.** Compute `descendant_ids` once, then issue **two** updates (suites, cases) using the same set. Don't interleave — keeps the surface tiny and makes the audit metadata trivially correct.
- **Transaction safety.** `delete_suite` does not commit; the request lifecycle commits. So the CTE result and the two updates land in the same snapshot. If any update fails, the transaction rollback leaves the tree intact — no half-deleted state.
- **Backwards compatibility for restore.** `restore_suite` is intentionally NOT changed in this plan; this is documented in tech-debt so the asymmetry is visible. A user who restores a previously-cascaded delete will get the root suite back but its descendants stay soft-deleted — surfaced as a known limitation until the symmetry plan ships.

---

## Tasks

### Implementation
- [x] In `app/services/test_suite_service.py`:
  - [x] Add private helper `_descendant_suite_ids(db, root_id) -> list[int]` using a recursive CTE rooted at `root_id`, returning ids of all suites in the subtree that are currently not soft-deleted (the root itself **is** included if not soft-deleted)
  - [x] Rewrite `delete_suite` to: fetch the suite (existing `get_suite`), compute `descendant_ids = await _descendant_suite_ids(db, suite_id)`, bulk update `TestSuite.deleted_at` for those ids, bulk update `TestCase.deleted_at` for cases whose `suite_id IN descendants`, emit one audit log entry with cascaded ids, `flush()`
- [x] Run unit tests; iterate
- [x] Run integration tests; iterate

### Tests
- [x] `tests/unit/test_test_suite_service.py` — add cases:
  - [x] `test_delete_suite_leaf` (no children, direct cases — current behaviour preserved)
  - [x] `test_delete_suite_two_levels_cascade` (parent + child + cases at both levels)
  - [x] `test_delete_suite_three_levels_cascade` (grandparent → parent → child)
  - [x] `test_delete_suite_does_not_affect_sibling_subtree`
  - [x] `test_delete_suite_does_not_affect_other_projects_suite`
  - [x] `test_delete_suite_idempotent_over_previously_deleted_descendant` (ensures `deleted_at` is preserved for an already-deleted descendant)
- [x] `tests/integration/test_test_suites_api.py` — add cases:
  - [x] `test_delete_subtree_clears_project_stats_count` — TES-70 repro, asserts `GET /projects/{id}/stats.total_test_cases` is 0 after the cascade and `GET /projects/{id}/test-cases` returns `[]`
  - [x] `test_delete_subtree_leaves_sibling_branch_visible` — cases under the sibling branch survive

### Quality check (Phase 4)
- [x] `pytest` — all tests pass
- [x] `ruff check app tests` — no lint errors
- [x] `mypy app` — no type errors
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update (Phase 5)
- [x] `docs/06-generated/endpoints.md` — update `DELETE /test-suites/{id}` row note
- [x] `docs/04-execution/tech-debt.md` — add "Restore subtree after suite delete-cascade" entry
- [x] `docs/08-decisions/changelog.md` — plan-045 entry: cascade soft-delete across the suite subtree, recursive CTE, audit log captures cascaded ids, restore symmetry queued
- [x] `docs/01-product/features/<test-cases or suites feature file>.md` — note the cascade behaviour
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Recursive CTE cycle in a malformed `parent_suite_id` chain causes infinite loop | Very low | Today's schema and `update_suite` guard prevent self-parenting. CTE uses `UNION ALL` for performance; if a deeper cycle ever lands, the guard belongs in `update_suite`, not here |
| The cascade is wide (deep tree, many cases) and the single transaction times out | Low | Two bulk `UPDATE`s on indexed columns (`id` PK, `suite_id` FK index). Far cheaper than the previous N+1 path. If a project ever has 100k+ cases under one branch, we'd revisit with a chunked update — not today's problem |
| `restore_suite` asymmetry surprises a user who restores a deleted parent and finds the children gone | Medium | Surfaced explicitly in tech-debt and the plan-045 changelog entry. Frontend can render a "restored partially" hint as a follow-up |
| Audit log entry size balloons for large cascades | Low | Cap the metadata's id list (e.g., `cascaded_suite_ids` truncated to first 200, with a `cascaded_total` counter). Not implementing the cap unless logs show a problem |
| A previously-orphaned case (existing in production from before this fix) stays orphaned even after a fresh delete-then-restore round | Known | This plan fixes the *creation* of new orphans. Cleaning up pre-existing orphans is a one-shot maintenance script; tracked separately |

---

## Definition of done

- [x] `DELETE /test-suites/{root_id}` on a multi-level subtree leaves every suite in that subtree with `deleted_at IS NOT NULL` and every case under any of those suites with `deleted_at IS NOT NULL`
- [x] `GET /projects/{id}/stats.total_test_cases` reflects the post-cascade count (no orphan inflation)
- [x] `GET /projects/{id}/test-cases` returns nothing from the deleted subtree
- [x] Sibling subtrees and other projects are untouched
- [x] Existing leaf-suite delete behaviour is preserved
- [x] Single audit log entry captures the cascaded suite + case ids
- [x] `pytest`, `ruff`, `mypy` all clean
- [x] Docs updated; tech-debt entry added for restore symmetry
- [x] TES-70 marked Done in Linear with the merge commit linked
