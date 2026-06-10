from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_outbox import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    EmailOutbox,
)
from app.services import email_outbox_service


async def _enqueue(db: AsyncSession, **overrides: object) -> EmailOutbox:
    kwargs: dict[str, object] = {
        "to_email": "a@b.com",
        "template": "welcome_invite",
        "context": {"link": "http://x/set-password?token=t"},
        "subject": "Hi",
    }
    kwargs.update(overrides)
    return await email_outbox_service.enqueue(db, **kwargs)  # type: ignore[arg-type]


# --- enqueue ---


@pytest.mark.asyncio
async def test_enqueue_creates_pending_row(db_session: AsyncSession) -> None:
    row = await _enqueue(db_session)
    assert row.id is not None
    assert row.status == STATUS_PENDING
    assert row.attempts == 0
    assert row.context["link"].endswith("token=t")


@pytest.mark.asyncio
async def test_enqueue_does_not_commit(db_session: AsyncSession) -> None:
    # enqueue only flushes; the row is visible in-session but not committed.
    await _enqueue(db_session)
    assert db_session.in_transaction()


# --- claim_batch ---


@pytest.mark.asyncio
async def test_claim_batch_flips_to_sending(db_session: AsyncSession) -> None:
    await _enqueue(db_session, to_email="one@b.com")
    await _enqueue(db_session, to_email="two@b.com")

    claimed = await email_outbox_service.claim_batch(db_session, limit=10)
    assert len(claimed) == 2
    assert all(r.status == STATUS_SENDING for r in claimed)


@pytest.mark.asyncio
async def test_claim_batch_respects_limit(db_session: AsyncSession) -> None:
    for i in range(3):
        await _enqueue(db_session, to_email=f"u{i}@b.com")
    claimed = await email_outbox_service.claim_batch(db_session, limit=2)
    assert len(claimed) == 2


@pytest.mark.asyncio
async def test_reclaim_skips_already_claimed(db_session: AsyncSession) -> None:
    # A row already in 'sending' is not picked up again (idempotent re-claim).
    await _enqueue(db_session)
    first = await email_outbox_service.claim_batch(db_session, limit=10)
    assert len(first) == 1
    second = await email_outbox_service.claim_batch(db_session, limit=10)
    assert second == []


@pytest.mark.asyncio
async def test_claim_skips_future_next_attempt(db_session: AsyncSession) -> None:
    row = await _enqueue(db_session)
    # Push it into the future (as mark_failed backoff would).
    row.next_attempt_at = datetime(2999, 1, 1, tzinfo=UTC)
    await db_session.flush()
    claimed = await email_outbox_service.claim_batch(db_session, limit=10)
    assert claimed == []


# --- mark_sent / mark_failed ---


@pytest.mark.asyncio
async def test_mark_sent(db_session: AsyncSession) -> None:
    row = await _enqueue(db_session)
    await email_outbox_service.mark_sent(db_session, row)
    assert row.status == STATUS_SENT
    assert row.sent_at is not None
    assert row.last_error is None


@pytest.mark.asyncio
async def test_mark_failed_retries_with_backoff(db_session: AsyncSession) -> None:
    row = await _enqueue(db_session, max_attempts=3)
    before = datetime.now(UTC)
    await email_outbox_service.mark_failed(db_session, row, "smtp boom")

    assert row.status == STATUS_PENDING
    assert row.attempts == 1
    assert row.last_error == "smtp boom"
    assert row.next_attempt_at > before


@pytest.mark.asyncio
async def test_mark_failed_gives_up_at_max_attempts(db_session: AsyncSession) -> None:
    row = await _enqueue(db_session, max_attempts=2)
    await email_outbox_service.mark_failed(db_session, row, "err1")
    assert row.status == STATUS_PENDING
    await email_outbox_service.mark_failed(db_session, row, "err2")
    assert row.status == STATUS_FAILED
    assert row.attempts == 2
    assert row.last_error == "err2"


@pytest.mark.asyncio
async def test_mark_failed_truncates_long_error(db_session: AsyncSession) -> None:
    row = await _enqueue(db_session, max_attempts=5)
    await email_outbox_service.mark_failed(db_session, row, "x" * 5000)
    assert row.last_error is not None
    assert len(row.last_error) == 1000


# --- backoff schedule ---


def test_backoff_schedule_is_exponential() -> None:
    assert email_outbox_service._backoff_seconds(1) == 60
    assert email_outbox_service._backoff_seconds(2) == 120
    assert email_outbox_service._backoff_seconds(3) == 240
    assert email_outbox_service._backoff_seconds(4) == 480


def test_backoff_is_capped() -> None:
    assert email_outbox_service._backoff_seconds(99) == 3600


# --- requeue_orphaned_sending ---


@pytest.mark.asyncio
async def test_requeue_orphaned_sending(db_session: AsyncSession) -> None:
    await _enqueue(db_session)
    claimed = await email_outbox_service.claim_batch(db_session, limit=10)
    assert claimed[0].status == STATUS_SENDING

    n = await email_outbox_service.requeue_orphaned_sending(db_session)
    assert n == 1

    result = await db_session.execute(select(EmailOutbox))
    rows = list(result.scalars().all())
    assert all(r.status == STATUS_PENDING for r in rows)
