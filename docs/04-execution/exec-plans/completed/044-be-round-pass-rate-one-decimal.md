# Execution Plan: Round All Pass-Rate Ratios to a Consistent Precision

**Date**: 2026-05-08
**Author**: gabi
**Status**: Complete

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

Every `pass_rate` ratio the API returns is rounded to **3 decimal places** (= 1 decimal place when rendered as a percent), so Dashboard, Reports, and Run-detail surfaces show consistent, predictable values without any client-side rounding drift.

---

## Context

The API returns `pass_rate` as a 0..1 ratio (api plan 035) and the web frontend currently re-rounds at every render site — `Math.round(x * 1000) / 10` here, `formatPassRate({ decimals: 1 })` there, raw `(passed/total) * 100` elsewhere. Same value can appear as `87.5%` in one widget and `87.51234%` in another tooltip. Pairs with web plan-083, which collapses every render-side conversion onto a single `formatPassRate` call. Rounding at the API boundary makes the wire value itself the source of truth — no consumer (web, CLI, future Slack bot) can disagree about what `0.875` means.

3 decimals on the ratio gives 1 decimal on the percent (`0.875` → `"87.5%"`). Picked over 4 decimals because the user-facing target is 1 decimal everywhere; storing extra precision invites tooltips that re-render `87.5234%` and undo the consistency. CLI / API consumers that need raw counts still have `passed` and `total` — they can recompute at full precision if they truly need it.

This is a presentation-precision change only — no business-logic change. The mean-of-run-rates rule (plans 035/039/041) and the per-run denominator (`passed / max(cases_in_scope, tested)`) are unchanged. Only the final returned value is rounded.

---

## Scope

### In scope

- New helper `app/utils/stats.round_ratio(value: float | None, decimals: int = 3) -> float | None` — passes `None` through, rounds otherwise. Single source of truth for the precision constant.
- Update `app/utils/stats.pass_rate(passed, total)` to apply `round_ratio` on its return value (every existing caller already routes through this helper, so this is the cheapest broad fix).
- Apply rounding at the response boundary in every service that constructs a `pass_rate` value not via `stats.pass_rate`:
  - `app/services/report_service.py`:
    - `get_dashboard()` — `DashboardResponse.pass_rate`
    - `get_report_analytics()` — `summary.overall_pass_rate`, `runs[*].pass_rate`, `trend[*].pass_rate`
    - `get_cross_project_report_analytics()` — `summary.overall_pass_rate`, `runs[*].pass_rate`, `trend[*].pass_rate`, `per_project[*].overall_pass_rate`
    - `get_run_report()` — `pass_rate` (already via `stats.pass_rate`, will inherit)
    - `get_project_metrics()` — `data[*].pass_rate` (already via `stats.pass_rate`, will inherit)
  - `app/services/project_service.py`:
    - `get_stats()` — `ProjectStats.pass_rate`
    - `get_bulk_stats()` — `ProjectStatsItem.pass_rate` (per-project mean of run rates — round after the mean, not before)
  - `app/services/test_run_service.py`:
    - `batch_run_progress()` — `TestRunProgress.pass_rate` (the canonical per-run rate; everything downstream consumes this)
- Pydantic schema docs updated to note "ratio in [0, 1] rounded to 3 decimal places". No Field validator change — Pydantic already accepts the rounded float; adding `decimal_places=3` would reject in-Python computations that happen to land at higher precision before our rounding step.
- Mean-of-run-rates aggregation: round **after** the mean is computed, not on each input. Otherwise a mean of 100 rounded values diverges noticeably from the true mean.
- Tests verify:
  - `round_ratio(None) is None`, `round_ratio(0.123456) == 0.123`, `round_ratio(0.875) == 0.875`, `round_ratio(0.0) == 0.0`, `round_ratio(1.0) == 1.0`
  - `stats.pass_rate(1, 3) == 0.333` (not `0.3333333333`)
  - Snapshot test on existing `get_dashboard`, `get_report_analytics`, `get_cross_project_report_analytics`, `get_bulk_stats` payloads — every `pass_rate` field's `len(str(x).split('.')[1]) <= 3` (or value in `{0.0, 1.0, None}`)
  - Mean-of-run-rates regression: a project with 3 completed runs whose individual `pass_rate` values are `0.333`, `0.667`, `1.000` returns an `overall_pass_rate` of `0.667` (rounded mean), not `0.666` (unrounded mean) and not `0.66666...`.

### Out of scope

- Changing the wire format from ratio to percent (still `0..1`, just rounded). The frontend continues to multiply by 100 at render via `toPercent`/`formatPassRate`.
- Hard `Field(decimal_places=3)` schema constraint — too risky for a presentation-precision change; would reject any in-flight value that hasn't been routed through `round_ratio`. Belt-and-suspenders only if a future regression needs it.
- Per-step `step_results` rounding — no rate is computed at the step level today.
- Frontend changes (separate plan in `web-testoria/plan-083`).
- `progress_percent` fields on `TestRunProgress` (none exist; pass_rate is the only ratio).
- Custom-report rows — `CustomReportRow` has no rate field.
- Database column type change (still `Float`/`Numeric`; the rounding is applied at service boundary).

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| utils | `app/utils/stats.py` | Add `round_ratio(value, decimals=3)`; update `pass_rate(passed, total)` to wrap its return in `round_ratio` |
| services | `app/services/report_service.py` | Wrap every direct `pass_rate` assignment that isn't already routed through `stats.pass_rate` (`overall_pass_rate` in three places, anywhere `passed / total` shows up inline) in `round_ratio` |
| services | `app/services/project_service.py` | Wrap `ProjectStats.pass_rate` and `ProjectStatsItem.pass_rate` in `round_ratio` after the per-project mean is computed |
| services | `app/services/test_run_service.py` | Wrap `TestRunProgress.pass_rate` in `round_ratio` in `batch_run_progress` (and any single-run helper that constructs the same shape) |
| tests | `tests/unit/test_stats_helper.py` | Add tests for `round_ratio` and the rounded `pass_rate` |
| tests | `tests/unit/test_report_service.py` | Add `len(str(x).split('.')[1]) <= 3` assertions on every returned `pass_rate`; mean-of-run-rates regression |
| tests | `tests/unit/test_project_service.py` | Same for `get_stats` / `get_bulk_stats` |
| tests | `tests/integration/test_reports_api.py` | Snapshot-style assertion on rounded values for `/dashboard`, `/projects/:id/report-analytics`, `/reports/analytics`, `/test-runs/:id/report` |
| docs | `docs/06-generated/endpoints.md` | Update the "ratio in [0, 1]" notes to "ratio in [0, 1], rounded to 3 decimal places" wherever pass_rate appears |
| docs | `docs/01-product/features/006-reporting-analytics.md` | Add a constraint line "All `pass_rate` ratios are rounded to 3 decimal places (= 1 decimal of percent) at the response boundary" |

### Key decisions

- **Round at the helper, not at every call site.** `stats.pass_rate` is the existing single point through which most rates flow; wrapping its return in `round_ratio` covers the majority of surfaces with one change. The few services that compute a mean directly (overall_pass_rate, per-project breakdown) round explicitly after the mean.
- **3 decimals on the ratio = 1 decimal on the percent.** Web-side renders multiply by 100 and format with 1 decimal. Returning more precision than the UI shows just creates rounding ambiguity in tooltips.
- **No schema-level constraint.** A Pydantic `Field(decimal_places=3)` would reject any in-flight value that bypasses our helper, including legitimate intermediate computations that haven't reached `round_ratio` yet. Keep the constraint as a service-boundary discipline tested by snapshot assertions.
- **Round after the mean, not before.** `mean([0.3333, 0.6667, 1.0000])` = `0.6667` (rounded to `0.667`); `mean([round(0.3333), round(0.6667), round(1.0000)])` = same in this case but diverges for longer chains. Standard numerical-aggregation guidance.
- **None passes through.** `pass_rate=None` means "no completed runs with results" (plan 041). `round_ratio(None)` returns `None` so empty-state handling stays unchanged.
- **No data migration.** The rounding applies to the response only; stored counts (`passed`, `total`, etc.) are unchanged. Old computed `pass_rate` values that some consumers cached won't get re-issued — but the next API call returns the rounded value, so cache replacement happens organically.

---

## Tasks

### Implementation
- [ ] Add `round_ratio(value, decimals=3)` to `app/utils/stats.py`
- [ ] Update `stats.pass_rate(passed, total)` to wrap its return in `round_ratio`
- [ ] Audit `app/services/report_service.py` for direct `pass_rate` assignments not routed through `stats.pass_rate` (the three `overall_pass_rate` mean computations); wrap them in `round_ratio`
- [ ] Audit `app/services/project_service.py` for the per-project mean computation in `get_stats` / `get_bulk_stats`; wrap in `round_ratio` after the mean
- [ ] Audit `app/services/test_run_service.py` `batch_run_progress` for the per-run rate; wrap in `round_ratio` (or rely on it routing through `stats.pass_rate` — verify)
- [ ] Grep for `passed / total`, `pass_rate=`, `pass_rate =`, and `(.+)/(.+)\s*if.*else.*None` anywhere in `app/` to catch any inline rate computation
- [ ] Add unit tests for `round_ratio` (5 cases: None, 0.0, 1.0, mid-range, repeating-decimal)
- [ ] Add unit-test assertions on every existing service test that returns a `pass_rate`: `len(str(rate).rsplit('.', 1)[1]) <= 3` or `rate in {0.0, 1.0}` or `rate is None`
- [ ] Add a mean-of-run-rates regression test (3 runs with rates `1/3`, `2/3`, `1.0` → expected mean `0.667`)
- [ ] Add integration-test assertions on `/dashboard`, `/projects/:id/report-analytics`, `/reports/analytics`, `/test-runs/:id/report` payloads that every `pass_rate` field obeys the precision constraint

### Quality check
- [ ] `pytest` — all tests pass
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [ ] `docs/06-generated/endpoints.md` updated — every `pass_rate` description notes "rounded to 3 decimal places"
- [ ] `docs/01-product/features/006-reporting-analytics.md` — add the rounding constraint section
- [ ] `docs/08-decisions/changelog.md` — Plan 044 entry: rounding rule, why 3 decimals, why service-boundary not schema-validator
- [ ] `docs/04-execution/tech-debt.md` — no entry needed unless something deferred
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| A consumer (CLI, dashboards) relies on the previous unrounded ratio for downstream math (e.g., re-summing across runs) | Low | The web frontend doesn't — it always re-formats. CLI uses raw counts (`passed`, `total`) for any aggregation. Document the change in the changelog so external consumers can adjust |
| Mean-of-run-rates aggregate value visibly shifts after the rounding rollout | Low | Difference between mean-of-rounded-rates and rounded-mean-of-unrounded-rates is bounded by `0.0005` (≈ 0.05% on percent). Below the user-visible 1-decimal precision; no perceptible shift. Snapshot test pins the exact value |
| Rounding `0.9999` to `1.000` subtly conflicts with progress-bar logic that special-cases `pass_rate == 1.0` to render "all green" | Low | Verify in tests that no client-side comparison treats `1.000` differently than `0.9994` would. The web frontend already renders pass-rate-green unconditionally (plan-080) so this is moot in practice |
| Pydantic JSON serialisation drops the trailing zeros (`0.5` instead of `0.500`) | Negligible | Numerical equality preserved; the web side multiplies by 100 and formats with `toFixed(1)`. JSON precision is not human display precision |
| New tests fail on Postgres because of slight aggregation differences vs SQLite | Low | Use `pytest.approx` with `abs=5e-4` on aggregated mean assertions; exact-equality only on direct `round_ratio` output |

---

## Definition of done

- [ ] Every API response carrying a `pass_rate` field returns a value with ≤ 3 decimal places (or `None`)
- [ ] No regression in computed values beyond the rounding precision (existing tests still pass; only new precision assertions added)
- [ ] `mean([1/3, 2/3, 1.0])` returns `0.667` from both `report_service.get_report_analytics` (per-project) and `report_service.get_cross_project_report_analytics` (cross-project) under a snapshot fixture
- [ ] `pytest`, `ruff`, `mypy` all green
- [ ] Endpoint docs note the precision; changelog entry filed
- [ ] Plan moved to `completed/`
