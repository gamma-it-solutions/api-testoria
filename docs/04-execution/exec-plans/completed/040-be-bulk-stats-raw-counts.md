# Execution Plan: Raw Passed/Total Counts on Project Bulk Stats

**Date**: 2026-04-22
**Author**: gabi
**Status**: In Progress

---

## Goal

Expose `passed_results` and `total_results` (raw integer counts across all completed runs of each project) on `ProjectStatsItem`, so the web dashboard can compute a correctly weighted overall pass rate across projects.

---

## Context

`GET /projects/stats` currently returns a derived `pass_rate` per project but no raw counts. The frontend dashboard needs raw counts to compute `sum(passed) / sum(total)` across multiple projects — the weighted overall rate. Without these fields the dashboard today falls back to an equal-weight mean of percentages, which over-represents low-volume projects.

Related: web-testoria plan-079.

---

## Scope

### In scope
- Add two integer fields to `ProjectStatsItem` schema
- Populate them in `project_service.get_bulk_stats` from data already computed in that method

### Out of scope
- Changes to the single-project `GET /projects/{id}/stats` endpoint — dashboard aggregation only uses bulk stats
- Changing how pass rate is computed (still completed runs only, soft-delete filtered)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/project.py` | Add `passed_results: int` and `total_results: int` to `ProjectStatsItem` |
| services | `app/services/project_service.py` | Populate the two fields from the existing `passed_results_by_project` / `total_results_by_project` dicts |
| tests | `tests/integration/test_projects_api.py` (or equivalent) | Assert new fields on bulk stats response |

### Key decisions

- **Counts reflect completed runs only** — same filter used for the existing `pass_rate` to keep semantics consistent. Consumers can still derive pass rate = `passed_results / total_results` if they want to recompute.
- **No change to `ProjectStats`** (single-project endpoint) — keeps scope tight; single-project consumers already have the derived `pass_rate` they need.

---

## Tasks

### Implementation
- [ ] Add `passed_results` + `total_results` fields to `ProjectStatsItem`
- [ ] Populate them in `get_bulk_stats`
- [ ] Add integration test assertion for new fields

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` passes
- [ ] `mypy app` passes

### Docs update
- [ ] `docs/06-generated/endpoints.md` — update `/projects/stats` response shape
- [ ] `docs/08-decisions/changelog.md` — plan entry
- [ ] Plan moved to `completed/`

---

## Definition of done

- [ ] Bulk stats response includes `passed_results` and `total_results`
- [ ] Tests + lint + type check green
- [ ] Docs updated
