from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.schemas.tag import TagCreate, TagResponse
from app.services import tag_service

router = APIRouter(prefix="/tags", tags=["tags"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_EDITOR = (UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)


@router.get("", response_model=list[TagResponse])
async def list_tags(
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> list[TagResponse]:
    if q:
        tags = await tag_service.search_tags(db, q, limit=limit)
    else:
        tags = await tag_service.list_tags(db, limit=limit)
    return [TagResponse.model_validate(t) for t in tags]


@router.post("", response_model=TagResponse)
async def create_tag(
    data: TagCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_EDITOR)),
) -> TagResponse:
    tag, created = await tag_service.create_tag(db, data.name)
    response.status_code = 201 if created else 200
    return TagResponse.model_validate(tag)
