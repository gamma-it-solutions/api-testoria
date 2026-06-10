"""Tests for `delete_suite` subtree cascade (TES-70 / plan-045) and
`update_suite` parent-cycle guard (TES-69 / plan-046)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_suite import TestSuite
from app.schemas.test_suite import TestSuiteUpdate
from app.services import test_suite_service


async def _seed_project(
    db: AsyncSession, name: str = "Cascade Project"
) -> Project:
    project = Project(name=name)
    db.add(project)
    await db.flush()
    return project


async def _seed_suite(
    db: AsyncSession,
    project: Project,
    name: str,
    parent: TestSuite | None = None,
) -> TestSuite:
    suite = TestSuite(
        project_id=project.id,
        name=name,
        parent_suite_id=parent.id if parent else None,
    )
    db.add(suite)
    await db.flush()
    return suite


async def _seed_case(db: AsyncSession, suite: TestSuite, title: str) -> TestCase:
    case = TestCase(
        suite_id=suite.id,
        title=title,
        priority="medium",
        type="manual",
    )
    db.add(case)
    await db.flush()
    return case


async def _is_deleted(db: AsyncSession, model: type, pk: int) -> bool:
    result = await db.execute(select(model).where(model.id == pk))
    row = result.scalar_one()
    return row.deleted_at is not None


async def test_delete_suite_leaf_preserves_existing_behaviour(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project, "Leaf")
    case = await _seed_case(db_session, suite, "C1")

    await test_suite_service.delete_suite(db_session, suite.id)

    assert await _is_deleted(db_session, TestSuite, suite.id)
    assert await _is_deleted(db_session, TestCase, case.id)


async def test_delete_suite_two_levels_cascade(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    parent = await _seed_suite(db_session, project, "Section")
    child = await _seed_suite(db_session, project, "Subsection", parent=parent)
    parent_case = await _seed_case(db_session, parent, "Direct")
    child_case = await _seed_case(db_session, child, "InChild")

    await test_suite_service.delete_suite(db_session, parent.id)

    assert await _is_deleted(db_session, TestSuite, parent.id)
    assert await _is_deleted(db_session, TestSuite, child.id)
    assert await _is_deleted(db_session, TestCase, parent_case.id)
    assert await _is_deleted(db_session, TestCase, child_case.id)


async def test_delete_suite_three_levels_cascade(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    grand = await _seed_suite(db_session, project, "L1")
    parent = await _seed_suite(db_session, project, "L2", parent=grand)
    child = await _seed_suite(db_session, project, "L3", parent=parent)
    deep_case = await _seed_case(db_session, child, "Deep")

    await test_suite_service.delete_suite(db_session, grand.id)

    assert await _is_deleted(db_session, TestSuite, grand.id)
    assert await _is_deleted(db_session, TestSuite, parent.id)
    assert await _is_deleted(db_session, TestSuite, child.id)
    assert await _is_deleted(db_session, TestCase, deep_case.id)


async def test_delete_suite_does_not_affect_sibling_subtree(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    parent_a = await _seed_suite(db_session, project, "A")
    parent_b = await _seed_suite(db_session, project, "B")
    child_a = await _seed_suite(db_session, project, "A1", parent=parent_a)
    child_b = await _seed_suite(db_session, project, "B1", parent=parent_b)
    case_a = await _seed_case(db_session, child_a, "Acase")
    case_b = await _seed_case(db_session, child_b, "Bcase")

    await test_suite_service.delete_suite(db_session, parent_a.id)

    assert await _is_deleted(db_session, TestSuite, parent_a.id)
    assert await _is_deleted(db_session, TestSuite, child_a.id)
    assert await _is_deleted(db_session, TestCase, case_a.id)
    # Sibling B branch untouched
    assert not await _is_deleted(db_session, TestSuite, parent_b.id)
    assert not await _is_deleted(db_session, TestSuite, child_b.id)
    assert not await _is_deleted(db_session, TestCase, case_b.id)


async def test_delete_suite_does_not_affect_other_projects(
    db_session: AsyncSession,
) -> None:
    project1 = await _seed_project(db_session, "P1")
    project2 = await _seed_project(db_session, "P2")
    suite1 = await _seed_suite(db_session, project1, "S1")
    suite2 = await _seed_suite(db_session, project2, "S2")
    case2 = await _seed_case(db_session, suite2, "P2Case")

    await test_suite_service.delete_suite(db_session, suite1.id)

    assert await _is_deleted(db_session, TestSuite, suite1.id)
    assert not await _is_deleted(db_session, TestSuite, suite2.id)
    assert not await _is_deleted(db_session, TestCase, case2.id)


async def test_delete_suite_idempotent_over_previously_deleted_descendant(
    db_session: AsyncSession,
) -> None:
    """A descendant that was independently soft-deleted earlier keeps its
    original `deleted_at` timestamp; the cascade does not stamp it again."""
    project = await _seed_project(db_session)
    parent = await _seed_suite(db_session, project, "P")
    child = await _seed_suite(db_session, project, "C", parent=parent)

    # Independently delete the child earlier
    earlier = datetime.now(UTC) - timedelta(days=1)
    child.deleted_at = earlier
    await db_session.flush()
    await db_session.refresh(child)
    original_deleted_at = child.deleted_at

    # Now cascade-delete the parent
    await test_suite_service.delete_suite(db_session, parent.id)

    await db_session.refresh(child)
    assert child.deleted_at is not None
    # Original timestamp preserved — the cascade's `where deleted_at is null`
    # filter leaves previously-soft-deleted descendants alone.
    assert child.deleted_at == original_deleted_at
    # Parent freshly stamped
    assert await _is_deleted(db_session, TestSuite, parent.id)


async def test_update_suite_rejects_parent_set_to_self(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project, "Self")

    with pytest.raises(BadRequestError):
        await test_suite_service.update_suite(
            db_session, suite.id, TestSuiteUpdate(parent_suite_id=suite.id)
        )


async def test_update_suite_rejects_parent_set_to_descendant(
    db_session: AsyncSession,
) -> None:
    """Moving a suite under one of its own descendants would orphan the
    subtree from the root. Service must reject before the change lands."""
    project = await _seed_project(db_session)
    grand = await _seed_suite(db_session, project, "G")
    parent = await _seed_suite(db_session, project, "P", parent=grand)
    child = await _seed_suite(db_session, project, "C", parent=parent)

    original_parent_of_grand = grand.parent_suite_id

    with pytest.raises(BadRequestError):
        await test_suite_service.update_suite(
            db_session, grand.id, TestSuiteUpdate(parent_suite_id=child.id)
        )

    await db_session.refresh(grand)
    assert grand.parent_suite_id == original_parent_of_grand


async def test_update_suite_allows_parent_set_to_unrelated_sibling(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    a = await _seed_suite(db_session, project, "A")
    b = await _seed_suite(db_session, project, "B")

    updated = await test_suite_service.update_suite(
        db_session, a.id, TestSuiteUpdate(parent_suite_id=b.id)
    )

    assert updated.parent_suite_id == b.id
