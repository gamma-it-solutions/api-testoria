from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.milestone import MilestoneCreate, MilestoneResponse, MilestoneUpdate
from app.services import milestone_service

router = APIRouter(tags=["milestones"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_MANAGER = (UserRole.LEAD, UserRole.ADMIN)


@router.get(
    "/projects/{project_id}/milestones",
    response_model=list[MilestoneResponse],
)
async def list_milestones(
    project_id: int,
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> list[MilestoneResponse]:
    items = await milestone_service.list_milestones(
        db, project_id, include_deleted=include_deleted
    )
    return [MilestoneResponse.model_validate(m) for m in items]


@router.post(
    "/projects/{project_id}/milestones",
    response_model=MilestoneResponse,
    status_code=201,
)
async def create_milestone(
    project_id: int,
    data: MilestoneCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> MilestoneResponse:
    ms = await milestone_service.create_milestone(db, project_id, data)
    return MilestoneResponse.model_validate(ms)


@router.put("/milestones/{milestone_id}", response_model=MilestoneResponse)
async def update_milestone(
    milestone_id: int,
    data: MilestoneUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> MilestoneResponse:
    ms = await milestone_service.update_milestone(db, milestone_id, data)
    return MilestoneResponse.model_validate(ms)


@router.delete("/milestones/{milestone_id}", status_code=204)
async def delete_milestone(
    milestone_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> None:
    await milestone_service.delete_milestone(db, milestone_id)


@router.post("/milestones/{milestone_id}/restore", response_model=MilestoneResponse)
async def restore_milestone(
    milestone_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_MANAGER)),
) -> MilestoneResponse:
    ms = await milestone_service.restore_milestone(db, milestone_id)
    return MilestoneResponse.model_validate(ms)
