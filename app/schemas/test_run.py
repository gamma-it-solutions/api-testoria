from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.schemas.test_result import TestResultResponse
from app.utils import stats

# Canonical lifecycle statuses for a test run. `in_progress` is the legacy
# name for `active` — accepted on input for one release cycle and normalised
# to `active` before persistence / response. Tracked as tech debt in
# `docs/04-execution/tech-debt.md` for removal.
TestRunStatus = Literal["planned", "active", "completed", "aborted"]
_TestRunStatusInput = Literal[
    "planned", "active", "in_progress", "completed", "aborted"
]


def _normalise_run_status(value: str) -> str:
    return "active" if value == "in_progress" else value


class TestRunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    suite_id: int | None = None
    milestone_id: int | None = None
    assigned_to: int | None = None
    config: dict[str, Any] = {}
    include_test_cases: list[int] | None = None


class TestRunUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    suite_id: int | None = None
    milestone_id: int | None = None
    assigned_to: int | None = None
    status: _TestRunStatusInput | None = None
    config: dict[str, Any] | None = None

    @field_validator("status")
    @classmethod
    def _coerce_status(cls, value: str | None) -> str | None:
        return None if value is None else _normalise_run_status(value)


class TestRunProgress(BaseModel):
    passed: int
    failed: int
    blocked: int
    # `no_run` includes both explicit `no_run` result rows and cases with no
    # result yet. The old separate `untested` field is folded in here.
    no_run: int
    total: int
    # In-memory value is the raw ratio so callers that aggregate (e.g.
    # report_service.overall_pass_rate) can compute a mean without rounding
    # drift. The wire value is rounded to 3 decimals (= 1 decimal of percent)
    # via the field serializer below — see plan 044.
    pass_rate: float | None = Field(default=None, ge=0, le=1)

    @field_serializer("pass_rate")
    def _round_pass_rate(self, value: float | None) -> float | None:
        return stats.round_ratio(value)


class TestRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    suite_id: int | None
    milestone_id: int | None
    assigned_to: int | None
    name: str
    status: str
    cases_mode: Literal["auto", "explicit"]
    config: dict[str, Any]
    progress: TestRunProgress | None = None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class TestCaseWithResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    suite_id: int
    title: str
    description: str | None
    preconditions: str | None
    steps: list[Any]
    priority: str
    type: str
    # `status` is the case's own workflow state; retained for back-compat.
    # `case_status` is the new, unambiguous field (vs `result.status`).
    # Remove `status` after one-release deprecation window.
    status: str
    case_status: str
    automation_id: str | None = None
    tags: list[str]
    result: TestResultResponse | None = None


class TestRunCasesUpdate(BaseModel):
    test_case_ids: list[int] = Field(max_length=1000)


class TestRunWithCases(BaseModel):
    run: TestRunResponse
    cases: list[TestCaseWithResult]
    total: int


class SuiteProgress(BaseModel):
    total: int
    passed: int
    failed: int
    blocked: int
    no_run: int
    other: int


class SuiteNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None


class TestRunSuiteNode(BaseModel):
    suite: SuiteNode
    progress: SuiteProgress
    cases: list[TestCaseWithResult]
    children: list["TestRunSuiteNode"] = []


class TestRunSuiteTree(BaseModel):
    run: TestRunResponse
    roots: list[TestRunSuiteNode]
    total: int
