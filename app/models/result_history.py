from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.test_result import TestResult
    from app.models.user import User


class ResultHistory(Base):
    __tablename__ = "result_history"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    test_result_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("test_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    changed_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    test_result: Mapped[TestResult] = relationship(
        "TestResult", back_populates="history"
    )
    changer: Mapped[User | None] = relationship("User", foreign_keys=[changed_by])
