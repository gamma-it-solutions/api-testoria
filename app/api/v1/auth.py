from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.database import get_db
from app.dependencies import Principal, get_current_user, get_principal
from app.models.mixins import not_deleted
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    MessageResponse,
    PrincipalResponse,
    ResetPasswordRequest,
    ResetTokenValidateResponse,
)
from app.schemas.token import Token
from app.schemas.user import UserResponse
from app.services import (
    audit_service,
    email_service,
    password_token_service,
    user_service,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class RefreshBody(BaseModel):
    refresh_token: str


@router.post("/login", response_model=Token, summary="Login with username and password")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Token:
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise UnauthorizedError("Incorrect username or password")
    if not user.is_active:
        raise BadRequestError("Inactive user account")

    await audit_service.log_action(
        db, user.id, "LOGIN", "User", user.id, request=request
    )

    token_data: dict[str, object] = {"sub": str(user.id)}
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/refresh", response_model=Token, summary="Refresh access token")
async def refresh_token(body: RefreshBody, db: AsyncSession = Depends(get_db)) -> Token:
    payload = decode_token(body.refresh_token, expected_type="refresh")
    user_id_raw = payload.get("sub")
    if user_id_raw is None:
        raise UnauthorizedError("Invalid token payload")

    result = await db.execute(select(User).where(User.id == int(str(user_id_raw))))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")

    token_data: dict[str, object] = {"sub": str(user.id)}
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.get("/me", response_model=UserResponse, summary="Get current user")
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.get(
    "/principal",
    response_model=PrincipalResponse,
    summary="Show what the presented credential is allowed to do",
)
async def get_principal_info(
    principal: Principal = Depends(get_principal),
) -> PrincipalResponse:
    return PrincipalResponse(
        user_id=principal.user.id,
        username=principal.user.username,
        account_role=principal.user.role,
        effective_role=str(principal.role),
        project_id=principal.project_id,
        via=principal.via,
    )


@router.post("/logout", summary="Logout current user")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    await audit_service.log_action(
        db, current_user.id, "LOGOUT", "User", current_user.id, request=request
    )
    return {"message": "Successfully logged out"}


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=202,
    summary="Request a password-reset link",
)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    # Always 202 with the same message — never reveal whether the address exists.
    result = await db.execute(
        select(User).where(
            User.email == body.email,
            not_deleted(User),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user is not None:
        await email_service.queue_password_reset(db, user)
        await audit_service.log_action(
            db, user.id, "PASSWORD_RESET_REQUESTED", "User", user.id, request=request
        )
    return MessageResponse(message="If the address exists, a reset link was sent.")


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password using a reset/invite token",
)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    consumed = await password_token_service.consume_token(body.token)
    if consumed is None:
        raise BadRequestError("Invalid or expired token")
    user_id, _purpose = consumed
    user = await user_service.set_password(db, user_id, body.new_password)
    await audit_service.log_action(
        db, user.id, "PASSWORD_RESET", "User", user.id, request=request
    )
    return MessageResponse(message="Password updated.")


@router.get(
    "/reset-password/validate",
    response_model=ResetTokenValidateResponse,
    summary="Check a reset/invite token without consuming it",
)
async def validate_reset_token(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> ResetTokenValidateResponse:
    peeked = await password_token_service.peek_token(token)
    if peeked is None:
        raise BadRequestError("Invalid or expired token")
    user_id, _purpose = peeked
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            not_deleted(User),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise BadRequestError("Invalid or expired token")
    return ResetTokenValidateResponse(valid=True, username=user.username)
