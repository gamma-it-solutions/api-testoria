import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import storage
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.uploads import sanitize_filename
from app.models.mixins import not_deleted
from app.models.result_attachment import ResultAttachment
from app.models.result_history import ResultHistory
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun, test_run_test_cases
from app.models.test_suite import TestSuite
from app.schemas.test_result import StepResult, TestResultCreate, TestResultUpdate
from app.services import realtime_service, test_run_service

logger = logging.getLogger(__name__)


async def _get_run(db: AsyncSession, run_id: int) -> TestRun:
    result = await db.execute(
        select(TestRun).where(TestRun.id == run_id, not_deleted(TestRun))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"TestRun {run_id} not found")
    return run


async def _get_case(db: AsyncSession, case_id: int) -> TestCase:
    result = await db.execute(
        select(TestCase).where(TestCase.id == case_id, not_deleted(TestCase))
    )
    tc = result.scalar_one_or_none()
    if tc is None:
        raise NotFoundError(f"TestCase {case_id} not found")
    return tc


def _normalise_status(status: str) -> str:
    """Compat shim: "skipped" is accepted on input but always persisted as "no_run"."""
    return "no_run" if status == "skipped" else status


def _normalise_step_results(step_results: list[StepResult]) -> list[dict[str, object]]:
    return [
        {
            "index": sr.index,
            "status": _normalise_status(sr.status),
            "comment": sr.comment,
        }
        for sr in step_results
    ]


def _validate_step_results(
    case: TestCase, step_results: list[StepResult]
) -> None:
    num_steps = len(case.steps) if case.steps else 0
    seen: set[int] = set()
    for sr in step_results:
        if sr.index >= num_steps:
            raise BadRequestError(
                f"Step index {sr.index} out of range for test case {case.id} "
                f"which has {num_steps} steps"
            )
        if sr.index in seen:
            raise BadRequestError(f"Duplicate step index {sr.index}")
        seen.add(sr.index)


async def get_result(db: AsyncSession, result_id: int) -> TestResult:
    result = await db.execute(
        select(TestResult)
        .options(selectinload(TestResult.attachments))
        .where(TestResult.id == result_id, not_deleted(TestResult))
    )
    tr = result.scalar_one_or_none()
    if tr is None:
        raise NotFoundError(f"TestResult {result_id} not found")
    return tr


def run_scope_case_ids(run: TestRun) -> Select[tuple[int]]:
    """Subquery yielding `test_case_id`s currently in the run's scope.

    Mirrors the scope rules of `GET /test-runs/{id}/cases`:
    - explicit: rows in `test_run_test_cases`
    - auto: non-deleted cases whose suite belongs to the run's project
      (and `suite_id` when set).
    """
    if run.cases_mode == "explicit":
        return select(test_run_test_cases.c.test_case_id).where(
            test_run_test_cases.c.test_run_id == run.id
        )
    q = (
        select(TestCase.id)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(
            TestSuite.project_id == run.project_id,
            not_deleted(TestCase),
            not_deleted(TestSuite),
        )
    )
    if run.suite_id is not None:
        q = q.where(TestCase.suite_id == run.suite_id)
    return q


async def list_results(
    db: AsyncSession, run_id: int, include_orphans: bool = False
) -> list[TestResult]:
    run = await _get_run(db, run_id)
    query = (
        select(TestResult)
        .options(selectinload(TestResult.attachments))
        .where(TestResult.test_run_id == run_id, not_deleted(TestResult))
    )
    if not include_orphans:
        query = query.where(
            TestResult.test_case_id.in_(run_scope_case_ids(run))
        )
    result = await db.execute(query.order_by(TestResult.test_case_id))
    return list(result.scalars().all())


async def _record_history(
    db: AsyncSession,
    result_id: int,
    status: str,
    comment: str | None,
    user_id: int | None,
) -> None:
    entry = ResultHistory(
        test_result_id=result_id,
        status=status,
        comment=comment,
        changed_by=user_id,
    )
    db.add(entry)


def _should_record_history(
    *,
    created: bool,
    old_status: str | None,
    old_comment: str | None,
    new_status: str,
    new_comment: str | None,
) -> bool:
    if created:
        return True
    if old_status != new_status:
        return True
    return (old_comment or None) != (new_comment or None)


async def submit(
    db: AsyncSession,
    run_id: int,
    data: TestResultCreate,
    user_id: int,
) -> TestResult:
    await _get_run(db, run_id)
    case = await _get_case(db, data.test_case_id)

    if data.step_results is not None:
        _validate_step_results(case, data.step_results)

    payload = data.model_dump()
    payload["status"] = _normalise_status(payload["status"])
    if data.step_results is not None:
        payload["step_results"] = _normalise_step_results(data.step_results)

    existing_result = await db.execute(
        select(TestResult)
        .options(selectinload(TestResult.attachments))
        .where(
            TestResult.test_run_id == run_id,
            TestResult.test_case_id == data.test_case_id,
            not_deleted(TestResult),
        )
    )
    tr = existing_result.scalar_one_or_none()
    created = tr is None
    old_status = tr.status if tr is not None else None
    old_comment = tr.comment if tr is not None else None

    if tr is not None:
        for field, value in payload.items():
            if field == "test_case_id":
                continue
            setattr(tr, field, value)
        tr.tested_by = user_id
        tr.tested_at = datetime.now(UTC)
    else:
        tr = TestResult(
            test_run_id=run_id,
            tested_by=user_id,
            tested_at=datetime.now(UTC),
            **payload,
        )
        db.add(tr)

    await db.flush()
    meaningful_change = _should_record_history(
        created=created,
        old_status=old_status,
        old_comment=old_comment,
        new_status=tr.status,
        new_comment=tr.comment,
    )
    if meaningful_change:
        await _record_history(db, tr.id, tr.status, tr.comment, user_id)
        await db.flush()
    # Reload with attachments for the response.
    tr = await get_result(db, tr.id)

    if meaningful_change:
        await test_run_service.transition_to_active(db, run_id, user_id=user_id)

    run = await _get_run(db, run_id)
    await realtime_service.publish_result_update(
        run_id=run_id,
        project_id=run.project_id,
        result_data={
            "result_id": tr.id,
            "test_case_id": tr.test_case_id,
            "status": tr.status,
            "tested_by": tr.tested_by,
        },
    )

    return tr


async def submit_many(
    db: AsyncSession,
    run_id: int,
    items: list[TestResultCreate],
    user_id: int,
) -> tuple[int, dict[str, int]]:
    """Upsert many results in one pass. Returns `(submitted, status_counts)`.

    Same upsert and history semantics as `submit()` — it shares
    `_should_record_history` — but validates the run once, fetches every target
    case in one query, transitions the run at most once, and publishes a single
    aggregate event instead of one per result. `submit()` per result costs ~6
    queries plus a Centrifugo round-trip; a 2000-case CI import cannot pay that.
    """
    run = await _get_run(db, run_id)
    if not items:
        return 0, {}

    case_ids = {item.test_case_id for item in items}
    cases_result = await db.execute(
        select(TestCase).where(TestCase.id.in_(case_ids), not_deleted(TestCase))
    )
    cases = {c.id: c for c in cases_result.scalars().all()}
    missing = case_ids - cases.keys()
    if missing:
        raise NotFoundError(f"TestCase {sorted(missing)[0]} not found")

    existing_result = await db.execute(
        select(TestResult).where(
            TestResult.test_run_id == run_id,
            TestResult.test_case_id.in_(case_ids),
            not_deleted(TestResult),
        )
    )
    existing = {r.test_case_id: r for r in existing_result.scalars().all()}

    submitted = 0
    status_counts: dict[str, int] = {}
    any_meaningful_change = False

    for item in items:
        case = cases[item.test_case_id]
        if item.step_results is not None:
            _validate_step_results(case, item.step_results)

        payload = item.model_dump()
        payload["status"] = _normalise_status(payload["status"])
        if item.step_results is not None:
            payload["step_results"] = _normalise_step_results(item.step_results)

        tr = existing.get(item.test_case_id)
        created = tr is None
        old_status = tr.status if tr is not None else None
        old_comment = tr.comment if tr is not None else None

        if tr is not None:
            for field, value in payload.items():
                if field == "test_case_id":
                    continue
                setattr(tr, field, value)
            tr.tested_by = user_id
            tr.tested_at = datetime.now(UTC)
        else:
            tr = TestResult(
                test_run_id=run_id,
                tested_by=user_id,
                tested_at=datetime.now(UTC),
                **payload,
            )
            db.add(tr)
            existing[item.test_case_id] = tr

        await db.flush()

        if _should_record_history(
            created=created,
            old_status=old_status,
            old_comment=old_comment,
            new_status=tr.status,
            new_comment=tr.comment,
        ):
            await _record_history(db, tr.id, tr.status, tr.comment, user_id)
            any_meaningful_change = True

        submitted += 1
        status_counts[tr.status] = status_counts.get(tr.status, 0) + 1

    await db.flush()

    if any_meaningful_change:
        await test_run_service.transition_to_active(db, run_id, user_id=user_id)

    await realtime_service.publish_result_bulk(
        run_id=run_id,
        project_id=run.project_id,
        submitted=submitted,
        status_counts=status_counts,
    )

    return submitted, status_counts


async def update_result(
    db: AsyncSession,
    result_id: int,
    data: TestResultUpdate,
    user_id: int,
) -> TestResult:
    tr = await get_result(db, result_id)
    old_status = tr.status
    old_comment = tr.comment

    if data.step_results is not None:
        case = await _get_case(db, tr.test_case_id)
        _validate_step_results(case, data.step_results)

    payload = data.model_dump(exclude_unset=True)
    if "status" in payload and payload["status"] is not None:
        payload["status"] = _normalise_status(payload["status"])
    if "step_results" in payload and data.step_results is not None:
        payload["step_results"] = _normalise_step_results(data.step_results)

    for field, value in payload.items():
        setattr(tr, field, value)
    tr.tested_by = user_id
    tr.tested_at = datetime.now(UTC)

    await db.flush()

    meaningful_change = _should_record_history(
        created=False,
        old_status=old_status,
        old_comment=old_comment,
        new_status=tr.status,
        new_comment=tr.comment,
    )
    if meaningful_change:
        await _record_history(db, tr.id, tr.status, tr.comment, user_id)
        await db.flush()

    tr = await get_result(db, tr.id)

    if meaningful_change:
        await test_run_service.transition_to_active(
            db, tr.test_run_id, user_id=user_id
        )

    return tr


async def get_history(db: AsyncSession, result_id: int) -> list[ResultHistory]:
    await get_result(db, result_id)
    result = await db.execute(
        select(ResultHistory)
        .where(ResultHistory.test_result_id == result_id)
        .order_by(ResultHistory.changed_at.asc())
    )
    return list(result.scalars().all())


def _build_object_key(result_id: int, filename: str) -> str:
    safe = sanitize_filename(filename)
    return f"results/{result_id}/{uuid.uuid4().hex}-{safe}"


async def upload_attachment(
    db: AsyncSession,
    result_id: int,
    filename: str,
    content: bytes,
    mime_type: str | None,
    user_id: int,
) -> ResultAttachment:
    """Upload a single attachment to MinIO and persist the row."""
    await get_result(db, result_id)

    safe_name = sanitize_filename(filename)
    object_key = _build_object_key(result_id, safe_name)

    await storage.put_object(object_key, content, content_type=mime_type)

    attachment = ResultAttachment(
        test_result_id=result_id,
        filename=safe_name,
        object_key=object_key,
        storage_backend="s3",
        file_size=len(content),
        mime_type=mime_type,
        uploaded_by=user_id,
    )
    db.add(attachment)
    try:
        await db.flush()
        await db.refresh(attachment)
    except Exception:
        # Compensating delete so MinIO doesn't accumulate orphans if the DB write fails.
        try:
            await storage.delete_object(object_key)
        except Exception as cleanup_err:  # noqa: BLE001
            logger.warning(
                "Failed to clean up MinIO object after DB insert failure: %s (%s)",
                object_key,
                cleanup_err,
            )
        raise
    return attachment


async def upload_attachments_bulk(
    db: AsyncSession,
    result_id: int,
    files: list[tuple[str, bytes, str | None]],
    user_id: int,
) -> tuple[list[ResultAttachment], list[tuple[str, str]]]:
    """Upload many attachments; return (committed, failures).

    `files` is a list of `(filename, content, mime_type)` triples.
    Failures is a list of `(filename, reason)` pairs.
    """
    await get_result(db, result_id)
    uploaded: list[ResultAttachment] = []
    failed: list[tuple[str, str]] = []
    for filename, content, mime_type in files:
        try:
            att = await upload_attachment(
                db=db,
                result_id=result_id,
                filename=filename,
                content=content,
                mime_type=mime_type,
                user_id=user_id,
            )
            uploaded.append(att)
        except Exception as err:  # noqa: BLE001 — per-file quarantine
            logger.warning("Bulk upload failed for %s: %s", filename, err)
            failed.append((filename, str(err)))
    return uploaded, failed


async def delete_attachment(db: AsyncSession, result_id: int, attach_id: int) -> None:
    result = await db.execute(
        select(ResultAttachment).where(ResultAttachment.id == attach_id)
    )
    attachment = result.scalar_one_or_none()
    if attachment is None or attachment.test_result_id != result_id:
        raise NotFoundError(f"Attachment {attach_id} not found")

    if attachment.storage_backend == "s3":
        try:
            await storage.delete_object(attachment.object_key)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "MinIO delete failed for %s: %s — DB row kept for operator review",
                attachment.object_key,
                err,
            )
            raise
    else:
        file_path = Path(attachment.object_key)
        if file_path.exists():
            file_path.unlink()
        else:
            logger.warning("Attachment file missing from disk: %s", file_path)

    await db.delete(attachment)
    await db.flush()


async def get_attachment(db: AsyncSession, attach_id: int) -> ResultAttachment | None:
    result = await db.execute(
        select(ResultAttachment).where(ResultAttachment.id == attach_id)
    )
    return result.scalar_one_or_none()
