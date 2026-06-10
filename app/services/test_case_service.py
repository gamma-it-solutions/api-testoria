from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.mixins import not_deleted
from app.models.project import Project
from app.models.tag import Tag, test_case_tags
from app.models.test_case import TestCase
from app.models.test_suite import TestSuite
from app.schemas.test_case import TestCaseCreate, TestCaseListFilters, TestCaseUpdate
from app.services import audit_service, realtime_service, tag_service


async def _get_project(db: AsyncSession, project_id: int) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, not_deleted(Project))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def _get_suite_in_project(
    db: AsyncSession, suite_id: int, project_id: int
) -> TestSuite:
    result = await db.execute(
        select(TestSuite).where(
            TestSuite.id == suite_id,
            TestSuite.project_id == project_id,
            not_deleted(TestSuite),
        )
    )
    suite = result.scalar_one_or_none()
    if suite is None:
        raise BadRequestError(f"Suite {suite_id} not found in project {project_id}")
    return suite


async def _resolve_tags(db: AsyncSession, tag_names: list[str]) -> list[Tag]:
    return await tag_service.get_or_create_many(db, tag_names)


def apply_case_order(query: Select[tuple[TestCase]]) -> Select[tuple[TestCase]]:
    """Sort cases by (display_order NULLS LAST, created_at, id) for stability.

    Mirrors `apply_suite_order` in `test_suite_service.py`. Postgres-specific
    NULLS LAST — the project targets Postgres.
    """
    return query.order_by(
        TestCase.display_order.asc().nulls_last(),
        TestCase.created_at.asc(),
        TestCase.id.asc(),
    )


def _build_list_query(
    project_id: int, filters: TestCaseListFilters, include_deleted: bool = False
) -> Select[tuple[TestCase]]:
    query = (
        select(TestCase)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(TestSuite.project_id == project_id, not_deleted(TestSuite))
        .options(selectinload(TestCase.tags))
    )
    if not include_deleted:
        query = query.where(not_deleted(TestCase))

    if filters.search:
        term = f"%{filters.search}%"
        query = query.where(
            or_(TestCase.title.ilike(term), TestCase.description.ilike(term))
        )
    if filters.suite_id is not None:
        query = query.where(TestCase.suite_id == filters.suite_id)
    if filters.priority is not None:
        query = query.where(TestCase.priority == filters.priority)
    if filters.type is not None:
        query = query.where(TestCase.type == filters.type)
    if filters.status is not None:
        query = query.where(TestCase.status == filters.status)
    if filters.tag_ids:
        query = query.where(
            select(test_case_tags.c.test_case_id)
            .where(
                test_case_tags.c.test_case_id == TestCase.id,
                test_case_tags.c.tag_id.in_(filters.tag_ids),
            )
            .exists()
        )
    if filters.automation_id is not None:
        query = query.where(TestCase.automation_id == filters.automation_id)

    return query


async def list_test_cases(
    db: AsyncSession,
    project_id: int,
    filters: TestCaseListFilters,
    include_deleted: bool = False,
) -> tuple[list[TestCase], int]:
    await _get_project(db, project_id)
    base_query = _build_list_query(project_id, filters, include_deleted=include_deleted)

    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total: int = count_result.scalar_one()

    offset = (filters.page - 1) * filters.page_size
    result = await db.execute(
        apply_case_order(base_query).offset(offset).limit(filters.page_size)
    )
    return list(result.scalars().all()), total


async def get_test_case(
    db: AsyncSession, test_case_id: int, allow_deleted: bool = False
) -> TestCase:
    query = (
        select(TestCase)
        .where(TestCase.id == test_case_id)
        .options(selectinload(TestCase.tags))
    )
    if not allow_deleted:
        query = query.where(not_deleted(TestCase))
    result = await db.execute(query)
    tc = result.scalar_one_or_none()
    if tc is None:
        raise NotFoundError(f"TestCase {test_case_id} not found")
    return tc


async def create_test_case(
    db: AsyncSession,
    project_id: int,
    data: TestCaseCreate,
    user_id: int | None = None,
) -> TestCase:
    await _get_project(db, project_id)
    await _get_suite_in_project(db, data.suite_id, project_id)

    tags = await _resolve_tags(db, data.tags)
    steps_data = [s.model_dump() for s in data.steps]

    tc = TestCase(
        suite_id=data.suite_id,
        title=data.title,
        description=data.description,
        preconditions=data.preconditions,
        steps=steps_data,
        priority=data.priority,
        type=data.type,
        status=data.status,
        tags=tags,
        automation_id=data.automation_id,
        display_order=data.display_order,
    )
    db.add(tc)
    await db.flush()
    await db.refresh(tc)
    await audit_service.log_action(db, user_id, "CREATE", "TestCase", tc.id)
    return tc


async def update_test_case(
    db: AsyncSession,
    test_case_id: int,
    data: TestCaseUpdate,
    user_id: int | None = None,
) -> TestCase:
    tc = await get_test_case(db, test_case_id)

    if data.suite_id is not None:
        suite_result = await db.execute(
            select(TestSuite).where(TestSuite.id == data.suite_id)
        )
        suite = suite_result.scalar_one_or_none()
        if suite is None:
            raise NotFoundError(f"TestSuite {data.suite_id} not found")
        tc.suite_id = data.suite_id

    if data.title is not None:
        tc.title = data.title
    if data.description is not None:
        tc.description = data.description
    if data.preconditions is not None:
        tc.preconditions = data.preconditions
    if data.steps is not None:
        tc.steps = [s.model_dump() for s in data.steps]
    if data.priority is not None:
        tc.priority = data.priority
    if data.type is not None:
        tc.type = data.type
    if data.status is not None:
        tc.status = data.status
    if data.tags is not None:
        tc.tags = await _resolve_tags(db, data.tags)
    if data.automation_id is not None:
        tc.automation_id = data.automation_id
        tc.type = "automated"
    if "display_order" in data.model_fields_set:
        tc.display_order = data.display_order

    await db.flush()
    await db.refresh(tc)

    suite_result = await db.execute(
        select(TestSuite).where(TestSuite.id == tc.suite_id)
    )
    suite = suite_result.scalar_one_or_none()
    if suite is not None:
        await realtime_service.publish_case_update(
            project_id=suite.project_id, case_id=tc.id, action="updated"
        )

    await audit_service.log_action(db, user_id, "UPDATE", "TestCase", tc.id)
    return tc


async def delete_test_case(
    db: AsyncSession, test_case_id: int, user_id: int | None = None
) -> None:
    tc = await get_test_case(db, test_case_id)
    tc.deleted_at = func.now()
    await audit_service.log_action(db, user_id, "DELETE", "TestCase", test_case_id)
    await db.flush()


async def restore_test_case(
    db: AsyncSession, test_case_id: int, user_id: int | None = None
) -> TestCase:
    tc = await get_test_case(db, test_case_id, allow_deleted=True)
    if tc.deleted_at is None:
        raise BadRequestError(f"TestCase {test_case_id} is not deleted")

    suite_result = await db.execute(
        select(TestSuite).where(TestSuite.id == tc.suite_id)
    )
    suite = suite_result.scalar_one_or_none()
    if suite is None or suite.deleted_at is not None:
        raise BadRequestError("Cannot restore test case: parent suite is deleted")

    tc.deleted_at = None
    await db.flush()
    await db.refresh(tc)
    await audit_service.log_action(db, user_id, "RESTORE", "TestCase", tc.id)
    return tc
