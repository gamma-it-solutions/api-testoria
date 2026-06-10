# Execution Plan: Unify `pass_rate` — compute over all statuses, return as 0..1 ratio

**Date**: 2026-04-20
**Author**:
**Status**: Completed

---

## Goal

Make every `pass_rate` the API emits mean the same thing:

1. **Denominator = every case/result in scope, regardless of status** (passed, failed, blocked, no_run, retest, skipped — all counted). No more "ratio-over-tested" vs "ratio-over-total" divergence.
2. **Shape = `float | None` in the range `[0.0, 1.0]`** (ratio), never `0..100` (percentage). Callers format for display.

Today the backend is inconsistent on both axes, and a mix of services return 0..1 while others return 0..100 — web has to guess per endpoint.

---

## Context

Current inventory of `pass_rate` producers:

| File | Line | Shape returned | Denominator |
|---|---|---|---|
| `app/services/test_run_service.py` | 265 | **0..1 ratio** | `tested` (excludes `no_run`) |
| `app/services/project_service.py` | 211, 317 | **0..1 ratio** | `total_results` |
| `app/services/report_service.py` | 196 (`_pass_rate`) | **0..1 ratio** | `total` (caller-supplied) |
| `app/services/report_service.py` | 517, 587 | **0..1 ratio** | `tested`, `total` |
| `app/services/report_service.py` | 156 | **0..100 percentage** | `total_results` |
| `app/services/ci_service.py` | 123 | **0..100 percentage** | `total` |

Two ambiguities fall out of this table:

1. **Scale**. Some endpoints give `0.82`, others give `82.0`. A web consumer staring at `pass_rate: 0.82` and another `pass_rate: 82.0` can't tell them apart without reading the docs. Web plan 058 is the direct consumer fix; this plan removes the cause.
2. **Denominator**. "Pass rate over *tested*" hides a run's real health when many cases are blocked or not-yet-run. A 1-case-passed, 9-cases-no_run run shouldn't read as "100% passing". The user decision here: **include every case in the denominator**, so `pass_rate = passed / total`, where `total` is every row in scope — including `no_run`, `blocked`, `skipped`, `retest`.

This is a public API contract change. Downstream consumers:

- Web app (plan 058 migrates it in lockstep)
- CLI (`testoria report …` renders the value; read-only — formatter needs a pass)
- CI badge endpoint (`GET /api/v1/ci/projects/{id}/badge` in `ci_service.py` — used externally; its color thresholds currently compare against `0..100`)

Because both numerator and denominator rules change, the `pass_rate` field value will move for existing runs — this is a breaking behaviour change (not a breaking schema change). Call it out in the changelog.

---

## Scope

### In scope

- Replace every `pass_rate` computation with `passed / total_including_no_run` (None if `total_including_no_run == 0`)
- Normalise every returned `pass_rate` to `0..1` — drop the `* 100` wherever it happens
- `TestRunProgress.pass_rate` denominator becomes `total` (was `tested`)
- `ProjectStats.pass_rate` denominator becomes `total_results` **plus `no_run` synthetic rows** if those exist in the count path, **or** equivalently `total_cases` for runs that enumerate their case-set — document whichever a single helper chooses
- `report_service.py` existing 0..100 site (`pass_rate = (passed / total_results * 100)`) drops the `* 100`
- `ci_service.py` badge: internally work with the 0..1 ratio, and format `label` / threshold check after scaling to percent locally for the badge SVG
- Introduce a single helper: `app/utils/stats.py::pass_rate(passed: int, total: int) -> float | None` — **the only place** the calculation lives; every service imports it
- Update Pydantic schemas that document `pass_rate` with a `Field(description="Ratio in [0, 1]; multiply by 100 for %")` and `ge=0, le=1` validators
- Unit tests per service confirming: (a) shape is 0..1, (b) denominator includes every status
- Integration tests confirming endpoints return values in `[0, 1]`
- Update OpenAPI / `endpoints.md` with the new contract
- Migration note in the changelog — the numeric values of `pass_rate` for existing runs will change (lower, in most cases, because `no_run` joins the denominator)

### Out of scope

- Renaming `pass_rate` → `pass_ratio` (discussed but rejected — churn not worth it; doc the shape instead)
- A second field like `pass_rate_over_executed` — keep the API lean; if product asks later, add then
- Changing CLI output formatting beyond what's needed to divide by 1 instead of by 100
- Badge color thresholds — keep the same visual thresholds, just map them to the new scale internally
- Web migration — tracked in web plan 058
- Any DB change — `pass_rate` is computed, not stored

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| util | `app/utils/stats.py` (**new**) | `pass_rate(passed, total) -> float | None` — returns `None` if `total == 0`, else `passed / total` |
| services | `app/services/test_run_service.py` | `pass_rate = (passed / total)` where `total` counts every case in the run (incl. `no_run`). Use the helper |
| services | `app/services/project_service.py:211,317` | Denominator switches from "results" to "cases in scope" (or equivalent total including `no_run`); use helper |
| services | `app/services/report_service.py:156` | Drop `* 100`; use helper; denominator includes all statuses |
| services | `app/services/report_service.py:196,517,587` | Switch denominators to all-statuses totals; use helper |
| services | `app/services/ci_service.py:123` | Compute ratio via helper; multiply by 100 *only* inside the label / threshold logic |
| schemas | `app/schemas/report.py`, `app/schemas/test_run.py`, `app/schemas/project.py` | Add `ge=0, le=1` and a clear description on every `pass_rate: float \| None` field |
| tests | `tests/unit/*_service.py` | Assert: (a) every `pass_rate` ≤ 1.0, (b) values move when `no_run` rows exist |
| tests | `tests/integration/*.py` | Assert endpoint responses are in `[0, 1]` |
| docs | `docs/06-generated/endpoints.md` | Describe the contract shift: `pass_rate` is now always a 0..1 ratio over all statuses |

### Key decisions

- **One helper, zero duplicate math.** `app/utils/stats.py::pass_rate` is the only function that divides `passed` by anything. Grep for `pass_rate =` after the refactor — every remaining line should be a helper call. This keeps the project from sliding back into the current inconsistency.
- **Denominator = all statuses.** Includes `no_run`. A run with 1 passed and 9 not-touched is **10%**, not 100%. This matches how testers actually read the number.
- **Ratio on the wire, percent in the UI.** 0..1 on the API; web / CLI / badge scale to percent at display time. Badge SVG stays visually identical because it formats `label` from the ratio.
- **Behavioural break, not schema break.** Field name and type don't change; the *value* does. Changelog entry + release note; no compat window — this is a correctness fix.
- **No compat field.** Adding `pass_rate_ratio` alongside `pass_rate` would ossify the current mess. Everyone swallows the break; the web migration in plan 058 is a one-line fix per site.
- **Validators**. Pydantic `ge=0, le=1` on the schema catches any backsliding in tests. If a future change accidentally returns `82.0`, the response serialisation fails loudly.
- **`no_run` source of truth.** For runs: the case-set defines "in scope" (plan 033 / 034 logic). For projects: every case in every active run. When both path and case-set-resolver are already factored (plan 033/034), reuse `_resolve_run_case_set`; don't reinvent the count.

---

## Tasks

### Implementation
- [x] Add `app/utils/stats.py` with `pass_rate(passed: int, total: int) -> float | None`; unit test it directly
- [x] Replace the computation in `test_run_service.py:265` with the helper; switch denominator from `tested` → `total` (all statuses, incl. `no_run`)
- [x] Replace in `project_service.py:211` and `:317` with the helper; ensure denominator is all-status totals
- [x] Replace in `report_service.py:156,196,517,587` with the helper; drop the `* 100` site
- [x] `ci_service.py:123`: use helper for the ratio; keep the percent transform local to the label and threshold comparisons only
- [x] Audit every `pass_rate` assignment/return in the codebase (grep); each should go through the helper
- [x] Add `ge=0, le=1` + description to every `pass_rate` Pydantic field
- [x] Unit tests:
  - [x] Helper: `(8, 10) -> 0.8`, `(0, 10) -> 0.0`, `(0, 0) -> None`, `(10, 10) -> 1.0`
  - [x] `test_run_service`: run with 1 passed + 9 no_run → `pass_rate == 0.1`
  - [x] `project_service`: counts include all statuses
  - [x] `report_service`: assert values ≤ 1.0 on every path
  - [x] `ci_service`: ratio is 0..1; label formats correctly from the ratio
- [x] Integration tests: every endpoint that surfaces `pass_rate` returns value in `[0, 1]` or `null`
- [x] Add a regression test that fails if any `pass_rate > 1`

### Quality check
- [x] `pytest`
- [x] `ruff check app tests`
- [x] `mypy app`
- [x] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [x] `docs/06-generated/endpoints.md` — every row mentioning `pass_rate` now says "ratio in [0, 1] over all statuses"
- [x] `docs/02-architecture/backend/service-layer.md` — document `app/utils/stats.py::pass_rate` as the canonical helper
- [x] `docs/08-decisions/changelog.md` — record: (a) unified `pass_rate` shape to 0..1, (b) unified denominator to all-statuses, (c) numeric values for existing runs will change
- [x] `docs/04-execution/tech-debt.md` — resolve any prior debt item about `pass_rate` inconsistency; log none-newly-added (this is a cleanup)
- [x] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| External CI badge consumers see a step-change in reported pass rate | Medium | Label text remains a "NN%" string; value shifts but the UX unit stays the same; release note |
| Web plan 058 ships without this, or vice versa — display stays wrong for a release | Medium | Ship together; block the web migration until this is deployed; release notes cross-link |
| Dashboards referenced in screenshots / docs show different numbers after release | Low | Update `docs/01-product/features/*` screenshots on first refresh; note in changelog |
| A hidden endpoint still returns 0..100 and slips through | Low | `ge=0, le=1` Pydantic validator plus the regression test catches it in CI |
| A project with zero cases now returns `null` where it used to return `0.0` | Low | Already the behaviour in most sites; align the few that returned `0.0` to `None` for consistency |
| `no_run` in the denominator penalises runs where testers plan to add more cases later | Low | Intended: "not run yet" lowers your pass rate until you run them; matches user expectation |

---

## Definition of done

- [x] Every `pass_rate` returned by the API is in `[0, 1]` or `None`
- [x] Every `pass_rate` denominator includes every status (passed, failed, blocked, no_run, skipped, retest)
- [x] One helper (`app/utils/stats.py::pass_rate`) performs the division; grep confirms no other site divides
- [x] Pydantic validators (`ge=0, le=1`) present on every `pass_rate` field
- [x] CI badge still renders correctly (label + color thresholds unchanged visually)
- [x] Unit + integration tests cover the helper and each service endpoint
- [x] Docs updated; changelog explains the behavioural shift
- [x] PR checklist completed
