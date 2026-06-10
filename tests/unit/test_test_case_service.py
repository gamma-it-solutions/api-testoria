"""Tests for `test_case_service` ordering (TES-69 / plan-046)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_suite import TestSuite
from app.schemas.test_case import TestCaseListFilters, TestCaseUpdate
from app.services import test_case_service


async def _seed_project(db: AsyncSession, name: str = "Order Project") -> Project:
    project = Project(name=name)
    db.add(project)
    await db.flush()
    return project


async def _seed_suite(db: AsyncSession, project: Project, name: str) -> TestSuite:
    suite = TestSuite(project_id=project.id, name=name)
    db.add(suite)
    await db.flush()
    return suite


async def _seed_case(
    db: AsyncSession,
    suite: TestSuite,
    title: str,
    display_order: int | None = None,
) -> TestCase:
    case = TestCase(
        suite_id=suite.id,
        title=title,
        priority="medium",
        type="manual",
        display_order=display_order,
    )
    db.add(case)
    await db.flush()
    return case


async def test_list_cases_sorts_by_display_order_ascending(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project, "S")
    # Seed out of order to confirm DB-side sort
    c30 = await _seed_case(db_session, suite, "Third", display_order=30)
    c10 = await _seed_case(db_session, suite, "First", display_order=10)
    c20 = await _seed_case(db_session, suite, "Second", display_order=20)

    cases, _ = await test_case_service.list_test_cases(
        db_session, project.id, TestCaseListFilters(suite_id=suite.id)
    )

    assert [c.id for c in cases] == [c10.id, c20.id, c30.id]


async def test_list_cases_sorts_null_display_order_last(
    db_session: AsyncSession,
) -> None:
    """NULL display_order should sort last so legacy rows fall behind reordered
    ones — matches the (NULLS LAST, created_at, id) contract used for suites."""
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project, "S")
    c_null = await _seed_case(db_session, suite, "Legacy", display_order=None)
    c_ordered = await _seed_case(db_session, suite, "Reordered", display_order=5)

    cases, _ = await test_case_service.list_test_cases(
        db_session, project.id, TestCaseListFilters(suite_id=suite.id)
    )

    assert [c.id for c in cases] == [c_ordered.id, c_null.id]


async def test_update_test_case_persists_display_order(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project, "S")
    case = await _seed_case(db_session, suite, "Subject", display_order=100)

    updated = await test_case_service.update_test_case(
        db_session, case.id, TestCaseUpdate(display_order=42)
    )

    assert updated.display_order == 42
