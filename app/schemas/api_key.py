from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.roles import UserRole


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_id: int | None = None
    role: UserRole = UserRole.TESTER
    # Absent -> API_KEY_DEFAULT_TTL_DAYS from now. Explicit `null` -> never expires,
    # which has to be asked for rather than fallen into.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    never_expires: bool = False
    # Lead/admin only — mint on behalf of another user. Ignored for everyone else.
    user_id: int | None = None


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    key_prefix: str
    user_id: int
    project_id: int | None
    role: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreateResponse(ApiKeyResponse):
    """The only schema that ever carries the secret. Returned once, at mint."""

    key: str
