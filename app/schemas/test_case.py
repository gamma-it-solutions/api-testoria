from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class TestStep(BaseModel):
    step: str
    expected: str


class TestCaseCreate(BaseModel):
    suite_id: int
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    preconditions: str | None = None
    steps: list[TestStep] = []
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    type: Literal["manual", "automated"] = "manual"
    status: Literal["draft", "active", "deprecated"] = "draft"
    tags: list[str] = []
    automation_id: str | None = Field(default=None, max_length=255)
    display_order: int | None = None

    @field_validator("automation_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> str | None:
        if isinstance(v, str) and v.strip() == "":
            return None
        return str(v) if v is not None else None


class TestCaseUpdate(BaseModel):
    suite_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    preconditions: str | None = None
    steps: list[TestStep] | None = None
    priority: Literal["low", "medium", "high", "critical"] | None = None
    type: Literal["manual", "automated"] | None = None
    status: Literal["draft", "active", "deprecated"] | None = None
    tags: list[str] | None = None
    automation_id: str | None = Field(default=None, max_length=255)
    display_order: int | None = None

    @field_validator("automation_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> str | None:
        if isinstance(v, str) and v.strip() == "":
            return None
        return str(v) if v is not None else None


class TestCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    suite_id: int
    title: str
    description: str | None
    preconditions: str | None
    steps: list[TestStep]
    priority: str
    type: str
    status: str
    tags: list[TagResponse]
    automation_id: str | None
    display_order: int | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_validator("steps", mode="before")
    @classmethod
    def coerce_steps(cls, v: Any) -> list[Any]:
        if isinstance(v, list):
            result: list[Any] = []
            for item in v:
                if isinstance(item, dict):
                    result.append(item)
                else:
                    result.append({"step": item.step, "expected": item.expected})
            return result
        return list(v)


class TestCaseListFilters(BaseModel):
    search: str | None = None
    suite_id: int | None = None
    priority: Literal["low", "medium", "high", "critical"] | None = None
    type: Literal["manual", "automated"] | None = None
    status: Literal["draft", "active", "deprecated"] | None = None
    tag_ids: list[int] | None = None
    automation_id: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ImportResult(BaseModel):
    created: int
    errors: list[dict[str, Any]]
