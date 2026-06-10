from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.test_suite import TestSuiteCreate, TestSuiteResponse, TestSuiteUpdate
from app.services import test_suite_service

router = APIRouter(tags=["test-suites"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_MANAGER = (UserRole.LEAD, UserRole.ADMIN)


@router.get(
    "/projects/{project_id}/test-suites", response_model=list[TestSuiteResponse]
)
async def list_suites(
    project_id: int,
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> list[TestSuiteResponse]:
    suites = await test_suite_service.list_suites(
        db, project_id, include_deleted=include_deleted
    )
    return [TestSuiteResponse.model_validate(s) for s in suites]


@router.post(
    "/projects/{project_id}/test-suites",
    response_model=TestSuiteResponse,
    status_code=201,
)
async def create_suite(
    project_id: int,
    data: TestSuiteCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> TestSuiteResponse:
    suite = await test_suite_service.create_suite(db, project_id, data)
    return TestSuiteResponse.model_validate(suite)


@router.get("/test-suites/{suite_id}", response_model=TestSuiteResponse)
async def get_suite(
    suite_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> TestSuiteResponse:
    suite = await test_suite_service.get_suite(db, suite_id)
    return TestSuiteResponse.model_validate(suite)


@router.put("/test-suites/{suite_id}", response_model=TestSuiteResponse)
async def update_suite(
    suite_id: int,
    data: TestSuiteUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> TestSuiteResponse:
    suite = await test_suite_service.update_suite(db, suite_id, data)
    return TestSuiteResponse.model_validate(suite)


@router.delete("/test-suites/{suite_id}", status_code=204)
async def delete_suite(
    suite_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> None:
    await test_suite_service.delete_suite(db, suite_id)


@router.post("/test-suites/{suite_id}/restore", response_model=TestSuiteResponse)
async def restore_suite(
    suite_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> TestSuiteResponse:
    suite = await test_suite_service.restore_suite(db, suite_id)
    return TestSuiteResponse.model_validate(suite)
