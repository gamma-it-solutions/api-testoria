# Execution Plan: 009 — Backend Phase 5: Reporting & Analytics

**Date**: 2026-03-24
**Author**: gabi
**Status**: Complete
**Priority**: HIGH
**Dependency**: 014-be-phase3-test-execution must be complete (results data must exist)

---

## Goal

Implement the reporting and analytics layer: dashboard metrics, run-level reports, custom report queries, and PDF/Excel export generation.

---

## Context

Phase 5 of `backend-implementation.md` (Phase 4 is WebSockets — plan 005). The frontend dashboard, project metrics view, and run report views all call these endpoints. Report generation for PDF/Excel is handled synchronously for now (Celery async generation is deferred as noted in the success criteria).

---

## Scope

### In scope
- `app/services/report_service.py` — metrics calculation, PDF generation (WeasyPrint or ReportLab), Excel generation (openpyxl)
- `app/schemas/report.py` — DashboardResponse, RunReportResponse, MetricsResponse, CustomReportRequest
- `app/api/v1/reports.py` — dashboard, run report, metrics, custom report

### Out of scope
- Celery async report tasks (deferred — `report_tasks.py` skeleton only)
- Email delivery of reports
- Scheduled report generation

---

## Technical approach

### Endpoints

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| GET | `/projects/{id}/dashboard` | viewer | Aggregated dashboard data |
| GET | `/test-runs/{id}/report` | viewer | Full run report (cases + results) |
| GET | `/projects/{id}/metrics` | viewer | Time-series pass rate, result trends |
| POST | `/reports/custom` | viewer | Custom filter query (date range, status, suite) |

### Dashboard response shape

```python
class DashboardResponse(BaseModel):
    total_test_cases: int
    total_test_runs: int
    total_test_suites: int
    pass_rate: float           # percentage of Passed results across all runs
    active_runs: int
    recent_runs: list[RunSummary]   # last 5 runs with pass/fail counts
    result_distribution: dict[str, int]  # {"Passed": N, "Failed": N, ...}
```

### Metrics calculation

```python
@staticmethod
async def get_dashboard(db: AsyncSession, project_id: int) -> dict:
    # Count test cases in project
    total_cases = await db.scalar(
        select(func.count(TestCase.id))
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(TestSuite.project_id == project_id)
    )
    # Count test runs
    total_runs = await db.scalar(
        select(func.count(TestRun.id)).where(TestRun.project_id == project_id)
    )
    # Pass rate across all results in project's runs
    total_results = await db.scalar(
        select(func.count(TestResult.id))
        .join(TestRun).where(TestRun.project_id == project_id)
    )
    passed = await db.scalar(
        select(func.count(TestResult.id))
        .join(TestRun).where(
            TestRun.project_id == project_id,
            TestResult.status == "Passed"
        )
    )
    pass_rate = (passed / total_results * 100) if total_results else 0
    return {...}
```

### PDF export

```python
@staticmethod
async def generate_run_report_pdf(db: AsyncSession, run_id: int) -> bytes:
    # Fetch run + all results with case titles
    # Render using WeasyPrint or ReportLab
    # Return bytes for streaming response
```

### Excel export

```python
@staticmethod
async def generate_run_report_excel(db: AsyncSession, run_id: int) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Test Results"
    ws.append(["Case ID", "Title", "Status", "Comment", "Tested By", "Tested At"])
    # ...rows...
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
```

### Report router

```python
@router.get("/{run_id}/report")
async def get_run_report(
    run_id: int,
    format: str = "json",    # json | pdf | excel
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if format == "pdf":
        data = await ReportService.generate_run_report_pdf(db, run_id)
        return Response(content=data, media_type="application/pdf")
    elif format == "excel":
        data = await ReportService.generate_run_report_excel(db, run_id)
        return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        return await ReportService.get_run_report(db, run_id)
```

---

## Tasks

### Schemas
- [x]Write `app/schemas/report.py` — RunSummary, DashboardResponse, RunReportResponse, MetricsResponse, CustomReportRequest

### Services
- [x]Write `app/services/report_service.py`:
  - `get_dashboard(db, project_id)` — all aggregated counts and pass rate
  - `get_run_report(db, run_id)` — run info + all cases + their results
  - `get_project_metrics(db, project_id, days)` — time-series data
  - `run_custom_report(db, filters)` — filtered result set
  - `generate_run_report_pdf(db, run_id)` — PDF bytes
  - `generate_run_report_excel(db, run_id)` — Excel bytes

### Router
- [x]Write `app/api/v1/reports.py`:
  - `GET /projects/{id}/dashboard`
  - `GET /test-runs/{id}/report?format=json|pdf|excel`
  - `GET /projects/{id}/metrics?days=30`
  - `POST /reports/custom`
- [x]Register `reports.router` in `app/main.py`

### Celery skeleton (deferred)
- [x]Write `app/tasks/report_tasks.py` — stub file with TODO comments for async report generation

### Tests
- [x]`tests/integration/test_reports_api.py`:
  - Dashboard returns correct pass rate given known results
  - Run report includes all cases with results
  - PDF endpoint returns `Content-Type: application/pdf`
  - Excel endpoint returns correct content type
  - 401 without auth, 404 for unknown project/run

### Quality check
- [x]`pytest` passes
- [x]`ruff check app tests` clean
- [x]`mypy app` clean

### Docs
- [x]`api/docs/06-generated/endpoints.md` — verify reporting rows
- [x]Move to `completed/`

---

## Definition of done

- [x]`GET /projects/{id}/dashboard` returns total_test_cases, total_test_runs, pass_rate, active_runs, recent_runs
- [x]`GET /test-runs/{id}/report` returns run info + all test cases with their results
- [x]`GET /test-runs/{id}/report?format=pdf` returns a downloadable PDF
- [x]`GET /test-runs/{id}/report?format=excel` returns a downloadable Excel file
- [x]Pass rate calculation is accurate (verified against known test data)
- [x]401 without auth, 404 for unknown IDs
- [x]Integration tests pass
