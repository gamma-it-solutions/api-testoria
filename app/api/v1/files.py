"""Legacy read-only shim for attachments still stored on local disk.

Removed once every `result_attachments` row has `storage_backend='s3'`.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.services import test_result_service

router = APIRouter(tags=["files"])

_VIEWER = (UserRole.READ_ONLY, UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)


@router.get("/files/legacy/{attachment_id}")
async def get_legacy_attachment(
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_VIEWER)),
) -> FileResponse:
    attachment = await test_result_service.get_attachment(db, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.storage_backend != "local":
        # Row has been migrated to MinIO — the presigned URL on the JSON
        # response is the correct path; the shim must not serve it.
        raise HTTPException(status_code=410, detail="Moved to object storage")
    path = Path(attachment.object_key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing from disk")
    return FileResponse(
        path,
        media_type=attachment.mime_type or "application/octet-stream",
        filename=attachment.filename,
    )
