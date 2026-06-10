# Feature: Reporting & Analytics

Phase 5 of the backend implementation.

---

## What it does

Provides aggregated metrics and exportable reports for projects and test runs:

- **Project Dashboard** — total counts (cases, suites, runs), pass rate, active runs, recent runs with status breakdown, result distribution
- **Report Analytics** — single aggregated payload powering the frontend Reports & Analytics page: project summary, all runs in a date window with precomputed status counts, test-case distribution by priority/type/automation, and a zero-filled daily trend series. Eliminates the N-fetch-per-run pattern in the UI
- **Cross-project Report Analytics** — same payload shape, aggregated across all projects (or a caller-supplied subset) plus a per-project breakdown row list, powering the Reports page when "All projects" is selected (plan 043)
- **Run Report** — detailed per-case results for a test run, available as JSON, PDF, or Excel download
- **Project Metrics** — time-series pass rate by day over a configurable window (1-365 days)
- **Custom Report** — filtered result query with optional filters: project, suite, run, status, date range; paginated

---

## API surface

| Method | Path | Description |
|--------|------|-------------|
| GET | `/projects/{id}/dashboard` | Full dashboard metrics |
| GET | `/projects/{id}/report-analytics` | Aggregated payload for the Reports & Analytics page (summary + runs + distributions + trend) |
| GET | `/reports/analytics` | Cross-project aggregated payload + per-project breakdown (plan 043) |
| GET | `/test-runs/{id}/report?format=json\|pdf\|excel` | Run report with optional export |
| GET | `/projects/{id}/metrics?days=30` | Time-series metrics |
| POST | `/reports/custom` | Custom filtered report |

All endpoints require `read_only` or higher role.

---

## Constraints

- PDF generated via ReportLab (pure Python, no system dependencies)
- Excel generated via openpyxl (same library used for test case export)
- All report generation is synchronous (in-request). Async Celery generation deferred — `app/tasks/report_tasks.py` is a stub
- Dashboard pass rate is a percentage (0-100); run report pass rate is a ratio (0-1), matching existing `ProjectStats` and `TestRunProgress` patterns
- `/projects/{id}/report-analytics` builds its response with 3–4 grouped SQL queries (no per-run loop); `summary` counts are project-wide while `runs` and `trend` respect the optional `date_from`/`date_to` window (filtering runs by `completed_at ?? created_at`). With both bounds set the trend array is zero-filled so charts draw a continuous X axis
- All report endpoints exclude soft-deleted rows (projects, suites, cases, runs, results) via `not_deleted()` filters — counts, distributions, and trends only reflect live data
- **Completed-only pass-rate (plan 039)** — `DashboardResponse.pass_rate` / `result_distribution`, `ReportAnalyticsSummary.overall_pass_rate` / `result_distribution`, `ProjectStats.pass_rate`, `ProjectStatsItem.pass_rate`, and the `trend` series all count only results from runs where `status='completed'`. The `active_runs` KPI still counts `planned + active` as work-in-flight. The per-run counts in `ReportAnalyticsResponse.runs[*]` and `DashboardResponse.recent_runs[*]` surface every run's pass/fail counts regardless of its status
- **Pass-rate precision (plan 044)** — every `pass_rate` ratio returned by the API is rounded to **3 decimal places** at the response boundary (= 1 decimal place when rendered as a percent: `0.875` → `"87.5%"`). `stats.round_ratio()` is the single source of truth for the precision constant. Aggregations (overall_pass_rate as mean of per-completed-run rates) operate on raw values and round once at the end so `mean([1/3, 1.0])` returns `0.667`, not `0.666`. `TestRunProgress.pass_rate` rounds via a Pydantic `field_serializer` so the in-memory value stays raw for reuse by report aggregations.
- **Cross-project endpoint (plan 043)** — `GET /reports/analytics` returns the same shape as the per-project endpoint, scoped to the resolved project set (`?project_ids=…` repeated, or every visible project when omitted). `summary.overall_pass_rate` is the mean of every completed run's own pass rate across the whole scope (not the mean of per-project means). `per_project[]` rows apply the same rule per project so each row agrees with `/projects/stats` for that project. `include_archived=false` by default; unknown ids in `project_ids` are silently dropped. `RunAnalyticsItem` now carries `project_id` (always) and `project_name` (populated only by this endpoint).
- **Pass rate = mean of per-completed-run rates (plan 041)** — `ProjectStats.pass_rate`, `ProjectStatsItem.pass_rate`, and `ReportAnalyticsSummary.overall_pass_rate` are the arithmetic mean of each completed run's own `pass_rate`. Per-run rate comes from `test_run_service.batch_run_progress` (`passed / max(cases_in_scope, tested)`) — the same value surfaced by the run-list endpoint — so Dashboard, per-project breakdown, and Reports KPI all agree for a given run. Runs whose own `pass_rate` is `null` (empty completed runs) contribute nothing. `DashboardResponse.pass_rate` retains the legacy weighted formula for now since no consumer complained; revisit if inconsistency surfaces
- **Automation coverage counts `type='automated'`** — `TestCaseDistribution.by_automation.automated` counts cases whose user-facing `type` field is `'automated'`, regardless of whether `automation_id` (the CI linkage id) is populated. Before, a case marked automated without a CI id wrongly counted as manual, and the Reports donut rendered 100% manual for any project that hadn't wired CI
