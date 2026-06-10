from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import settings
from app.core import storage

# "skipped" is retained in the Literal for one release as a compat shim — the
# service layer normalises it to "no_run" before persisting.
ResultStatus = Literal["passed", "failed", "blocked", "no_run", "skipped"]


class StepResult(BaseModel):
    index: int = Field(ge=0)
    status: ResultStatus
    comment: str | None = Field(default=None, max_length=1000)


class TestResultCreate(BaseModel):
    test_case_id: int
    status: ResultStatus = "no_run"
    comment: str | None = None
    message: str | None = None
    stack_trace: str | None = None
    execution_time: int | None = Field(default=None, ge=0)
    defects: list[Any] = []
    step_results: list[StepResult] | None = None


class TestResultUpdate(BaseModel):
    status: ResultStatus | None = None
    comment: str | None = None
    message: str | None = None
    stack_trace: str | None = None
    execution_time: int | None = Field(default=None, ge=0)
    defects: list[Any] | None = None
    step_results: list[StepResult] | None = None


class ResultAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    test_result_id: int
    uploaded_by: int | None
    filename: str
    file_size: int
    mime_type: str | None
    uploaded_at: datetime
    storage_backend: str = "s3"
    object_key: str = ""
    url: str = ""

    @model_validator(mode="after")
    def _populate_url(self) -> "ResultAttachmentResponse":
        if self.url:
            return self
        if self.storage_backend == "s3" and self.object_key:
            try:
                self.url = storage.generate_presigned_url_sync(
                    self.object_key, ttl_seconds=settings.S3_PRESIGN_TTL_SECONDS
                )
            except Exception:  # noqa: BLE001 — surface empty url; client can reload
                self.url = ""
        elif self.storage_backend == "local":
            self.url = f"/api/v1/files/legacy/{self.id}"
        return self


class BulkUploadFailure(BaseModel):
    filename: str
    reason: str


class ResultAttachmentBulkResponse(BaseModel):
    uploaded: list[ResultAttachmentResponse]
    failed: list[BulkUploadFailure]


class TestResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    test_run_id: int
    test_case_id: int
    tested_by: int | None
    status: str
    comment: str | None
    message: str | None
    stack_trace: str | None
    execution_time: int | None
    defects: list[Any]
    step_results: list[StepResult] | None
    tested_at: datetime
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    attachments: list[ResultAttachmentResponse] = Field(default_factory=list)


class TestResultHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    test_result_id: int
    changed_by: int | None
    status: str
    comment: str | None
    changed_at: datetime
