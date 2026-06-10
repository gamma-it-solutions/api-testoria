from fastapi import APIRouter, Depends

from app.core.centrifugo import generate_connection_token, generate_subscription_token
from app.core.roles import UserRole
from app.dependencies import require_role
from app.models.user import User
from app.schemas.websocket import (
    ConnectionTokenResponse,
    SubscriptionTokensRequest,
    SubscriptionTokensResponse,
)

router = APIRouter(prefix="/websocket", tags=["websocket"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)


@router.get("/connection-token", response_model=ConnectionTokenResponse)
async def get_connection_token(
    current_user: User = Depends(require_role(*_VIEWER)),
) -> ConnectionTokenResponse:
    token = generate_connection_token(current_user.id, current_user.username)
    return ConnectionTokenResponse(token=token)


@router.post("/subscription-tokens", response_model=SubscriptionTokensResponse)
async def get_subscription_tokens(
    data: SubscriptionTokensRequest,
    current_user: User = Depends(require_role(*_VIEWER)),
) -> SubscriptionTokensResponse:
    tokens = {
        channel: generate_subscription_token(current_user.id, channel)
        for channel in data.channels
    }
    return SubscriptionTokensResponse(tokens=tokens)
