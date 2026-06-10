from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.models.user import User
from app.schemas.test_result import (
    TestResultCreate as ResultCreate,
)
from app.schemas.test_result import (
    TestResultUpdate as ResultUpdate,
)
from app.services import test_result_service, test_run_service


async def _seed_user(db: AsyncSession) -> User:
    user = User(
        username="lifecycle-user",
        email="lifecycle@example.com",
        hashed_password="hash",
        role="tester",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _seed_fixture(
    db: AsyncSession, *, run_status: str = "planned"
) -> tuple[User, TestRun, TestCase]:
    user = await _seed_user(db)
    project = Project(name="Result Trigger")
    db.add(project)
    await db.flush()
    suite = TestSuite(project_id=project.id, name="Suite")
    db.add(suite)
    await db.flush()
    case = TestCase(
        suite_id=suite.id,
        title="Case",
        priority="medium",
        type="manual",
    )
    db.add(case)
    run = TestRun(project_id=project.id, name="Run", status=run_status)
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return user, run, case


# --- submit() auto-transitions planned → active ---


@pytest.mark.asyncio
async def test_submit_first_result_flips_planned_to_active(
    db_session: AsyncSession,
) -> None:
    user, run, case = await _seed_fixture(db_session)
    assert run.status == "planned"

    await test_result_service.submit(
        db_session,
        run.id,
        ResultCreate(test_case_id=case.id, status="passed"),
        user.id,
    )

    refreshed = await test_run_service.get_run(db_session, run.id)
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_submit_no_op_repeat_does_not_revert_completed(
    db_session: AsyncSession,
) -> None:
    """If the run has been closed and a tester re-submits an identical result,
    the guarded UPDATE must be a no-op. The run stays `completed`."""
    user, run, case = await _seed_fixture(db_session)
    await test_result_service.submit(
        db_session,
        run.id,
        ResultCreate(test_case_id=case.id, status="passed"),
        user.id,
    )
    # Close manually.
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    await db_session.flush()

    await test_result_service.submit(
        db_session,
        run.id,
        ResultCreate(test_case_id=case.id, status="passed"),
        user.id,
    )
    refreshed = await test_run_service.get_run(db_session, run.id)
    assert refreshed.status == "completed"


@pytest.mark.asyncio
async def test_submit_resubmit_same_status_does_not_write_history_or_transition_twice(
    db_session: AsyncSession,
) -> None:
    """A no-op resubmit skips history recording; on a planned run this means
    the transition also does not fire a second time (it wouldn't anyway, since
    the guard checks status == 'planned', but we also assert the history
    table is not spammed)."""
    from sqlalchemy import select

    from app.models.result_history import ResultHistory

    user, run, case = await _seed_fixture(db_session)

    await test_result_service.submit(
        db_session,
        run.id,
        ResultCreate(test_case_id=case.id, status="passed"),
        user.id,
    )
    first_count = (
        await db_session.execute(select(ResultHistory))
    ).scalars().all()

    # Submit the same payload again — no meaningful change.
    await test_result_service.submit(
        db_session,
        run.id,
        ResultCreate(test_case_id=case.id, status="passed"),
        user.id,
    )
    second_count = (
        await db_session.execute(select(ResultHistory))
    ).scalars().all()
    assert len(first_count) == len(second_count)


# --- update_result() triggers transition on meaningful change ---


@pytest.mark.asyncio
async def test_update_result_flips_planned_to_active_on_status_change(
    db_session: AsyncSession,
) -> None:
    user, run, case = await _seed_fixture(db_session)

    # Seed an existing result directly in the `planned` run without using
    # submit() so we can test update_result() in isolation.
    existing = TestResult(
        test_run_id=run.id,
        test_case_id=case.id,
        status="no_run",
        tested_by=user.id,
        tested_at=datetime.now(UTC),
    )
    db_session.add(existing)
    await db_session.flush()
    await db_session.refresh(existing)

    # Sanity: the run is still planned because we bypassed submit().
    assert (await test_run_service.get_run(db_session, run.id)).status == "planned"

    await test_result_service.update_result(
        db_session,
        existing.id,
        ResultUpdate(status="failed"),
        user.id,
    )

    refreshed = await test_run_service.get_run(db_session, run.id)
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_update_result_no_op_does_not_transition(
    db_session: AsyncSession,
) -> None:
    """Writing the same status + comment back is a no-op and must not
    transition the run."""
    user, run, case = await _seed_fixture(db_session)

    existing = TestResult(
        test_run_id=run.id,
        test_case_id=case.id,
        status="passed",
        comment="ok",
        tested_by=user.id,
        tested_at=datetime.now(UTC),
    )
    db_session.add(existing)
    await db_session.flush()
    await db_session.refresh(existing)

    await test_result_service.update_result(
        db_session,
        existing.id,
        ResultUpdate(status="passed", comment="ok"),
        user.id,
    )

    refreshed = await test_run_service.get_run(db_session, run.id)
    assert refreshed.status == "planned"
