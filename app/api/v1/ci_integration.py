from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import UserRole
from app.database import get_db
from app.dependencies import require_role
from app.models.user import User
from app.services import ci_service

router = APIRouter(prefix="/ci", tags=["ci-integration"])

_TESTER = (UserRole.TESTER, UserRole.LEAD, UserRole.ADMIN)


@router.post("/webhooks")
async def receive_webhook(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(*_TESTER)),
) -> dict[str, str]:
    return await ci_service.process_webhook(db, payload)


@router.post("/results/bulk", status_code=200)
async def bulk_import_results(
    test_run_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(*_TESTER)),
) -> dict[str, int]:
    xml_content = await file.read()
    return await ci_service.import_junit_xml(
        db, test_run_id, xml_content, current_user.id
    )


@router.get("/runs/{run_id}/badge")
async def get_badge(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    svg = await ci_service.generate_badge(db, run_id)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-cache"},
    )
