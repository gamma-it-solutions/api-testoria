import math

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.project import (
    ProjectCreate,
    ProjectResponse,
    ProjectStats,
    ProjectStatsBulkResponse,
    ProjectUpdate,
)
from app.schemas.user import PaginatedResponse
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_MANAGER = (UserRole.LEAD, UserRole.ADMIN)
_ADMIN = (UserRole.ADMIN,)


@router.get("", response_model=PaginatedResponse[ProjectResponse])
async def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    include_archived: bool = Query(default=False),
    include_deleted: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> PaginatedResponse[ProjectResponse]:
    projects, total = await project_service.list_projects(
        db,
        page=page,
        page_size=page_size,
        include_archived=include_archived,
        include_deleted=include_deleted,
    )
    pages = math.ceil(total / page_size) if page_size else 1
    return PaginatedResponse(
        items=[ProjectResponse.model_validate(p) for p in projects],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> ProjectResponse:
    project = await project_service.create_project(db, data, user_id=current_user.id)
    return ProjectResponse.model_validate(project)


@router.get("/stats", response_model=ProjectStatsBulkResponse)
async def get_bulk_project_stats(
    include_archived: bool = Query(default=False),
    project_ids: list[int] | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> ProjectStatsBulkResponse:
    return await project_service.get_bulk_stats(
        db,
        include_archived=include_archived,
        project_ids=project_ids,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> ProjectResponse:
    project = await project_service.get_project(db, project_id)
    return ProjectResponse.model_validate(project)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_MANAGER)),
) -> ProjectResponse:
    project = await project_service.update_project(
        db, project_id, data, user_id=current_user.id
    )
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_ADMIN)),
) -> None:
    await project_service.delete_project(db, project_id, user_id=current_user.id)


@router.post("/{project_id}/restore", response_model=ProjectResponse)
async def restore_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_ADMIN)),
) -> ProjectResponse:
    project = await project_service.restore_project(
        db, project_id, user_id=current_user.id
    )
    return ProjectResponse.model_validate(project)


@router.get("/{project_id}/stats", response_model=ProjectStats)
async def get_project_stats(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> ProjectStats:
    return await project_service.get_stats(db, project_id)
