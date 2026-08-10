from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import ForbiddenError
from app.core.roles import UserRole
from app.core.uploads import validate_image_upload, validate_upload_size
from app.database import get_db
from app.dependencies import Principal, get_principal, require_role
from app.models.user import User
from app.schemas.test_result import (
    BulkUploadFailure,
    ResultAttachmentBulkResponse,
    ResultAttachmentResponse,
    ResultImportReport,
    TestResultCreate,
    TestResultHistoryResponse,
    TestResultResponse,
    TestResultUpdate,
)
from app.services import result_import_service, test_result_service, test_run_service

router = APIRouter(tags=["test-results"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)
_TESTER = (UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)


@router.get(
    "/test-runs/{run_id}/results",
    response_model=list[TestResultResponse],
)
async def list_results(
    run_id: int,
    include_orphans: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> list[TestResultResponse]:
    results = await test_result_service.list_results(
        db, run_id, include_orphans=include_orphans
    )
    return [TestResultResponse.model_validate(r) for r in results]


@router.post(
    "/test-runs/{run_id}/results",
    response_model=TestResultResponse,
    status_code=201,
)
async def submit_result(
    run_id: int,
    data: TestResultCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_TESTER)),
) -> TestResultResponse:
    tr = await test_result_service.submit(db, run_id, data, current_user.id)
    return TestResultResponse.model_validate(tr)


@router.post(
    "/test-runs/{run_id}/results/import",
    response_model=ResultImportReport,
    summary="Import results from a JUnit XML or JSON report",
)
async def import_results(
    run_id: int,
    file: UploadFile = File(...),
    format: str = Form(default="auto"),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ResultImportReport:
    if principal.role not in _TESTER:
        raise ForbiddenError()
    await _assert_run_in_key_scope(db, run_id, principal)
    content = await validate_upload_size(file)
    return await result_import_service.import_results(
        db,
        run_id,
        content,
        principal.user.id,
        filename=file.filename,
        fmt=format,
    )


async def _assert_run_in_key_scope(
    db: AsyncSession, run_id: int, principal: Principal
) -> None:
    """A project-scoped API key may only write to runs in that project.

    Scope is a write guard on the CLI-facing routes, not a general read ACL —
    see `docs/02-architecture/backend/auth.md`.
    """
    if principal.project_id is None:
        return
    run = await test_run_service.get_run(db, run_id)
    if run.project_id != principal.project_id:
        raise ForbiddenError(
            f"This API key is scoped to project {principal.project_id}"
        )


@router.get("/test-results/{result_id}", response_model=TestResultResponse)
async def get_result(
    result_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> TestResultResponse:
    tr = await test_result_service.get_result(db, result_id)
    return TestResultResponse.model_validate(tr)


@router.put("/test-results/{result_id}", response_model=TestResultResponse)
async def update_result(
    result_id: int,
    data: TestResultUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_TESTER)),
) -> TestResultResponse:
    tr = await test_result_service.update_result(db, result_id, data, current_user.id)
    return TestResultResponse.model_validate(tr)


@router.get(
    "/test-results/{result_id}/history",
    response_model=list[TestResultHistoryResponse],
)
async def get_history(
    result_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> list[TestResultHistoryResponse]:
    history = await test_result_service.get_history(db, result_id)
    return [TestResultHistoryResponse.model_validate(h) for h in history]


@router.post(
    "/test-results/{result_id}/attachments",
    response_model=ResultAttachmentResponse,
    status_code=201,
)
async def upload_attachment(
    result_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_TESTER)),
) -> ResultAttachmentResponse:
    # Single-file endpoint stays permissive — size-only check; used for
    # screenshots and for generic attachments (logs, traces).
    content = await validate_upload_size(file)
    attachment = await test_result_service.upload_attachment(
        db=db,
        result_id=result_id,
        filename=file.filename or "upload",
        content=content,
        mime_type=file.content_type,
        user_id=current_user.id,
    )
    return ResultAttachmentResponse.model_validate(attachment)


@router.post(
    "/test-results/{result_id}/attachments/bulk",
    response_model=ResultAttachmentBulkResponse,
    status_code=201,
)
async def upload_attachments_bulk(
    result_id: int,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_TESTER)),
) -> ResultAttachmentBulkResponse:
    if len(files) == 0:
        raise HTTPException(status_code=400, detail="At least one file required")
    if len(files) > settings.MAX_ATTACHMENTS_PER_BULK:
        raise HTTPException(
            status_code=400,
            detail=(
                f"At most {settings.MAX_ATTACHMENTS_PER_BULK} files per bulk upload"
            ),
        )

    prepared: list[tuple[str, bytes, str | None]] = []
    failed: list[BulkUploadFailure] = []
    for file in files:
        name = file.filename or "upload"
        try:
            content = await validate_image_upload(file)
        except HTTPException as err:
            failed.append(BulkUploadFailure(filename=name, reason=str(err.detail)))
            continue
        prepared.append((name, content, file.content_type))

    uploaded, service_failures = await test_result_service.upload_attachments_bulk(
        db=db,
        result_id=result_id,
        files=prepared,
        user_id=current_user.id,
    )
    failed.extend(
        BulkUploadFailure(filename=name, reason=reason)
        for name, reason in service_failures
    )
    return ResultAttachmentBulkResponse(
        uploaded=[ResultAttachmentResponse.model_validate(a) for a in uploaded],
        failed=failed,
    )


@router.delete(
    "/test-results/{result_id}/attachments/{attach_id}",
    status_code=204,
)
async def delete_attachment(
    result_id: int,
    attach_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_TESTER)),
) -> None:
    await test_result_service.delete_attachment(db, result_id, attach_id)
