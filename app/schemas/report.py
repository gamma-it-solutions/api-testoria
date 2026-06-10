from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: str
    passed: int
    failed: int
    blocked: int
    no_run: int
    total: int
    created_at: datetime


class DashboardResponse(BaseModel):
    total_test_cases: int
    total_test_runs: int
    total_test_suites: int
    pass_rate: float = Field(ge=0, le=1)
    active_runs: int
    recent_runs: list[RunSummary]
    result_distribution: dict[str, int]


class RunReportAttachment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    mime_type: str | None
    url: str = ""
    # Internal — used by backend PDF/Excel generators to fetch bytes from
    # storage. Not intended for direct consumption by the frontend; the `url`
    # field is the public shape.
    object_key: str = ""
    storage_backend: str = "s3"


class RunReportCaseResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: int
    case_title: str
    suite_name: str | None
    priority: str
    status: str | None
    comment: str | None
    tested_by: int | None
    tested_at: datetime | None
    execution_time: int | None
    attachments: list[RunReportAttachment] = Field(default_factory=list)


class RunReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    run_name: str
    run_status: str
    project_id: int
    created_at: datetime
    completed_at: datetime | None
    passed: int
    failed: int
    blocked: int
    # `no_run` includes cases with no result yet (plan 035 / untested-fold).
    no_run: int
    total: int
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    cases: list[RunReportCaseResult]


class MetricsDataPoint(BaseModel):
    date: str
    passed: int
    failed: int
    blocked: int
    no_run: int
    total: int
    pass_rate: float | None = Field(default=None, ge=0, le=1)


class MetricsResponse(BaseModel):
    project_id: int
    days: int
    data: list[MetricsDataPoint]


class CustomReportRequest(BaseModel):
    project_id: int
    suite_id: int | None = None
    run_id: int | None = None
    status: list[str] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class CustomReportRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    result_id: int
    run_id: int
    run_name: str
    case_id: int
    case_title: str
    status: str
    comment: str | None
    tested_by: int | None
    tested_at: datetime


class CustomReportResponse(BaseModel):
    items: list[CustomReportRow]
    total: int
    page: int
    page_size: int
    pages: int


class RunAnalyticsItem(BaseModel):
    id: int
    project_id: int
    # Populated only by the cross-project analytics endpoint so the per-project
    # endpoint avoids an extra join — the project is already known from its URL.
    project_name: str | None = None
    name: str
    status: str
    milestone_id: int | None
    assigned_to: int | None
    passed: int
    failed: int
    blocked: int
    no_run: int
    total: int
    pass_rate: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime
    completed_at: datetime | None


class TestCaseDistribution(BaseModel):
    by_priority: dict[str, int]
    by_type: dict[str, int]
    by_automation: dict[str, int]


class TrendPoint(BaseModel):
    date: str
    passed: int
    failed: int
    blocked: int
    no_run: int
    total: int
    pass_rate: float | None = Field(default=None, ge=0, le=1)


class ReportAnalyticsSummary(BaseModel):
    total_test_cases: int
    total_test_suites: int
    total_test_runs: int
    active_runs: int
    overall_pass_rate: float = Field(ge=0, le=1)
    total_results: int
    result_distribution: dict[str, int]


class ProjectReportAnalyticsResponse(BaseModel):
    project_id: int
    date_from: datetime | None
    date_to: datetime | None
    summary: ReportAnalyticsSummary
    runs: list[RunAnalyticsItem]
    test_case_distribution: TestCaseDistribution
    trend: list[TrendPoint]


class PerProjectAnalyticsRow(BaseModel):
    project_id: int
    project_name: str
    is_archived: bool
    total_test_runs: int
    completed_runs: int
    overall_pass_rate: float | None = Field(default=None, ge=0, le=1)
    total_results: int


class CrossProjectReportAnalyticsResponse(BaseModel):
    # Echoes the project id set actually used to build the response. `null`
    # when the caller didn't pass `project_ids` and the server resolved the
    # full visible set — useful for the UI to detect "all projects" vs an
    # explicit subset.
    project_ids: list[int] | None
    date_from: datetime | None
    date_to: datetime | None
    summary: ReportAnalyticsSummary
    runs: list[RunAnalyticsItem]
    test_case_distribution: TestCaseDistribution
    trend: list[TrendPoint]
    per_project: list[PerProjectAnalyticsRow]
