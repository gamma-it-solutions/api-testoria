from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.mixins import not_deleted
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_suite import TestSuite
from app.schemas.test_suite import TestSuiteCreate, TestSuiteUpdate


def apply_suite_order(query: Select[tuple[TestSuite]]) -> Select[tuple[TestSuite]]:
    """Sort suites by (display_order NULLS LAST, created_at, id) for stability.

    Postgres-specific NULLS LAST — the project targets Postgres.
    """
    return query.order_by(
        TestSuite.display_order.asc().nulls_last(),
        TestSuite.created_at.asc(),
        TestSuite.id.asc(),
    )


async def _get_project(db: AsyncSession, project_id: int) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, not_deleted(Project))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def get_suite(
    db: AsyncSession, suite_id: int, allow_deleted: bool = False
) -> TestSuite:
    query = select(TestSuite).where(TestSuite.id == suite_id)
    if not allow_deleted:
        query = query.where(not_deleted(TestSuite))
    result = await db.execute(query)
    suite = result.scalar_one_or_none()
    if suite is None:
        raise NotFoundError(f"TestSuite {suite_id} not found")
    return suite


async def list_suites(
    db: AsyncSession, project_id: int, include_deleted: bool = False
) -> list[TestSuite]:
    await _get_project(db, project_id)
    query = select(TestSuite).where(TestSuite.project_id == project_id)
    if not include_deleted:
        query = query.where(not_deleted(TestSuite))
    result = await db.execute(apply_suite_order(query))
    return list(result.scalars().all())


async def create_suite(
    db: AsyncSession, project_id: int, data: TestSuiteCreate
) -> TestSuite:
    await _get_project(db, project_id)

    if data.parent_suite_id is not None:
        parent = await get_suite(db, data.parent_suite_id)
        if parent.project_id != project_id:
            raise BadRequestError("Parent suite belongs to a different project")

    suite = TestSuite(
        project_id=project_id,
        parent_suite_id=data.parent_suite_id,
        name=data.name,
        description=data.description,
        display_order=data.display_order,
    )
    db.add(suite)
    await db.flush()
    await db.refresh(suite)
    return suite


async def update_suite(
    db: AsyncSession, suite_id: int, data: TestSuiteUpdate
) -> TestSuite:
    suite = await get_suite(db, suite_id)

    if data.parent_suite_id is not None:
        if data.parent_suite_id == suite_id:
            raise BadRequestError("A suite cannot be its own parent")
        parent = await get_suite(db, data.parent_suite_id)
        if parent.project_id != suite.project_id:
            raise BadRequestError("Parent suite belongs to a different project")
        descendants = await _descendant_suite_ids(db, suite_id)
        if data.parent_suite_id in descendants:
            raise BadRequestError(
                "Cannot move a suite under one of its own descendants"
            )

    if data.name is not None:
        suite.name = data.name
    if data.description is not None:
        suite.description = data.description
    if "parent_suite_id" in data.model_fields_set:
        suite.parent_suite_id = data.parent_suite_id
    if "display_order" in data.model_fields_set:
        suite.display_order = data.display_order

    await db.flush()
    await db.refresh(suite)
    return suite


async def _descendant_suite_ids(
    db: AsyncSession, root_id: int
) -> list[int]:
    """Return ids of `root_id` plus every active descendant via parent_suite_id.

    Uses a Postgres recursive CTE for a single round-trip; restricted to suites
    that are not already soft-deleted so a previously-orphaned descendant
    keeps its original `deleted_at` timestamp on cascade. Postgres-only —
    matches the file's existing `nulls_last()` Postgres assumption.
    """
    base = (
        select(TestSuite.id.label("id"))
        .where(TestSuite.id == root_id, not_deleted(TestSuite))
        .cte(name="suite_subtree", recursive=True)
    )
    descendants = select(TestSuite.id).where(
        TestSuite.parent_suite_id == base.c.id,
        not_deleted(TestSuite),
    )
    cte = base.union_all(descendants)
    result = await db.execute(select(cte.c.id))
    return [row[0] for row in result.all()]


async def delete_suite(db: AsyncSession, suite_id: int) -> None:
    """Soft-delete the suite and every descendant suite + their test cases.

    Cascades down the parent_suite_id tree so deleting a "section" cleans up
    sub-sections and their cases atomically (TES-70 / plan-045). Sibling
    branches and other projects are untouched. Already-soft-deleted descendants
    keep their original `deleted_at` timestamp.
    """
    await get_suite(db, suite_id)
    now = func.now()

    descendant_ids = await _descendant_suite_ids(db, suite_id)
    if not descendant_ids:
        # Defensive: get_suite would have raised if the root were missing,
        # so this only fires if the root was just soft-deleted concurrently.
        await db.flush()
        return

    await db.execute(
        update(TestSuite)
        .where(TestSuite.id.in_(descendant_ids), not_deleted(TestSuite))
        .values(deleted_at=now)
    )
    await db.execute(
        update(TestCase)
        .where(TestCase.suite_id.in_(descendant_ids), not_deleted(TestCase))
        .values(deleted_at=now)
    )
    await db.flush()


async def restore_suite(db: AsyncSession, suite_id: int) -> TestSuite:
    suite = await get_suite(db, suite_id, allow_deleted=True)
    if suite.deleted_at is None:
        raise BadRequestError(f"TestSuite {suite_id} is not deleted")

    # Parent project must not be deleted
    project_result = await db.execute(
        select(Project).where(Project.id == suite.project_id)
    )
    project = project_result.scalar_one_or_none()
    if project is None or project.deleted_at is not None:
        raise BadRequestError("Cannot restore suite: parent project is deleted")

    suite.deleted_at = None
    await db.flush()
    await db.refresh(suite)
    return suite
