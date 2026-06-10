from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Status values the drain worker moves a row through.
STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"


class EmailOutbox(Base):
    """Durable email queue.

    Rows are written in the *same* DB transaction as the action that triggers
    the email (e.g. user creation), so an email exists iff that action commits.
    The in-process drain worker (`app/core/email_worker.py`) claims `pending`
    rows over a reused, paced SMTP connection and retries with backoff.
    """

    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    # Template stem under app/templates/email/ (e.g. "welcome_invite").
    template: Mapped[str] = mapped_column(String(100), nullable=False)
    # Render context (link, username, …). JSONB on Postgres, JSON on SQLite.
    context: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
