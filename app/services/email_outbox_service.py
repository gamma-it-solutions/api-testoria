from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.email_outbox import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    EmailOutbox,
)

# Exponential backoff between send attempts. attempts=1 → 1m, 2 → 2m, 4 → 8m …
_BACKOFF_BASE_SECONDS = 60
_BACKOFF_CAP_SECONDS = 3600


def _backoff_seconds(attempts: int) -> int:
    """Seconds to wait before the next attempt after `attempts` failures."""
    delay = _BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0))
    return min(int(delay), _BACKOFF_CAP_SECONDS)


async def enqueue(
    db: AsyncSession,
    to_email: str,
    template: str,
    context: dict[str, Any],
    subject: str,
    max_attempts: int | None = None,
) -> EmailOutbox:
    """Write a `pending` outbox row. Does NOT commit — joins the caller's
    transaction, so the email exists iff the surrounding action commits."""
    row = EmailOutbox(
        to_email=to_email,
        subject=subject,
        template=template,
        context=context,
        status=STATUS_PENDING,
        attempts=0,
        max_attempts=max_attempts or settings.EMAIL_MAX_ATTEMPTS,
        next_attempt_at=func.now(),
    )
    db.add(row)
    await db.flush()
    return row


async def claim_batch(db: AsyncSession, limit: int) -> list[EmailOutbox]:
    """Atomically claim up to `limit` ready rows, flipping them to `sending`.

    Uses `FOR UPDATE SKIP LOCKED` so concurrent workers (multiple uvicorn
    workers / API replicas) never claim the same row. Does not commit; the
    caller commits to make the claim durable before sending.
    """
    result = await db.execute(
        select(EmailOutbox)
        .where(
            EmailOutbox.status == STATUS_PENDING,
            EmailOutbox.next_attempt_at <= func.now(),
        )
        .order_by(EmailOutbox.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(result.scalars().all())
    for row in rows:
        row.status = STATUS_SENDING
    await db.flush()
    return rows


async def mark_sent(db: AsyncSession, row: EmailOutbox) -> None:
    row.status = STATUS_SENT
    row.sent_at = datetime.now(UTC)
    row.last_error = None
    await db.flush()


async def mark_failed(db: AsyncSession, row: EmailOutbox, error: str) -> None:
    """Record a failed send: bump attempts, keep the error, and either retry
    with backoff or give up (→ `failed`) once `max_attempts` is reached."""
    row.attempts += 1
    row.last_error = error[:1000]
    if row.attempts >= row.max_attempts:
        row.status = STATUS_FAILED
    else:
        row.status = STATUS_PENDING
        row.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=_backoff_seconds(row.attempts)
        )
    await db.flush()


async def requeue_orphaned_sending(db: AsyncSession) -> int:
    """Reset any rows left in `sending` (a worker crashed mid-send) back to
    `pending` so they are retried. Called once at worker startup."""
    result = await db.execute(
        update(EmailOutbox)
        .where(EmailOutbox.status == STATUS_SENDING)
        .values(status=STATUS_PENDING)
    )
    await db.flush()
    return result.rowcount or 0
