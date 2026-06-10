from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.test_run import TestRun
from app.services import test_run_service


async def _seed_project(db: AsyncSession) -> Project:
    project = Project(name="Lifecycle Project")
    db.add(project)
    await db.flush()
    return project


async def _seed_run(
    db: AsyncSession,
    project: Project,
    *,
    status: str = "planned",
    completed_at: datetime | None = None,
) -> TestRun:
    run = TestRun(
        project_id=project.id,
        name=f"Run {status}",
        status=status,
        completed_at=completed_at,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


# --- transition_to_active ---


@pytest.mark.asyncio
async def test_transition_to_active_flips_planned(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    run = await _seed_run(db_session, project, status="planned")

    transitioned = await test_run_service.transition_to_active(db_session, run.id)
    assert transitioned is True

    refreshed = await test_run_service.get_run(db_session, run.id)
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_transition_to_active_noop_when_already_active(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    run = await _seed_run(db_session, project, status="active")

    transitioned = await test_run_service.transition_to_active(db_session, run.id)
    assert transitioned is False

    refreshed = await test_run_service.get_run(db_session, run.id)
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_transition_to_active_noop_when_completed(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    run = await _seed_run(
        db_session, project, status="completed", completed_at=datetime.now(UTC)
    )

    transitioned = await test_run_service.transition_to_active(db_session, run.id)
    assert transitioned is False

    refreshed = await test_run_service.get_run(db_session, run.id)
    assert refreshed.status == "completed"


@pytest.mark.asyncio
async def test_transition_to_active_idempotent_under_double_call(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    run = await _seed_run(db_session, project, status="planned")

    first = await test_run_service.transition_to_active(db_session, run.id)
    second = await test_run_service.transition_to_active(db_session, run.id)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_transition_to_active_skips_soft_deleted_run(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    run = await _seed_run(db_session, project, status="planned")
    run.deleted_at = datetime.now(UTC)
    await db_session.flush()

    transitioned = await test_run_service.transition_to_active(db_session, run.id)
    assert transitioned is False
