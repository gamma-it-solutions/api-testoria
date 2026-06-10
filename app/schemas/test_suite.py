from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TestSuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    parent_suite_id: int | None = None
    display_order: int | None = None


class TestSuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    parent_suite_id: int | None = None
    display_order: int | None = None


class TestSuiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    parent_suite_id: int | None
    name: str
    description: str | None
    display_order: int | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
