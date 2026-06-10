from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.report import (
    CrossProjectReportAnalyticsResponse,
    CustomReportRequest,
    CustomReportResponse,
    DashboardResponse,
    MetricsResponse,
    ProjectReportAnalyticsResponse,
    RunReportResponse,
)
from app.services import report_service

router = APIRouter(tags=["reports"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)


@router.get("/projects/{project_id}/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> DashboardResponse:
    return await report_service.get_dashboard(db, project_id)


@router.get("/test-runs/{run_id}/report", response_model=None)
async def get_run_report(
    run_id: int,
    format: Literal["json", "pdf", "excel"] = Query(default="json"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> RunReportResponse | Response:
    if format == "pdf":
        data = await report_service.generate_run_report_pdf(db, run_id)
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=run-{run_id}-report.pdf"
            },
        )
    elif format == "excel":
        data = await report_service.generate_run_report_excel(db, run_id)
        xlsx_type = (
            "application/vnd.openxmlformats-officedocument" ".spreadsheetml.sheet"
        )
        return Response(
            content=data,
            media_type=xlsx_type,
            headers={
                "Content-Disposition": f"attachment; filename=run-{run_id}-report.xlsx"
            },
        )
    return await report_service.get_run_report(db, run_id)


@router.get(
    "/projects/{project_id}/report-analytics",
    response_model=ProjectReportAnalyticsResponse,
)
async def get_report_analytics(
    project_id: int,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    run_status: str | None = Query(default=None),
    include_trend: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> ProjectReportAnalyticsResponse:
    return await report_service.get_report_analytics(
        db,
        project_id,
        date_from=date_from,
        date_to=date_to,
        run_status=run_status,
        include_trend=include_trend,
    )


@router.get("/projects/{project_id}/metrics", response_model=MetricsResponse)
async def get_project_metrics(
    project_id: int,
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> MetricsResponse:
    return await report_service.get_project_metrics(db, project_id, days=days)


@router.post("/reports/custom", response_model=CustomReportResponse)
async def run_custom_report(
    filters: CustomReportRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> CustomReportResponse:
    return await report_service.run_custom_report(db, filters)


@router.get(
    "/reports/analytics",
    response_model=CrossProjectReportAnalyticsResponse,
)
async def get_cross_project_report_analytics(
    project_ids: list[int] | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    run_status: str | None = Query(default=None),
    include_trend: bool = Query(default=True),
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> CrossProjectReportAnalyticsResponse:
    return await report_service.get_cross_project_report_analytics(
        db,
        project_ids=project_ids,
        date_from=date_from,
        date_to=date_to,
        run_status=run_status,
        include_trend=include_trend,
        include_archived=include_archived,
    )
