from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.result_attachment import ResultAttachment
    from app.models.result_history import ResultHistory
    from app.models.test_case import TestCase
    from app.models.test_run import TestRun
    from app.models.user import User


class TestResult(Base, SoftDeleteMixin):
    __tablename__ = "test_results"
    __test__ = False
    __table_args__ = (
        UniqueConstraint("test_run_id", "test_case_id", name="uq_result_run_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    test_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("test_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    test_case_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("test_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tested_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    stack_trace: Mapped[str | None] = mapped_column(Text)
    execution_time: Mapped[int | None] = mapped_column(Integer)
    defects: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    step_results: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    tested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    test_run: Mapped[TestRun] = relationship("TestRun", back_populates="results")
    test_case: Mapped[TestCase] = relationship("TestCase")
    tester: Mapped[User | None] = relationship("User", foreign_keys=[tested_by])
    attachments: Mapped[list[ResultAttachment]] = relationship(
        "ResultAttachment",
        back_populates="test_result",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    history: Mapped[list[ResultHistory]] = relationship(
        "ResultHistory",
        back_populates="test_result",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ResultHistory.changed_at",
    )
