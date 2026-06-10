# Execution Plan: Record `ResultHistory` only on meaningful change (align `submit` with `update_result`)

**Date**: 2026-04-20
**Author**:
**Status**: Completed

---

## Goal

Stop appending redundant `ResultHistory` rows when a tester re-submits the same verdict/comment for a test case. Today `submit()` records a history row unconditionally while `update_result()` records one only when `status` changes — inconsistent, and the root cause behind the duplicate entries rendered by the web "History & Context" timeline (web plan 063 is the consumer fix).

New contract: a `ResultHistory` row is written when — and only when — the result's state changes in a way a reviewer cares about:

- Initial creation of a `TestResult` (one row)
- Subsequent change in `status`
- Subsequent change in `comment` (same status, new comment — still a meaningful event)
- Subsequent change in `step_results` (per plan 031) — optional for v1; logged as follow-up if deferred

A pure no-op re-submit (same status + same comment + same step_results) writes no new history row.

---

## Context

Two code paths diverge today:

```py
# app/services/test_result_service.py:96-148  — submit()
if tr is not None:
    # update in place
else:
    # create
await db.flush()
await _record_history(db, tr.id, tr.status, tr.comment, user_id)   # ← always
```

```py
# app/services/test_result_service.py:151-176  — update_result()
if data.status is not None and data.status != old_status:
    await _record_history(db, tr.id, tr.status, tr.comment, user_id)  # ← only on status change
```

Result:
- `POST /test-results` (submit) N times with the same payload → N `ResultHistory` rows
- `PATCH /test-results/{id}` (update) N times with the same payload → 0 additional rows

The web (`TestResultHistoryPanel.vue`) then prepends the current `result` to the timeline on top of `props.history`, producing visible duplicates — but even without that client behaviour, the DB has redundant rows.

### Secondary consideration

`_record_history` is called after `db.flush()`; the row references `tr.status` and `tr.comment`. If `submit()` created a new `TestResult`, the history row is the correct "created as X" marker. If `submit()` updated an existing result without changing anything, the row is pure noise.

### Why this is the right layer

The duplicate could be deduplicated on the read side (`get_history`), but that's papering over the data. History rows are append-only audit; write them only when there's something to audit.

---

## Scope

### In scope

- Align `submit()` with `update_result()`: record history if and only if a meaningful field changed — `status`, `comment`, or `step_results`. On first creation of the `TestResult`, always record (the "created" event).
- Introduce a single helper `_should_record_history(old, new) -> bool` (or inline with a clear comment) used by both call sites, so they cannot diverge again.
- `update_result()` also records on `comment` change (today it only records on `status` change); align to the same helper.
- Optional: record on `step_results` diff; if complex, defer to a follow-up (decision below). For v1 this plan records on `status` or `comment` change; `step_results` deferred and logged.
- No data migration. Existing redundant history rows stay. Write-path is fixed going forward.
- Unit tests covering:
  - Create → 1 history row
  - Resubmit same status + same comment → 0 additional rows
  - Resubmit same status + new comment → 1 additional row
  - Resubmit new status → 1 additional row
  - `update_result` with same status + new comment → 1 additional row (behavioural fix)
  - `update_result` with same status + same comment → 0 additional rows (unchanged)
- Integration test confirming `GET /test-results/{id}/history` returns a non-redundant timeline after a no-op resubmit
- Docs: `endpoints.md` notes the contract; data-layer doc describes when a history row is written

### Out of scope

- Deduplicating the existing rows in production (migration that collapses consecutive identical rows) — logged as follow-up; needs product sign-off
- Recording step_results diffs as history events (if deferred) — logged
- Exposing a "changed fields" summary on history rows — separate feature
- Changes to `get_history` read path beyond what today's ordering already does (`changed_at ASC`)
- Client-side deduping — web plan 063 covers the timeline rendering regardless, since older data will still have duplicates
- Auth / role changes — unchanged

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| services | `app/services/test_result_service.py` | Add `_should_record_history(old_tr, new_status, new_comment, created: bool) -> bool`; gate `_record_history` in `submit()` and `update_result()` through it |
| services | `app/services/test_result_service.py` | `submit()` — capture the pre-update `(status, comment)` when the result exists; compare after the in-place update; only call `_record_history` if meaningful change or creation |
| services | `app/services/test_result_service.py` | `update_result()` — broaden the current "status changed" gate to "status OR comment changed" via the helper |
| tests | `tests/unit/test_test_result_service.py` | Case matrix (create / resubmit same / resubmit new status / resubmit new comment / update no-op / update comment-only) |
| tests | `tests/integration/test_test_results_api.py` | `GET /test-results/{id}/history` non-redundant after no-op resubmit |
| docs | `docs/06-generated/endpoints.md`, `docs/02-architecture/backend/data-layer.md` | Document when history rows are written |

### Key decisions

- **Creation always records.** A first `TestResult` is itself a new audit event. Skipping it would lose the "first tested" marker.
- **Comment change is a history event.** A tester correcting a comment after a verdict is a reviewer-relevant fact. `update_result()` currently misses this; fix it to align with the new contract.
- **`step_results` change deferred.** Diffing per-step arrays cleanly requires a concise serialisation on the history row; designing that belongs to a separate plan. Logged in tech debt with a note that it may generate empty history entries if coarsely handled.
- **Single helper, two call sites.** The whole problem stems from two paths that did the same thing differently; one helper makes drift impossible.
- **No schema change, no migration.** Past rows are an imperfect audit, not a corruption. A retroactive cleanup is product-facing and deserves explicit review.
- **Treat `None` and empty string `""` for `comment` as equal** for the change check — avoids spurious history rows when the client sends `null` vs omitting the field.
- **Pre-state snapshot.** In `submit()`, capture `(tr.status, tr.comment)` **before** the in-place assignment; compare against the incoming data. Do not rely on post-flush state.

---

## Tasks

### Implementation
- [x] Add `_should_record_history(*, created: bool, old_status: str | None, old_comment: str | None, new_status: str, new_comment: str | None) -> bool`
  - [x] Return `True` if `created`
  - [x] Return `True` if `old_status != new_status`
  - [x] Return `True` if `(old_comment or None) != (new_comment or None)` (treat `""` as `None`)
  - [x] Else return `False`
- [x] `submit()`:
  - [x] Before the in-place update branch, snapshot `(old_status, old_comment)` from the existing `tr` (or `None, None` in the create branch)
  - [x] Apply the update / create as today
  - [x] Flush, then gate `_record_history` via the helper
- [x] `update_result()`:
  - [x] Broaden the current `data.status != old_status` check to the helper
  - [x] Snapshot `old_comment` upfront too
  - [x] Gate `_record_history` via the helper
- [x] Keep `_record_history` signature as today — no change to the row itself
- [x] Unit tests (`tests/unit/test_test_result_service.py`):
  - [x] `submit` creating → 1 row
  - [x] `submit` same status + same comment → no new row
  - [x] `submit` same status + new comment → 1 new row
  - [x] `submit` new status → 1 new row
  - [x] `submit` after update-only-via-PATCH (ensure the two paths compose sanely)
  - [x] `update_result` same status + same comment → no new row (regression guard)
  - [x] `update_result` same status + new comment → 1 new row (behavioural fix)
  - [x] `update_result` new status → 1 new row (unchanged)
  - [x] `None` vs `""` comment treated as equal
- [x] Integration test (`tests/integration/test_test_results_api.py`):
  - [x] Submit the same verdict twice on the same case — `GET .../history` returns exactly one row
  - [x] PATCH comment-only on an existing result — history reflects the comment change
- [x] Manual smoke against a seeded run: resubmit a case three times; verify history length

### Quality check
- [x] `pytest`
- [x] `ruff check app tests`
- [x] `mypy app`
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — note when `ResultHistory` is appended (creation; status change; comment change)
- [x] `docs/02-architecture/backend/data-layer.md` — document the `_should_record_history` rule; reference the helper
- [x] `docs/08-decisions/changelog.md` — record: aligned `submit` with `update_result`; history no longer grows on no-op resubmits; comment-only changes now recorded
- [x] `docs/04-execution/tech-debt.md` — log follow-ups: (a) record `step_results` diffs as history, (b) optional backfill / compaction of legacy redundant rows, (c) surface a "changed fields" summary on each row
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| A test currently assumed a history row appears on every resubmit and breaks after this change | Medium | Update tests to the new contract; grep `_record_history` / `get_history` in tests and adjust |
| Comment `None` vs `""` inconsistency on the API causes spurious or missing rows | Low | Helper normalises both to `None`; covered by unit test |
| Legacy rows remain in the DB and the web timeline still shows duplicates until web plan 063 merges | Expected | Both plans coordinated; web plan handles legacy data anyway via render-layer dedup |
| Recording on comment-only change adds history rows where there were none before (update_result) | Low | Intended: comment changes are audit-relevant; changelog entry communicates this |
| Concurrent submits race and create two history rows because two requests both see `old == new`-ish state | Low | Acceptable; acceptable audit noise. If it becomes an issue, add a DB-side unique constraint on (result_id, status, comment, minute_bucket) — logged as follow-up |
| `step_results` not yet covered in the change check misses a reviewer-relevant edit | Medium | Documented as known limitation; follow-up plan tracks |

---

## Definition of done

- [x] Single `_should_record_history` helper used by both `submit` and `update_result`
- [x] Creation records exactly one history row
- [x] No-op resubmit records zero additional rows
- [x] Status change records one row
- [x] Comment-only change (via submit or update) records one row
- [x] `None` vs `""` comment treated as equal
- [x] Unit + integration tests cover the matrix
- [x] Docs updated; changelog explains the behavioural shift
- [x] PR checklist completed
