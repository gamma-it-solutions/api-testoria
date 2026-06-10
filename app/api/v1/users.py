import math
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import ROLE_METADATA, UserRole
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.user import User
from app.schemas.user import (
    PaginatedResponse,
    RoleResponse,
    UserBulkCreate,
    UserBulkCreateResult,
    UserCreate,
    UserListFilters,
    UserResponse,
    UserUpdate,
)
from app.services import user_service

users_router = APIRouter(prefix="/users", tags=["users"])
roles_router = APIRouter(prefix="/roles", tags=["roles"])


@users_router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    search: str | None = Query(default=None),
    status: Literal["active", "inactive"] | None = Query(default=None),
    role: UserRole | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.LEAD, UserRole.ADMIN)),
) -> PaginatedResponse[UserResponse]:
    filters = UserListFilters(
        search=search, status=status, role=role, page=page, page_size=page_size
    )
    users, total = await user_service.list_users(db, filters)
    pages = math.ceil(total / page_size) if page_size else 1
    return PaginatedResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@users_router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEAD, UserRole.ADMIN)),
) -> UserResponse:
    user = await user_service.create_user(db, data, actor=current_user)
    return UserResponse.model_validate(user)


@users_router.post("/bulk", response_model=UserBulkCreateResult)
async def bulk_create_users(
    data: UserBulkCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEAD, UserRole.ADMIN)),
) -> UserBulkCreateResult:
    return await user_service.bulk_create_users(db, data, actor=current_user)


@users_router.get("/export")
async def export_users(
    format: Literal["csv", "excel"] = Query(default="csv"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.LEAD, UserRole.ADMIN)),
) -> Response:
    if format == "csv":
        return StreamingResponse(
            user_service.export_users_csv(db),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users.csv"},
        )
    data = await user_service.export_users_excel(db)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=users.xlsx"},
    )


@users_router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(UserRole.LEAD, UserRole.ADMIN)),
) -> UserResponse:
    user = await user_service.get_user(db, user_id)
    return UserResponse.model_validate(user)


@users_router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEAD, UserRole.ADMIN)),
) -> UserResponse:
    user = await user_service.update_user(db, user_id, data, actor=current_user)
    return UserResponse.model_validate(user)


@users_router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEAD, UserRole.ADMIN)),
) -> None:
    await user_service.delete_user(db, user_id, actor=current_user)


@roles_router.get("", response_model=list[RoleResponse])
async def list_roles(
    _: User = Depends(get_current_user),
) -> list[RoleResponse]:
    return [
        RoleResponse(
            slug=role,
            label=str(meta["label"]),
            is_default=bool(meta["is_default"]),
            is_deletable=bool(meta["is_deletable"]),
            description=str(meta["description"]),
        )
        for role, meta in ROLE_METADATA.items()
    ]
