from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.core.email import EmailSender, render_email
from app.database import AsyncSessionLocal
from app.services import email_outbox_service

logger = logging.getLogger(__name__)


class EmailWorker:
    """In-process outbox drain loop, started/stopped from the FastAPI lifespan.

    Every `EMAIL_OUTBOX_POLL_SECONDS` it claims a batch of pending rows, opens
    one SMTP connection, renders + sends each (paced by `EMAIL_SEND_PACE_MS`),
    and records per-row outcomes. State lives in Postgres, so it is restart-safe
    and correct across multiple workers via `FOR UPDATE SKIP LOCKED`.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        # Recover rows a previous run left mid-send.
        try:
            async with AsyncSessionLocal() as db:
                requeued = await email_outbox_service.requeue_orphaned_sending(db)
                await db.commit()
            if requeued:
                logger.info(
                    "email worker requeued %d orphaned 'sending' rows", requeued
                )
        except Exception:
            logger.exception("email worker startup requeue failed")

        while True:
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("email drain loop iteration failed")
            await asyncio.sleep(settings.EMAIL_OUTBOX_POLL_SECONDS)

    async def _drain_once(self) -> None:
        async with AsyncSessionLocal() as db:
            # Claim the batch and commit the claim so the rows are durably
            # marked 'sending' (other workers skip them) and the locks release
            # before we start the slow SMTP sends. expire_on_commit=False keeps
            # the row objects usable on the same session after this commit.
            rows = await email_outbox_service.claim_batch(
                db, settings.EMAIL_OUTBOX_BATCH_SIZE
            )
            await db.commit()
            if not rows:
                return

            logger.info("email worker claimed %d row(s)", len(rows))
            pace = settings.EMAIL_SEND_PACE_MS / 1000.0

            # Send each over one connection, recording each outcome immediately.
            async with EmailSender() as sender:
                for idx, row in enumerate(rows):
                    try:
                        html, text = render_email(row.template, dict(row.context))
                        await sender.send(row.to_email, row.subject, html, text)
                        await email_outbox_service.mark_sent(db, row)
                    except Exception as exc:  # noqa: BLE001 — one bad row must not kill the batch
                        logger.warning(
                            "email send failed id=%s to=%s: %s",
                            row.id,
                            row.to_email,
                            exc,
                        )
                        await email_outbox_service.mark_failed(db, row, str(exc))
                    await db.commit()
                    if pace and idx < len(rows) - 1:
                        await asyncio.sleep(pace)
