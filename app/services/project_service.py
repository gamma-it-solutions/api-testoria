from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.mixins import not_deleted
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.schemas.project import (
    ProjectCreate,
    ProjectStats,
    ProjectStatsBulkResponse,
    ProjectStatsItem,
    ProjectUpdate,
)
from app.services import audit_service, test_run_service
from app.utils import stats

_ACTIVE_RUN_STATUSES = ("planned", "active")


async def get_project(
    db: AsyncSession, project_id: int, allow_deleted: bool = False
) -> Project:
    query = select(Project).where(Project.id == project_id)
    if not allow_deleted:
        query = query.where(not_deleted(Project))
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def list_projects(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    include_archived: bool = False,
    include_deleted: bool = False,
) -> tuple[list[Project], int]:
    query = select(Project)
    if not include_archived:
        query = query.where(Project.is_archived.is_(False))
    if not include_deleted:
        query = query.where(not_deleted(Project))

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total: int = count_result.scalar_one()

    offset = (page - 1) * page_size
    result = await db.execute(
        query.order_by(Project.created_at.desc()).offset(offset).limit(page_size)
    )
    return list(result.scalars().all()), total


async def create_project(
    db: AsyncSession, data: ProjectCreate, user_id: int | None = None
) -> Project:
    project = Project(name=data.name, description=data.description)
    db.add(project)
    await db.flush()
    await db.refresh(project)
    await audit_service.log_action(db, user_id, "CREATE", "Project", project.id)
    return project


async def update_project(
    db: AsyncSession,
    project_id: int,
    data: ProjectUpdate,
    user_id: int | None = None,
) -> Project:
    project = await get_project(db, project_id)

    changes: dict[str, object] = {}
    if data.name is not None:
        changes["name"] = data.name
        project.name = data.name
    if data.description is not None:
        changes["description"] = data.description
        project.description = data.description
    if data.is_archived is not None:
        changes["is_archived"] = data.is_archived
        project.is_archived = data.is_archived

    await db.flush()
    await db.refresh(project)
    await audit_service.log_action(
        db, user_id, "UPDATE", "Project", project.id, changes=changes
    )
    return project


async def delete_project(
    db: AsyncSession, project_id: int, user_id: int | None = None
) -> None:
    project = await get_project(db, project_id)
    now = func.now()
    project.deleted_at = now

    # Cascade soft-delete to suites and their cases
    suite_ids_result = await db.execute(
        select(TestSuite.id).where(
            TestSuite.project_id == project_id, not_deleted(TestSuite)
        )
    )
    suite_ids = [row[0] for row in suite_ids_result.all()]
    if suite_ids:
        await db.execute(
            update(TestSuite)
            .where(TestSuite.id.in_(suite_ids), not_deleted(TestSuite))
            .values(deleted_at=now)
        )
        await db.execute(
            update(TestCase)
            .where(TestCase.suite_id.in_(suite_ids), not_deleted(TestCase))
            .values(deleted_at=now)
        )

    # Cascade soft-delete to runs and their results
    run_ids_result = await db.execute(
        select(TestRun.id).where(
            TestRun.project_id == project_id, not_deleted(TestRun)
        )
    )
    run_ids = [row[0] for row in run_ids_result.all()]
    if run_ids:
        await db.execute(
            update(TestRun)
            .where(TestRun.id.in_(run_ids), not_deleted(TestRun))
            .values(deleted_at=now)
        )
        await db.execute(
            update(TestResult)
            .where(TestResult.test_run_id.in_(run_ids), not_deleted(TestResult))
            .values(deleted_at=now)
        )

    await audit_service.log_action(db, user_id, "DELETE", "Project", project_id)
    await db.flush()


async def restore_project(
    db: AsyncSession, project_id: int, user_id: int | None = None
) -> Project:
    project = await get_project(db, project_id, allow_deleted=True)
    if project.deleted_at is None:
        raise BadRequestError(f"Project {project_id} is not deleted")
    project.deleted_at = None
    await db.flush()
    await db.refresh(project)
    await audit_service.log_action(db, user_id, "RESTORE", "Project", project.id)
    return project


async def get_stats(db: AsyncSession, project_id: int) -> ProjectStats:
    await get_project(db, project_id)

    suite_count_result = await db.execute(
        select(func.count(TestSuite.id)).where(
            TestSuite.project_id == project_id, not_deleted(TestSuite)
        )
    )
    total_suites: int = suite_count_result.scalar_one()

    case_count_result = await db.execute(
        select(func.count(TestCase.id))
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(
            TestSuite.project_id == project_id,
            not_deleted(TestSuite),
            not_deleted(TestCase),
        )
    )
    total_cases: int = case_count_result.scalar_one()

    run_count_result = await db.execute(
        select(func.count(TestRun.id)).where(
            TestRun.project_id == project_id, not_deleted(TestRun)
        )
    )
    total_runs: int = run_count_result.scalar_one()

    # Pass rate = mean of per-completed-run pass rates (plan 041). Per-run
    # denominator matches the run-list / Dashboard definition via
    # `test_run_service.batch_run_progress` — otherwise a run with untested
    # cases would show a different rate here than on the Dashboard.
    completed_runs = list(
        (
            await db.execute(
                select(TestRun).where(
                    TestRun.project_id == project_id,
                    TestRun.status == "completed",
                    not_deleted(TestRun),
                )
            )
        )
        .scalars()
        .all()
    )
    progress_by_run = await test_run_service.batch_run_progress(db, completed_runs)
    rates = [
        p.pass_rate for p in progress_by_run.values() if p.pass_rate is not None
    ]
    pass_rate_value = stats.round_ratio(
        sum(rates) / len(rates) if rates else None
    )

    return ProjectStats(
        total_test_cases=total_cases,
        total_test_suites=total_suites,
        total_test_runs=total_runs,
        pass_rate=pass_rate_value,
    )


async def get_bulk_stats(
    db: AsyncSession,
    *,
    include_archived: bool = False,
    project_ids: Sequence[int] | None = None,
) -> ProjectStatsBulkResponse:
    """Return one stats row per project in a single round-trip of grouped queries.

    Semantics match `get_stats()` for any individual project; the difference is
    just avoiding the per-project round-trip.
    """
    project_query = select(Project.id, Project.name, Project.is_archived)
    if not include_archived:
        project_query = project_query.where(Project.is_archived.is_(False))
    if project_ids:
        project_query = project_query.where(Project.id.in_(project_ids))
    project_query = project_query.order_by(Project.name.asc())

    project_rows = (await db.execute(project_query)).all()
    if not project_rows:
        return ProjectStatsBulkResponse(items=[], total=0)

    ids = [row.id for row in project_rows]

    # Suites per project
    suite_rows = (
        await db.execute(
            select(TestSuite.project_id, func.count(TestSuite.id))
            .where(TestSuite.project_id.in_(ids))
            .group_by(TestSuite.project_id)
        )
    ).all()
    suites_by_project: dict[int, int] = {row[0]: row[1] for row in suite_rows}

    # Cases per project (through suites)
    case_rows = (
        await db.execute(
            select(TestSuite.project_id, func.count(TestCase.id))
            .join(TestCase, TestCase.suite_id == TestSuite.id)
            .where(TestSuite.project_id.in_(ids))
            .group_by(TestSuite.project_id)
        )
    ).all()
    cases_by_project: dict[int, int] = {row[0]: row[1] for row in case_rows}

    # Runs per project, split by status so we can derive active_runs in the same query
    run_rows = (
        await db.execute(
            select(
                TestRun.project_id,
                TestRun.status,
                func.count(TestRun.id),
            )
            .where(TestRun.project_id.in_(ids))
            .group_by(TestRun.project_id, TestRun.status)
        )
    ).all()
    total_runs_by_project: dict[int, int] = {}
    active_runs_by_project: dict[int, int] = {}
    for project_id, status, count in run_rows:
        total_runs_by_project[project_id] = (
            total_runs_by_project.get(project_id, 0) + count
        )
        if status in _ACTIVE_RUN_STATUSES:
            active_runs_by_project[project_id] = (
                active_runs_by_project.get(project_id, 0) + count
            )

    # Per-project pass_rate = mean of per-completed-run rates (plan 041).
    # Per-run rates come from `test_run_service.batch_run_progress` so the
    # denominator matches the run-list / Dashboard definition exactly.
    completed_runs = list(
        (
            await db.execute(
                select(TestRun).where(
                    TestRun.project_id.in_(ids),
                    TestRun.status == "completed",
                    not_deleted(TestRun),
                )
            )
        )
        .scalars()
        .all()
    )
    progress_by_run = await test_run_service.batch_run_progress(db, completed_runs)
    rates_by_project: dict[int, list[float]] = {}
    for run in completed_runs:
        rate = progress_by_run[run.id].pass_rate
        if rate is not None:
            rates_by_project.setdefault(run.project_id, []).append(rate)

    items: list[ProjectStatsItem] = []
    for row in project_rows:
        rates = rates_by_project.get(row.id, [])
        pass_rate_value = stats.round_ratio(
            sum(rates) / len(rates) if rates else None
        )
        items.append(
            ProjectStatsItem(
                project_id=row.id,
                name=row.name,
                is_archived=row.is_archived,
                total_test_cases=cases_by_project.get(row.id, 0),
                total_test_suites=suites_by_project.get(row.id, 0),
                total_test_runs=total_runs_by_project.get(row.id, 0),
                active_runs=active_runs_by_project.get(row.id, 0),
                pass_rate=pass_rate_value,
            )
        )

    return ProjectStatsBulkResponse(items=items, total=len(items))
