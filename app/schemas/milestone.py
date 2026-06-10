from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class MilestoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    target_date: date | None = None


class MilestoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    target_date: date | None = None
    is_completed: bool | None = None


class MilestoneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None
    target_date: date | None
    is_completed: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
