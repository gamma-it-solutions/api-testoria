# Execution Plan: Pass Rate = Mean of Per-Completed-Run Rates

**Date**: 2026-04-22
**Author**: gabi
**Status**: In Progress

---

## Goal

Change `pass_rate` on `ProjectStats`, `ProjectStatsItem`, and `ReportAnalyticsSummary.overall_pass_rate` from weighted `sum(passed) / sum(total)` to the unweighted **mean of each completed run's pass rate**. Revert the `passed_results` / `total_results` fields added in plan-040 — they were introduced for a weighted-overall design the user has since rejected.

---

## Context

Follow-up to plan-039 and plan-040. The user's preferred semantic: a project's pass rate is the average of its completed runs' individual pass rates, not a sum-weighted ratio. Under the current formula, one huge failing run swamps many smaller passing runs; under the new rule each completed run counts equally.

Example: run A = 1/1 (100%), run B = 0/100 (0%). Weighted = 0.99%, mean-of-rates = 50%. The user wants 50%.

---

## Scope

### In scope
- Recompute `ProjectStats.pass_rate`, `ProjectStatsItem.pass_rate`, `ReportAnalyticsSummary.overall_pass_rate` as mean of per-run rates
- Runs with `total_results == 0` (completed but empty) do NOT contribute (consistent with how per-run `pass_rate` is returned as `null`)
- Revert `passed_results` / `total_results` fields on `ProjectStatsItem` (added in plan-040) — unused under the new semantic
- Update tests that assert pass_rate values (most are still valid because fixtures use a single completed run)

### Out of scope
- Per-run `pass_rate` on `TestRun.progress` / run-level report — unchanged, always `passed / total` within one run
- Soft-delete filter parity between `get_stats` and `get_bulk_stats` — pre-existing drift, leave as tech debt

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/project.py` | Remove `passed_results`, `total_results` from `ProjectStatsItem` |
| services | `app/services/project_service.py` | In `get_stats` + `get_bulk_stats`: group results by `(project_id, run_id, status)`, derive per-run rates, return `mean(rates)` per project |
| services | `app/services/report_service.py` | In `get_report_analytics`: recompute `overall_pass_rate` as mean of per-completed-run rates for the project |
| tests | `tests/integration/test_projects_api.py` | Drop `passed_results` / `total_results` assertions |

### Key decisions

- **Mean over "runs with results" only.** An empty completed run's rate is undefined (`null`), so including it would require inventing a value. Skipping it matches what the UI shows for that run individually.
- **No schema field for the run count.** A consumer who wants the denominator of the mean can count their own runs; exposing it doubles surface area for no payoff today.
- **Compute in Python, not SQL.** Single grouped query returns `(project_id, run_id, status, count)`; aggregation into per-run rates and then per-project means runs in Python. Clearer than nested CTEs and avoids Postgres-specific window syntax.

---

## Tasks

### Implementation
- [ ] Remove `passed_results`, `total_results` from `ProjectStatsItem` schema
- [ ] Rewrite `get_stats` pass_rate to mean-of-run-rates
- [ ] Rewrite `get_bulk_stats` pass_rate to mean-of-run-rates; drop the two popped fields from the item construction
- [ ] Rewrite `get_report_analytics` `overall_pass_rate` to mean-of-run-rates
- [ ] Update `test_bulk_stats_multiple_projects` (drop new-field assertions)

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` passes
- [ ] `mypy app` passes

### Docs update
- [ ] `docs/06-generated/endpoints.md` — update `ProjectStatsItem` shape + describe new `pass_rate` semantic
- [ ] `docs/08-decisions/changelog.md` — entry explaining semantic change and that plan-040's fields were reverted
- [ ] Plan moved to `completed/`

---

## Definition of done

- [ ] `pass_rate` on project endpoints matches `mean(completed-run rates)`
- [ ] Existing fixtures still assert the same numeric values (single-completed-run fixtures)
- [ ] `passed_results` / `total_results` no longer on the response
- [ ] Tests + lint + type check green
