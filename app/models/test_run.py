from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.milestone import Milestone
    from app.models.project import Project
    from app.models.test_case import TestCase
    from app.models.test_result import TestResult
    from app.models.test_suite import TestSuite
    from app.models.user import User

test_run_test_cases = Table(
    "test_run_test_cases",
    Base.metadata,
    Column(
        "test_run_id",
        Integer,
        ForeignKey("test_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "test_case_id",
        Integer,
        ForeignKey("test_cases.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class TestRun(Base, SoftDeleteMixin):
    __tablename__ = "test_runs"
    __test__ = False
    __table_args__ = (
        CheckConstraint(
            "cases_mode IN ('auto', 'explicit')", name="ck_test_runs_cases_mode"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    suite_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("test_suites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    milestone_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("milestones.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assigned_to: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="planned")
    cases_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="auto", default="auto"
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    project: Mapped[Project] = relationship("Project")
    suite: Mapped[TestSuite | None] = relationship("TestSuite")
    milestone: Mapped[Milestone | None] = relationship(
        "Milestone", back_populates="test_runs"
    )
    assigned_user: Mapped[User | None] = relationship(
        "User", foreign_keys=[assigned_to]
    )
    results: Mapped[list[TestResult]] = relationship(
        "TestResult",
        back_populates="test_run",
        passive_deletes=True,
    )
    test_cases: Mapped[list[TestCase]] = relationship(
        "TestCase",
        secondary=test_run_test_cases,
        lazy="selectin",
    )
