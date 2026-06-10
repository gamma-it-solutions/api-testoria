from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.test_result import TestResult
    from app.models.user import User


class ResultAttachment(Base):
    __tablename__ = "result_attachments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    test_result_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("test_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_backend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="s3", server_default="s3"
    )
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    test_result: Mapped[TestResult] = relationship(
        "TestResult", back_populates="attachments"
    )
    uploader: Mapped[User | None] = relationship("User", foreign_keys=[uploaded_by])
