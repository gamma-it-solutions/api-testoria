"""`submit_many` must be a faster `submit`, not a different one.

The batch path exists purely for cost. If its history/upsert semantics drift
from `submit()`, a CI import silently writes different data than the UI does —
so parity is asserted directly rather than assumed.
"""

import itertools
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.project import Project
from app.models.result_history import ResultHistory
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.models.user import User
from app.schemas.test_result import TestResultCreate
from app.services import realtime_service, test_result_service

_graph_seq = itertools.count()


async def _graph(db: AsyncSession, n_cases: int = 2) -> tuple[TestRun, list[int], User]:
    # Unique per call — tests that build two independent graphs would otherwise
    # collide on the users.username / users.email unique constraints.
    seq = next(_graph_seq)
    user = User(
        username=f"bulk{seq}",
        email=f"bulk{seq}@example.com",
        hashed_password="x",
        role="tester",
        is_active=True,
    )
    project = Project(name=f"Bulk project {seq}")
    db.add_all([user, project])
    await db.flush()

    suite = TestSuite(project_id=project.id, name="S")
    db.add(suite)
    await db.flush()

    case_ids = []
    for index in range(n_cases):
        case = TestCase(suite_id=suite.id, title=f"case {index}", steps=[])
        db.add(case)
        await db.flush()
        case_ids.append(case.id)

    run = TestRun(project_id=project.id, name="R", status="planned")
    db.add(run)
    await db.flush()
    return run, case_ids, user


async def _history_count(db: AsyncSession, run_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(ResultHistory)
        .join(TestResult, ResultHistory.test_result_id == TestResult.id)
        .where(TestResult.test_run_id == run_id)
    )
    return int(result.scalar_one())


async def test_submit_many_creates_results_and_history(
    db_session: AsyncSession,
) -> None:
    run, case_ids, user = await _graph(db_session)

    submitted, counts = await test_result_service.submit_many(
        db_session,
        run.id,
        [
            TestResultCreate(test_case_id=case_ids[0], status="passed"),
            TestResultCreate(test_case_id=case_ids[1], status="failed"),
        ],
        user.id,
    )

    assert submitted == 2
    assert counts == {"passed": 1, "failed": 1}
    assert await _history_count(db_session, run.id) == 2


async def test_resubmitting_identical_results_writes_no_history(
    db_session: AsyncSession,
) -> None:
    """Parity with `submit`: a no-op resubmit must not pollute the timeline.

    This is what makes the CLI safe to run alongside a live reporter that
    already posted the same values.
    """
    run, case_ids, user = await _graph(db_session, n_cases=1)
    items = [TestResultCreate(test_case_id=case_ids[0], status="passed")]

    await test_result_service.submit_many(db_session, run.id, items, user.id)
    after_first = await _history_count(db_session, run.id)
    await test_result_service.submit_many(db_session, run.id, items, user.id)

    assert await _history_count(db_session, run.id) == after_first


async def test_changed_status_does_write_history(db_session: AsyncSession) -> None:
    run, case_ids, user = await _graph(db_session, n_cases=1)

    await test_result_service.submit_many(
        db_session,
        run.id,
        [TestResultCreate(test_case_id=case_ids[0], status="passed")],
        user.id,
    )
    await test_result_service.submit_many(
        db_session,
        run.id,
        [TestResultCreate(test_case_id=case_ids[0], status="failed")],
        user.id,
    )

    assert await _history_count(db_session, run.id) == 2


async def test_submit_many_matches_submit_history_semantics(
    db_session: AsyncSession,
) -> None:
    """Same input through both paths must yield the same history count."""
    run_a, cases_a, user = await _graph(db_session, n_cases=1)
    run_b, cases_b, _ = await _graph(db_session, n_cases=1)

    payload_a = TestResultCreate(test_case_id=cases_a[0], status="passed")
    payload_b = TestResultCreate(test_case_id=cases_b[0], status="passed")

    await test_result_service.submit(db_session, run_a.id, payload_a, user.id)
    await test_result_service.submit(db_session, run_a.id, payload_a, user.id)

    await test_result_service.submit_many(db_session, run_b.id, [payload_b], user.id)
    await test_result_service.submit_many(db_session, run_b.id, [payload_b], user.id)

    assert await _history_count(db_session, run_a.id) == await _history_count(
        db_session, run_b.id
    )


async def test_submit_many_publishes_one_aggregate_event(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N results must not mean N websocket publishes."""
    run, case_ids, user = await _graph(db_session, n_cases=2)
    bulk = AsyncMock()
    single = AsyncMock()
    monkeypatch.setattr(realtime_service, "publish_result_bulk", bulk)
    monkeypatch.setattr(realtime_service, "publish_result_update", single)

    await test_result_service.submit_many(
        db_session,
        run.id,
        [TestResultCreate(test_case_id=cid, status="passed") for cid in case_ids],
        user.id,
    )

    assert bulk.await_count == 1
    assert single.await_count == 0


async def test_submit_many_transitions_the_run_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import test_run_service

    run, case_ids, user = await _graph(db_session, n_cases=3)
    transition = AsyncMock()
    monkeypatch.setattr(test_run_service, "transition_to_active", transition)

    await test_result_service.submit_many(
        db_session,
        run.id,
        [TestResultCreate(test_case_id=cid, status="passed") for cid in case_ids],
        user.id,
    )

    assert transition.await_count == 1


async def test_submit_many_skips_the_transition_when_nothing_changed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import test_run_service

    run, case_ids, user = await _graph(db_session, n_cases=1)
    items = [TestResultCreate(test_case_id=case_ids[0], status="passed")]
    await test_result_service.submit_many(db_session, run.id, items, user.id)

    transition = AsyncMock()
    monkeypatch.setattr(test_run_service, "transition_to_active", transition)
    await test_result_service.submit_many(db_session, run.id, items, user.id)

    assert transition.await_count == 0


async def test_submit_many_with_no_items_is_a_noop(db_session: AsyncSession) -> None:
    run, _, user = await _graph(db_session, n_cases=1)

    submitted, counts = await test_result_service.submit_many(
        db_session, run.id, [], user.id
    )

    assert submitted == 0
    assert counts == {}


async def test_submit_many_404s_on_an_unknown_case(db_session: AsyncSession) -> None:
    run, _, user = await _graph(db_session, n_cases=1)

    with pytest.raises(NotFoundError):
        await test_result_service.submit_many(
            db_session,
            run.id,
            [TestResultCreate(test_case_id=987654, status="passed")],
            user.id,
        )


async def test_submit_many_404s_on_an_unknown_run(db_session: AsyncSession) -> None:
    _, case_ids, user = await _graph(db_session, n_cases=1)

    with pytest.raises(NotFoundError):
        await test_result_service.submit_many(
            db_session,
            987654,
            [TestResultCreate(test_case_id=case_ids[0], status="passed")],
            user.id,
        )


async def test_submit_many_normalises_skipped_to_no_run(
    db_session: AsyncSession,
) -> None:
    run, case_ids, user = await _graph(db_session, n_cases=1)

    _, counts = await test_result_service.submit_many(
        db_session,
        run.id,
        [TestResultCreate(test_case_id=case_ids[0], status="skipped")],
        user.id,
    )

    assert counts == {"no_run": 1}
