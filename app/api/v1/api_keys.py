from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import Principal, require_jwt
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreateResponse, ApiKeyResponse
from app.services import api_key_service, audit_service

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    data: ApiKeyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_jwt),
) -> ApiKeyCreateResponse:
    api_key, plaintext = await api_key_service.mint(db, data, principal.user)
    await audit_service.log_action(
        db, principal.user.id, "API_KEY_CREATE", "ApiKey", api_key.id, request=request
    )
    return ApiKeyCreateResponse(
        **ApiKeyResponse.model_validate(api_key).model_dump(), key=plaintext
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    user_id: int | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_jwt),
) -> list[ApiKeyResponse]:
    keys = await api_key_service.list_keys(
        db, principal.user, user_id=user_id, include_revoked=include_revoked
    )
    return [ApiKeyResponse.model_validate(k) for k in keys]


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_jwt),
) -> None:
    await api_key_service.revoke(db, key_id, principal.user)
    await audit_service.log_action(
        db, principal.user.id, "API_KEY_REVOKE", "ApiKey", key_id, request=request
    )
