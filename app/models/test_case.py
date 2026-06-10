from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import SoftDeleteMixin
from app.models.tag import test_case_tags

if TYPE_CHECKING:
    from app.models.tag import Tag
    from app.models.test_suite import TestSuite


class TestCase(Base, SoftDeleteMixin):
    __tablename__ = "test_cases"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    suite_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("test_suites.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    preconditions: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    priority: Mapped[str] = mapped_column(String(50), nullable=False, default="medium")
    type: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    automation_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    display_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    suite: Mapped[TestSuite] = relationship("TestSuite", back_populates="test_cases")
    tags: Mapped[list[Tag]] = relationship(
        "Tag",
        secondary=test_case_tags,
        back_populates="test_cases",
        lazy="selectin",
    )
