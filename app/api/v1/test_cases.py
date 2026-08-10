import math
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import Response as FastAPIResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.test_case import (
    ImportResult,
    TestCaseCreate,
    TestCaseListFilters,
    TestCaseResponse,
    TestCaseUpdate,
)
from app.schemas.user import PaginatedResponse
from app.services import export_service, import_service, test_case_service

router = APIRouter(tags=["test-cases"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_MANAGER = (UserRole.LEAD, UserRole.ADMIN)


@router.get("/projects/{project_id}/test-cases/export")
async def export_test_cases(
    project_id: int,
    format: Literal["csv", "excel"] = Query(default="csv"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> Response:
    if format == "csv":
        data = await export_service.export_csv(db, project_id)
        return FastAPIResponse(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=test_cases.csv"},
        )
    data = await export_service.export_excel(db, project_id)
    return FastAPIResponse(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=test_cases.xlsx"},
    )


@router.post(
    "/projects/{project_id}/test-cases/import",
    response_model=ImportResult,
)
async def import_test_cases(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> ImportResult:
    content = await file.read()
    filename = file.filename or ""
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        rows = import_service.parse_excel(content)
    else:
        rows = import_service.parse_csv(content)
    return await import_service.import_test_cases(rows, project_id, db)


@router.get(
    "/projects/{project_id}/test-cases",
    response_model=PaginatedResponse[TestCaseResponse],
)
async def list_test_cases(
    project_id: int,
    search: str | None = Query(default=None),
    suite_id: int | None = Query(default=None),
    priority: Literal["low", "medium", "high", "critical"] | None = Query(default=None),
    type: Literal["manual", "automated"] | None = Query(default=None),
    status: Literal["draft", "active", "deprecated"] | None = Query(default=None),
    tag_ids: list[int] | None = Query(default=None),
    automation_id: str | None = Query(default=None),
    has_automation_id: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> PaginatedResponse[TestCaseResponse]:
    filters = TestCaseListFilters(
        search=search,
        suite_id=suite_id,
        priority=priority,
        type=type,
        status=status,
        tag_ids=tag_ids,
        automation_id=automation_id,
        has_automation_id=has_automation_id,
        page=page,
        page_size=page_size,
    )
    cases, total = await test_case_service.list_test_cases(
        db, project_id, filters, include_deleted=include_deleted
    )
    pages = math.ceil(total / page_size) if page_size else 1
    return PaginatedResponse(
        items=[TestCaseResponse.model_validate(tc) for tc in cases],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.post(
    "/projects/{project_id}/test-cases",
    response_model=TestCaseResponse,
    status_code=201,
)
async def create_test_case(
    project_id: int,
    data: TestCaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> TestCaseResponse:
    tc = await test_case_service.create_test_case(
        db, project_id, data, user_id=current_user.id
    )
    return TestCaseResponse.model_validate(tc)


@router.get("/test-cases/{test_case_id}", response_model=TestCaseResponse)
async def get_test_case(
    test_case_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> TestCaseResponse:
    tc = await test_case_service.get_test_case(db, test_case_id)
    return TestCaseResponse.model_validate(tc)


@router.put("/test-cases/{test_case_id}", response_model=TestCaseResponse)
async def update_test_case(
    test_case_id: int,
    data: TestCaseUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> TestCaseResponse:
    tc = await test_case_service.update_test_case(
        db, test_case_id, data, user_id=current_user.id
    )
    return TestCaseResponse.model_validate(tc)


@router.delete("/test-cases/{test_case_id}", status_code=204)
async def delete_test_case(
    test_case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> None:
    await test_case_service.delete_test_case(db, test_case_id, user_id=current_user.id)


@router.post("/test-cases/{test_case_id}/restore", response_model=TestCaseResponse)
async def restore_test_case(
    test_case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> TestCaseResponse:
    tc = await test_case_service.restore_test_case(
        db, test_case_id, user_id=current_user.id
    )
    return TestCaseResponse.model_validate(tc)
