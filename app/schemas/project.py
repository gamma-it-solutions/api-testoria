from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    is_archived: bool | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ProjectStats(BaseModel):
    total_test_cases: int
    total_test_suites: int
    total_test_runs: int
    pass_rate: float | None = Field(default=None, ge=0, le=1)


class ProjectStatsItem(BaseModel):
    project_id: int
    name: str
    is_archived: bool
    total_test_cases: int
    total_test_suites: int
    total_test_runs: int
    active_runs: int
    pass_rate: float | None = Field(default=None, ge=0, le=1)


class ProjectStatsBulkResponse(BaseModel):
    items: list[ProjectStatsItem]
    total: int
