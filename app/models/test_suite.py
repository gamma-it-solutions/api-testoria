from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.test_case import TestCase


class TestSuite(Base, SoftDeleteMixin):
    __tablename__ = "test_suites"
    __test__ = False

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_suite_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("test_suites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
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

    project: Mapped[Project] = relationship("Project", back_populates="test_suites")
    parent_suite: Mapped[TestSuite | None] = relationship(
        "TestSuite",
        back_populates="child_suites",
        remote_side=[id],
        foreign_keys=[parent_suite_id],
    )
    child_suites: Mapped[list[TestSuite]] = relationship(
        "TestSuite",
        back_populates="parent_suite",
        foreign_keys=[parent_suite_id],
        passive_deletes=True,
    )
    test_cases: Mapped[list[TestCase]] = relationship(
        "TestCase",
        back_populates="suite",
        passive_deletes=True,
    )
