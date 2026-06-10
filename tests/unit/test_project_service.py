from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.services import project_service


async def _seed_project(db: AsyncSession, name: str = "Stats Project") -> Project:
    project = Project(name=name)
    db.add(project)
    await db.flush()
    return project


async def _seed_run_and_results(
    db: AsyncSession,
    project: Project,
    *,
    run_status: str,
    results: list[str],
) -> TestRun:
    suite = TestSuite(project_id=project.id, name=f"Suite-{run_status}")
    db.add(suite)
    await db.flush()

    completed_at = datetime.now(UTC) if run_status == "completed" else None
    run = TestRun(
        project_id=project.id,
        suite_id=suite.id,
        name=f"Run-{run_status}",
        status=run_status,
        completed_at=completed_at,
    )
    db.add(run)
    await db.flush()

    for i, status in enumerate(results):
        case = TestCase(
            suite_id=suite.id,
            title=f"Case {run_status} {i}",
            priority="medium",
            type="manual",
        )
        db.add(case)
        await db.flush()
        result = TestResult(
            test_run_id=run.id,
            test_case_id=case.id,
            status=status,
            tested_at=datetime.now(UTC),
        )
        db.add(result)
    await db.flush()
    return run


@pytest.mark.asyncio
async def test_get_stats_counts_only_completed_runs(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    # Planned run with a passed result — must not contribute to pass_rate.
    await _seed_run_and_results(
        db_session, project, run_status="planned", results=["passed"]
    )
    # Completed run with 1 passed + 1 failed.
    await _seed_run_and_results(
        db_session, project, run_status="completed", results=["passed", "failed"]
    )

    stats = await project_service.get_stats(db_session, project.id)
    assert stats.pass_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_get_stats_pass_rate_is_none_when_no_completed_runs(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    await _seed_run_and_results(
        db_session, project, run_status="active", results=["passed", "passed"]
    )

    stats = await project_service.get_stats(db_session, project.id)
    assert stats.pass_rate is None


@pytest.mark.asyncio
async def test_get_bulk_stats_filters_non_completed_runs(
    db_session: AsyncSession,
) -> None:
    project_a = await _seed_project(db_session, "A")
    project_b = await _seed_project(db_session, "B")

    # Project A: only an active run → pass_rate None.
    await _seed_run_and_results(
        db_session, project_a, run_status="active", results=["passed"]
    )
    # Project B: a completed run with 1 passed + 1 failed → 0.5.
    await _seed_run_and_results(
        db_session, project_b, run_status="completed", results=["passed", "failed"]
    )

    resp = await project_service.get_bulk_stats(db_session)
    by_id = {item.project_id: item for item in resp.items}
    assert by_id[project_a.id].pass_rate is None
    assert by_id[project_b.id].pass_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_get_bulk_stats_active_runs_counts_planned_and_active(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    await _seed_run_and_results(
        db_session, project, run_status="planned", results=[]
    )
    await _seed_run_and_results(
        db_session, project, run_status="active", results=[]
    )
    await _seed_run_and_results(
        db_session, project, run_status="completed", results=[]
    )
    await _seed_run_and_results(
        db_session, project, run_status="aborted", results=[]
    )

    resp = await project_service.get_bulk_stats(db_session)
    row = next(item for item in resp.items if item.project_id == project.id)
    # planned + active only — completed and aborted are excluded.
    assert row.active_runs == 2
    assert row.total_test_runs == 4
