from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.roles import UserRole

T = TypeVar("T")


class UserCreate(BaseModel):
    # Invite-only: accounts are always created with an unusable random password;
    # the user sets one via the welcome set-password email link. There is no
    # password field at creation (single or bulk) — see plan 049.
    username: str
    email: EmailStr
    full_name: str | None = None
    role: UserRole = UserRole.LEAD


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=1)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class UserBulkCreate(BaseModel):
    users: Annotated[list[UserCreate], Field(max_length=100)]
    strict: bool = False


class BulkCreateError(BaseModel):
    index: int
    username: str | None = None
    email: str | None = None
    detail: str


class UserBulkCreateResult(BaseModel):
    created: int
    errors: list[BulkCreateError]


class UserListFilters(BaseModel):
    search: str | None = None
    status: Literal["active", "inactive"] | None = None
    role: UserRole | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class RoleResponse(BaseModel):
    slug: str
    label: str
    is_default: bool
    is_deletable: bool
    description: str


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int
