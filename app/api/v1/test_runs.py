import math
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.test_run import (
    TestRunCasesUpdate,
    TestRunCreate,
    TestRunProgress,
    TestRunResponse,
    TestRunSuiteTree,
    TestRunUpdate,
    TestRunWithCases,
)
from app.schemas.user import PaginatedResponse
from app.services import test_run_service

router = APIRouter(tags=["test-runs"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_TESTER = (UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_MANAGER = (UserRole.LEAD, UserRole.ADMIN)


@router.get(
    "/projects/{project_id}/test-runs",
    response_model=PaginatedResponse[TestRunResponse],
)
async def list_runs(
    project_id: int,
    status: Literal[
        "planned", "active", "in_progress", "completed", "aborted"
    ]
    | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> PaginatedResponse[TestRunResponse]:
    normalised_status = "active" if status == "in_progress" else status
    runs, total, progress_map = await test_run_service.list_runs(
        db,
        project_id,
        status=normalised_status,
        page=page,
        page_size=page_size,
        include_deleted=include_deleted,
        include_progress=True,
    )
    pages = math.ceil(total / page_size) if page_size else 1
    items: list[TestRunResponse] = []
    for r in runs:
        resp = TestRunResponse.model_validate(r)
        if progress_map is not None:
            resp = resp.model_copy(update={"progress": progress_map.get(r.id)})
        items.append(resp)
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.post(
    "/projects/{project_id}/test-runs",
    response_model=TestRunResponse,
    status_code=201,
)
async def create_run(
    project_id: int,
    data: TestRunCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_TESTER)),
) -> TestRunResponse:
    run = await test_run_service.create_run(
        db, project_id, data, user_id=current_user.id
    )
    return TestRunResponse.model_validate(run)


@router.get("/test-runs/{run_id}", response_model=TestRunResponse)
async def get_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> TestRunResponse:
    run = await test_run_service.get_run(db, run_id)
    return TestRunResponse.model_validate(run)


@router.put("/test-runs/{run_id}", response_model=TestRunResponse)
async def update_run(
    run_id: int,
    data: TestRunUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_TESTER)),
) -> TestRunResponse:
    run = await test_run_service.update_run(db, run_id, data)
    return TestRunResponse.model_validate(run)


@router.delete("/test-runs/{run_id}", status_code=204)
async def delete_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> None:
    await test_run_service.delete_run(db, run_id, user_id=current_user.id)


@router.post("/test-runs/{run_id}/restore", response_model=TestRunResponse)
async def restore_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> TestRunResponse:
    run = await test_run_service.restore_run(db, run_id, user_id=current_user.id)
    return TestRunResponse.model_validate(run)


@router.post("/test-runs/{run_id}/close", response_model=TestRunResponse)
async def close_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_TESTER)),
) -> TestRunResponse:
    run = await test_run_service.close_run(db, run_id, user_id=current_user.id)
    return TestRunResponse.model_validate(run)


@router.get("/test-runs/{run_id}/progress", response_model=TestRunProgress)
async def get_progress(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> TestRunProgress:
    return await test_run_service.get_progress(db, run_id)


@router.put("/test-runs/{run_id}/cases", status_code=204)
async def set_run_cases(
    run_id: int,
    data: TestRunCasesUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_TESTER)),
) -> None:
    await test_run_service.set_run_cases(db, run_id, data.test_case_ids)


@router.get(
    "/test-runs/{run_id}/cases",
    response_model=TestRunWithCases | TestRunSuiteTree,
)
async def get_run_with_cases(
    run_id: int,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    sort: Literal["suite_id,id", "id", "title", "priority", "suite"] = Query(
        default="suite_id,id"
    ),
    group_by: Literal["suite"] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> TestRunWithCases | TestRunSuiteTree:
    if group_by == "suite":
        tree_data = await test_run_service.get_suite_tree(db, run_id)
        return TestRunSuiteTree.model_validate(tree_data)
    data = await test_run_service.get_with_cases(
        db, run_id, limit=limit, offset=offset, sort=sort
    )
    return TestRunWithCases.model_validate(data)
