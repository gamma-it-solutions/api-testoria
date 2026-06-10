from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.defect import (
    DefectResponse,
    GitHubDefectCreate,
    GitLabDefectCreate,
    JiraDefectCreate,
)
from app.services.defect_service import DefectService

router = APIRouter(prefix="/defects", tags=["defects"])

_TESTER = (UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)


@router.post("/jira", response_model=DefectResponse, status_code=201)
async def create_jira_defect(
    data: JiraDefectCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_TESTER)),
) -> DefectResponse:
    return await DefectService.create_jira(db, data)


@router.post("/github", response_model=DefectResponse, status_code=201)
async def create_github_defect(
    data: GitHubDefectCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_TESTER)),
) -> DefectResponse:
    return await DefectService.create_github(db, data)


@router.post("/gitlab", response_model=DefectResponse, status_code=201)
async def create_gitlab_defect(
    data: GitLabDefectCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_TESTER)),
) -> DefectResponse:
    return await DefectService.create_gitlab(db, data)
