from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.services import report_service


async def _seed_project(db: AsyncSession) -> Project:
    project = Project(name="Analytics Project")
    db.add(project)
    await db.flush()
    return project


async def _seed_suite(db: AsyncSession, project: Project) -> TestSuite:
    suite = TestSuite(project_id=project.id, name="Suite")
    db.add(suite)
    await db.flush()
    return suite


async def _seed_case(
    db: AsyncSession,
    suite: TestSuite,
    title: str,
    *,
    priority: str = "medium",
    type_: str = "manual",
    automation_id: str | None = None,
) -> TestCase:
    case = TestCase(
        suite_id=suite.id,
        title=title,
        priority=priority,
        type=type_,
        automation_id=automation_id,
    )
    db.add(case)
    await db.flush()
    return case


async def _seed_run(
    db: AsyncSession,
    project: Project,
    *,
    name: str = "Run",
    status: str = "planned",
    completed_at: datetime | None = None,
) -> TestRun:
    run = TestRun(
        project_id=project.id,
        name=name,
        status=status,
        completed_at=completed_at,
    )
    db.add(run)
    await db.flush()
    return run


async def _seed_result(
    db: AsyncSession,
    run: TestRun,
    case: TestCase,
    status: str,
    *,
    tested_at: datetime | None = None,
) -> TestResult:
    result = TestResult(
        test_run_id=run.id,
        test_case_id=case.id,
        status=status,
        tested_at=tested_at or datetime.now(UTC),
    )
    db.add(result)
    await db.flush()
    return result


# --- _aggregate_run_status_counts ---


@pytest.mark.asyncio
async def test_aggregate_run_status_counts_empty_input(
    db_session: AsyncSession,
) -> None:
    counts = await report_service._aggregate_run_status_counts(db_session, [])
    assert counts == {}


@pytest.mark.asyncio
async def test_aggregate_run_status_counts_zero_fills_runs(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    run = await _seed_run(db_session, project)
    counts = await report_service._aggregate_run_status_counts(db_session, [run.id])
    assert counts[run.id] == {
        "passed": 0,
        "failed": 0,
        "blocked": 0,
        "no_run": 0,
        "total": 0,
    }


@pytest.mark.asyncio
async def test_aggregate_run_status_counts_groups_across_runs(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case_a = await _seed_case(db_session, suite, "A")
    case_b = await _seed_case(db_session, suite, "B")
    case_c = await _seed_case(db_session, suite, "C")

    run1 = await _seed_run(db_session, project, name="R1")
    run2 = await _seed_run(db_session, project, name="R2")

    await _seed_result(db_session, run1, case_a, "passed")
    await _seed_result(db_session, run1, case_b, "failed")
    await _seed_result(db_session, run2, case_c, "blocked")

    counts = await report_service._aggregate_run_status_counts(
        db_session, [run1.id, run2.id]
    )
    assert counts[run1.id]["passed"] == 1
    assert counts[run1.id]["failed"] == 1
    assert counts[run1.id]["total"] == 2
    assert counts[run2.id]["blocked"] == 1
    assert counts[run2.id]["total"] == 1


# --- get_report_analytics ---


@pytest.mark.asyncio
async def test_report_analytics_unknown_project_raises(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(NotFoundError):
        await report_service.get_report_analytics(db_session, project_id=999999)


@pytest.mark.asyncio
async def test_report_analytics_empty_project(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    resp = await report_service.get_report_analytics(db_session, project.id)
    assert resp.project_id == project.id
    assert resp.summary.total_test_cases == 0
    assert resp.summary.total_test_runs == 0
    assert resp.summary.active_runs == 0
    assert resp.summary.overall_pass_rate == 0.0
    assert resp.runs == []
    assert resp.test_case_distribution.by_priority == {}
    assert resp.test_case_distribution.by_type == {}
    assert resp.test_case_distribution.by_automation == {
        "automated": 0,
        "manual": 0,
    }
    assert resp.trend == []


@pytest.mark.asyncio
async def test_report_analytics_aggregates_runs_and_distributions(
    db_session: AsyncSession,
) -> None:
    """Summary pass-rate / distribution count only completed runs (plan 039).
    The per-run `runs` list still surfaces counts for in-flight runs."""
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case_a = await _seed_case(db_session, suite, "A", priority="high", type_="manual")
    case_b = await _seed_case(
        db_session, suite, "B", priority="low", type_="automated", automation_id="ci/b"
    )
    await _seed_case(db_session, suite, "C", priority="high", type_="manual")

    run1 = await _seed_run(db_session, project, name="R1", status="planned")
    run2 = await _seed_run(
        db_session,
        project,
        name="R2",
        status="completed",
        completed_at=datetime.now(UTC),
    )

    await _seed_result(db_session, run1, case_a, "passed")
    await _seed_result(db_session, run1, case_b, "failed")
    await _seed_result(db_session, run2, case_a, "passed")

    resp = await report_service.get_report_analytics(db_session, project.id)

    assert resp.summary.total_test_cases == 3
    assert resp.summary.total_test_runs == 2
    assert resp.summary.active_runs == 1
    # Only the completed run contributes to summary stats.
    assert resp.summary.total_results == 1
    assert resp.summary.result_distribution == {"passed": 1}
    # Per-run rate uses the run-list denominator (plan 041): run2 has 1 passed
    # result but its scope is 3 cases (auto / project-wide), so its rate is
    # 1/3, not 1/1. Summary = mean of completed runs' rates = 1/3.
    assert resp.summary.overall_pass_rate == pytest.approx(0.333, abs=5e-4)

    by_run = {r.id: r for r in resp.runs}
    assert by_run[run1.id].passed == 1
    assert by_run[run1.id].failed == 1
    assert by_run[run1.id].total == 2
    assert by_run[run1.id].pass_rate == pytest.approx(0.5)
    assert by_run[run2.id].passed == 1
    assert by_run[run2.id].total == 1

    assert resp.test_case_distribution.by_priority == {"high": 2, "low": 1}
    assert resp.test_case_distribution.by_type == {"manual": 2, "automated": 1}
    assert resp.test_case_distribution.by_automation == {
        "automated": 1,
        "manual": 2,
    }


@pytest.mark.asyncio
async def test_report_analytics_automation_coverage_uses_type_flag(
    db_session: AsyncSession,
) -> None:
    """Regression: `by_automation.automated` counts cases flagged
    `type='automated'`, regardless of whether `automation_id` is populated.
    The previous implementation required `automation_id IS NOT NULL`, which
    left the Reports donut chart showing 100% manual for any project whose
    testers marked cases as automated without linking a CI id."""
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    # Two automated cases — one linked to CI, one not linked.
    await _seed_case(
        db_session, suite, "Linked", type_="automated", automation_id="ci/x"
    )
    await _seed_case(db_session, suite, "Unlinked", type_="automated")
    # One manual case for contrast.
    await _seed_case(db_session, suite, "Manual", type_="manual")

    resp = await report_service.get_report_analytics(db_session, project.id)
    assert resp.test_case_distribution.by_automation == {
        "automated": 2,
        "manual": 1,
    }


@pytest.mark.asyncio
async def test_report_analytics_excludes_non_completed_runs_from_summary(
    db_session: AsyncSession,
) -> None:
    """Planned / active / aborted runs leak no counts into the summary."""
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case = await _seed_case(db_session, suite, "A")

    planned = await _seed_run(db_session, project, name="P", status="planned")
    active = await _seed_run(db_session, project, name="A", status="active")
    aborted = await _seed_run(db_session, project, name="X", status="aborted")
    await _seed_result(db_session, planned, case, "passed")
    await _seed_result(db_session, active, case, "failed")
    await _seed_result(db_session, aborted, case, "passed")

    resp = await report_service.get_report_analytics(db_session, project.id)
    assert resp.summary.total_results == 0
    assert resp.summary.result_distribution == {}
    assert resp.summary.overall_pass_rate == 0.0
    # active_runs counts planned + active only (not aborted).
    assert resp.summary.active_runs == 2


@pytest.mark.asyncio
async def test_report_analytics_date_filter_excludes_runs_outside_window(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case = await _seed_case(db_session, suite, "A")

    now = datetime.now(UTC)
    old_run = await _seed_run(
        db_session,
        project,
        name="Old",
        status="completed",
        completed_at=now - timedelta(days=60),
    )
    recent_run = await _seed_run(
        db_session,
        project,
        name="Recent",
        status="completed",
        completed_at=now - timedelta(days=1),
    )
    await _seed_result(db_session, old_run, case, "passed")
    await _seed_result(db_session, recent_run, case, "failed")

    resp = await report_service.get_report_analytics(
        db_session,
        project.id,
        date_from=now - timedelta(days=7),
        date_to=now,
    )
    # Summary counts remain project-wide
    assert resp.summary.total_test_runs == 2
    # Only the recent run appears in the window
    returned_ids = {r.id for r in resp.runs}
    assert returned_ids == {recent_run.id}


@pytest.mark.asyncio
async def test_report_analytics_status_filter(db_session: AsyncSession) -> None:
    project = await _seed_project(db_session)
    await _seed_run(db_session, project, name="A", status="planned")
    completed = await _seed_run(
        db_session,
        project,
        name="B",
        status="completed",
        completed_at=datetime.now(UTC),
    )

    resp = await report_service.get_report_analytics(
        db_session, project.id, run_status="completed"
    )
    assert [r.id for r in resp.runs] == [completed.id]


@pytest.mark.asyncio
async def test_report_analytics_include_trend_false_skips_trend(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case = await _seed_case(db_session, suite, "A")
    run = await _seed_run(db_session, project)
    await _seed_result(db_session, run, case, "passed")

    resp = await report_service.get_report_analytics(
        db_session, project.id, include_trend=False
    )
    assert resp.trend == []


@pytest.mark.asyncio
async def test_report_analytics_trend_zero_fills_window(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case = await _seed_case(db_session, suite, "A")
    today = datetime.now(UTC)
    run = await _seed_run(
        db_session, project, status="completed", completed_at=today
    )

    await _seed_result(db_session, run, case, "passed", tested_at=today)

    resp = await report_service.get_report_analytics(
        db_session,
        project.id,
        date_from=today - timedelta(days=2),
        date_to=today,
    )
    assert len(resp.trend) == 3
    totals = [p.total for p in resp.trend]
    assert sum(totals) == 1


@pytest.mark.asyncio
async def test_report_analytics_trend_excludes_non_completed_runs(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case = await _seed_case(db_session, suite, "A")
    active_run = await _seed_run(db_session, project, status="active")

    today = datetime.now(UTC)
    await _seed_result(db_session, active_run, case, "passed", tested_at=today)

    resp = await report_service.get_report_analytics(
        db_session,
        project.id,
        date_from=today - timedelta(days=2),
        date_to=today,
    )
    assert len(resp.trend) == 3
    assert all(p.total == 0 for p in resp.trend)


@pytest.mark.asyncio
async def test_report_analytics_excludes_soft_deleted_rows(
    db_session: AsyncSession,
) -> None:
    """Soft-deleted suites, cases, runs, and results must not appear in any
    counts, distributions, or trend — matching `get_dashboard()` semantics.
    """
    now = datetime.now(UTC)
    project = await _seed_project(db_session)

    live_suite = await _seed_suite(db_session, project)
    dead_suite = await _seed_suite(db_session, project)
    dead_suite.deleted_at = now

    live_case = await _seed_case(
        db_session, live_suite, "live", priority="high", type_="manual"
    )
    dead_case = await _seed_case(
        db_session, live_suite, "dead", priority="low", type_="automated"
    )
    dead_case.deleted_at = now
    soft_deleted_result_case = await _seed_case(
        db_session, live_suite, "for-dead-result", priority="high", type_="manual"
    )

    live_run = await _seed_run(
        db_session,
        project,
        name="live-run",
        status="completed",
        completed_at=now,
    )
    dead_run = await _seed_run(db_session, project, name="dead-run")
    dead_run.deleted_at = now

    await _seed_result(db_session, live_run, live_case, "passed")
    dead_result = await _seed_result(
        db_session, live_run, soft_deleted_result_case, "failed"
    )
    dead_result.deleted_at = now
    await db_session.flush()

    resp = await report_service.get_report_analytics(db_session, project.id)

    assert resp.summary.total_test_suites == 1
    assert resp.summary.total_test_cases == 2  # live_case + soft_deleted_result_case
    assert resp.summary.total_test_runs == 1
    assert resp.summary.total_results == 1
    assert resp.summary.result_distribution == {"passed": 1}
    assert [r.id for r in resp.runs] == [live_run.id]
    assert resp.runs[0].total == 1
    assert resp.test_case_distribution.by_priority == {"high": 2}
    assert resp.test_case_distribution.by_type == {"manual": 2}
    assert resp.test_case_distribution.by_automation == {
        "automated": 0,
        "manual": 2,
    }


# --- _resolve_project_scope ---


@pytest.mark.asyncio
async def test_resolve_project_scope_returns_all_visible_when_none(
    db_session: AsyncSession,
) -> None:
    p1 = await _seed_project(db_session)
    p2 = await _seed_project(db_session)
    projects = await report_service._resolve_project_scope(
        db_session, None, include_archived=False
    )
    ids = {p.id for p in projects}
    assert {p1.id, p2.id}.issubset(ids)


@pytest.mark.asyncio
async def test_resolve_project_scope_filters_archived_by_default(
    db_session: AsyncSession,
) -> None:
    visible = await _seed_project(db_session)
    archived = await _seed_project(db_session)
    archived.is_archived = True
    await db_session.flush()

    default = await report_service._resolve_project_scope(
        db_session, None, include_archived=False
    )
    default_ids = {p.id for p in default}
    assert visible.id in default_ids
    assert archived.id not in default_ids

    with_archived = await report_service._resolve_project_scope(
        db_session, None, include_archived=True
    )
    with_archived_ids = {p.id for p in with_archived}
    assert {visible.id, archived.id}.issubset(with_archived_ids)


@pytest.mark.asyncio
async def test_resolve_project_scope_explicit_ids_filter_unknown(
    db_session: AsyncSession,
) -> None:
    p = await _seed_project(db_session)
    projects = await report_service._resolve_project_scope(
        db_session, [p.id, 999_999], include_archived=False
    )
    assert [proj.id for proj in projects] == [p.id]


@pytest.mark.asyncio
async def test_resolve_project_scope_empty_list_returns_empty(
    db_session: AsyncSession,
) -> None:
    projects = await report_service._resolve_project_scope(
        db_session, [], include_archived=False
    )
    assert projects == []


# --- get_cross_project_report_analytics ---


@pytest.mark.asyncio
async def test_cross_project_analytics_empty_scope_returns_zeros(
    db_session: AsyncSession,
) -> None:
    resp = await report_service.get_cross_project_report_analytics(
        db_session, project_ids=[]
    )
    assert resp.project_ids == []
    assert resp.summary.total_test_runs == 0
    assert resp.summary.overall_pass_rate == 0.0
    assert resp.runs == []
    assert resp.per_project == []


@pytest.mark.asyncio
async def test_cross_project_analytics_aggregates_across_projects(
    db_session: AsyncSession,
) -> None:
    """Two projects, one completed run each — summary aggregates both;
    per_project rows agree with /projects/stats convention; runs list carries
    project_id and project_name on every row."""
    p1 = await _seed_project(db_session)
    p2 = await _seed_project(db_session)
    s1 = await _seed_suite(db_session, p1)
    s2 = await _seed_suite(db_session, p2)
    case_a = await _seed_case(db_session, s1, "A")
    case_b = await _seed_case(db_session, s2, "B")
    case_c = await _seed_case(db_session, s2, "C")

    now = datetime.now(UTC)
    r1 = await _seed_run(
        db_session, p1, name="P1-Run", status="completed", completed_at=now
    )
    r2 = await _seed_run(
        db_session, p2, name="P2-Run", status="completed", completed_at=now
    )
    await _seed_result(db_session, r1, case_a, "passed")  # p1: 1/1 = 1.0
    await _seed_result(db_session, r2, case_b, "failed")  # p2: 0/2 = 0.0
    await _seed_result(db_session, r2, case_c, "passed")  # (auto scope = 2 cases)

    resp = await report_service.get_cross_project_report_analytics(
        db_session, project_ids=[p1.id, p2.id]
    )

    assert resp.summary.total_test_runs == 2
    assert resp.summary.total_test_cases == 3
    # Mean of completed-run rates: (1.0 + 0.5) / 2
    assert resp.summary.overall_pass_rate == pytest.approx(0.75)

    by_project = {row.project_id: row for row in resp.per_project}
    assert by_project[p1.id].overall_pass_rate == pytest.approx(1.0)
    assert by_project[p1.id].completed_runs == 1
    assert by_project[p2.id].overall_pass_rate == pytest.approx(0.5)
    assert by_project[p2.id].total_results == 2

    by_run = {row.id: row for row in resp.runs}
    assert by_run[r1.id].project_id == p1.id
    assert by_run[r1.id].project_name == p1.name
    assert by_run[r2.id].project_id == p2.id
    assert by_run[r2.id].project_name == p2.name


@pytest.mark.asyncio
async def test_cross_project_analytics_summary_excludes_non_completed(
    db_session: AsyncSession,
) -> None:
    p = await _seed_project(db_session)
    s = await _seed_suite(db_session, p)
    case = await _seed_case(db_session, s, "A")
    planned = await _seed_run(db_session, p, name="Planned", status="planned")
    await _seed_result(db_session, planned, case, "passed")

    resp = await report_service.get_cross_project_report_analytics(
        db_session, project_ids=[p.id]
    )
    assert resp.summary.total_results == 0
    assert resp.summary.overall_pass_rate == 0.0
    assert resp.summary.active_runs == 1


@pytest.mark.asyncio
async def test_cross_project_analytics_archived_excluded_by_default(
    db_session: AsyncSession,
) -> None:
    visible = await _seed_project(db_session)
    archived = await _seed_project(db_session)
    archived.is_archived = True
    await db_session.flush()

    # Without include_archived, archived project is silently dropped from scope.
    resp = await report_service.get_cross_project_report_analytics(db_session)
    project_ids = {row.project_id for row in resp.per_project}
    assert visible.id in project_ids
    assert archived.id not in project_ids

    # Opt in.
    resp_with = await report_service.get_cross_project_report_analytics(
        db_session, include_archived=True
    )
    project_ids_with = {row.project_id for row in resp_with.per_project}
    assert {visible.id, archived.id}.issubset(project_ids_with)


@pytest.mark.asyncio
async def test_cross_project_analytics_date_filter_only_affects_runs_and_trend(
    db_session: AsyncSession,
) -> None:
    p = await _seed_project(db_session)
    s = await _seed_suite(db_session, p)
    case = await _seed_case(db_session, s, "A")
    now = datetime.now(UTC)
    old_run = await _seed_run(
        db_session, p, name="Old", status="completed",
        completed_at=now - timedelta(days=60),
    )
    recent_run = await _seed_run(
        db_session, p, name="Recent", status="completed",
        completed_at=now - timedelta(days=1),
    )
    await _seed_result(db_session, old_run, case, "passed")
    await _seed_result(db_session, recent_run, case, "failed")

    resp = await report_service.get_cross_project_report_analytics(
        db_session,
        project_ids=[p.id],
        date_from=now - timedelta(days=7),
        date_to=now,
    )
    # Summary stays scope-wide (both runs counted).
    assert resp.summary.total_test_runs == 2
    # Runs list is filtered to the window.
    assert {r.id for r in resp.runs} == {recent_run.id}


@pytest.mark.asyncio
async def test_cross_project_analytics_status_filter(
    db_session: AsyncSession,
) -> None:
    p = await _seed_project(db_session)
    await _seed_run(db_session, p, name="A", status="planned")
    completed = await _seed_run(
        db_session, p, name="B", status="completed", completed_at=datetime.now(UTC)
    )
    resp = await report_service.get_cross_project_report_analytics(
        db_session, project_ids=[p.id], run_status="completed"
    )
    assert [r.id for r in resp.runs] == [completed.id]


@pytest.mark.asyncio
async def test_cross_project_analytics_include_trend_false_skips_trend(
    db_session: AsyncSession,
) -> None:
    p = await _seed_project(db_session)
    s = await _seed_suite(db_session, p)
    case = await _seed_case(db_session, s, "A")
    run = await _seed_run(
        db_session, p, status="completed", completed_at=datetime.now(UTC)
    )
    await _seed_result(db_session, run, case, "passed")

    resp = await report_service.get_cross_project_report_analytics(
        db_session, project_ids=[p.id], include_trend=False
    )
    assert resp.trend == []


@pytest.mark.asyncio
async def test_cross_project_analytics_per_project_matches_per_project_endpoint(
    db_session: AsyncSession,
) -> None:
    """Per-project breakdown rows should agree with get_report_analytics for
    each project (mean-of-run-rates rule, plan 041)."""
    p = await _seed_project(db_session)
    s = await _seed_suite(db_session, p)
    case_a = await _seed_case(db_session, s, "A")
    case_b = await _seed_case(db_session, s, "B")

    now = datetime.now(UTC)
    r1 = await _seed_run(db_session, p, status="completed", completed_at=now)
    r2 = await _seed_run(db_session, p, status="completed", completed_at=now)
    await _seed_result(db_session, r1, case_a, "passed")
    await _seed_result(db_session, r2, case_a, "passed")
    await _seed_result(db_session, r2, case_b, "failed")

    per_project_resp = await report_service.get_report_analytics(db_session, p.id)
    cross_resp = await report_service.get_cross_project_report_analytics(
        db_session, project_ids=[p.id]
    )

    breakdown = next(
        row for row in cross_resp.per_project if row.project_id == p.id
    )
    assert breakdown.overall_pass_rate == pytest.approx(
        per_project_resp.summary.overall_pass_rate
    )


# --- Pass-rate rounding regressions (plan 044) ---


def _has_at_most_n_decimals(value: float, n: int = 3) -> bool:
    """Helper: True when `value` is an int-like float, 0/1, or has ≤ n decimals."""
    if value in (0.0, 1.0):
        return True
    parts = str(value).split(".")
    if len(parts) == 1:
        return True
    return len(parts[1]) <= n


@pytest.mark.asyncio
async def test_report_analytics_pass_rates_rounded_to_three_decimals(
    db_session: AsyncSession,
) -> None:
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case_a = await _seed_case(db_session, suite, "A")
    case_b = await _seed_case(db_session, suite, "B")
    case_c = await _seed_case(db_session, suite, "C")
    now = datetime.now(UTC)
    run = await _seed_run(
        db_session, project, status="completed", completed_at=now
    )
    await _seed_result(db_session, run, case_a, "passed", tested_at=now)
    await _seed_result(db_session, run, case_b, "failed", tested_at=now)
    await _seed_result(db_session, run, case_c, "blocked", tested_at=now)

    resp = await report_service.get_report_analytics(db_session, project.id)
    assert _has_at_most_n_decimals(resp.summary.overall_pass_rate)
    for r in resp.runs:
        if r.pass_rate is not None:
            assert _has_at_most_n_decimals(r.pass_rate)
    for tp in resp.trend:
        if tp.pass_rate is not None:
            assert _has_at_most_n_decimals(tp.pass_rate)


@pytest.mark.asyncio
async def test_cross_project_analytics_pass_rates_rounded_to_three_decimals(
    db_session: AsyncSession,
) -> None:
    p1 = await _seed_project(db_session)
    p2 = await _seed_project(db_session)
    s1 = await _seed_suite(db_session, p1)
    s2 = await _seed_suite(db_session, p2)
    case_a = await _seed_case(db_session, s1, "A")
    case_b = await _seed_case(db_session, s2, "B")
    case_c = await _seed_case(db_session, s2, "C")
    now = datetime.now(UTC)
    r1 = await _seed_run(db_session, p1, status="completed", completed_at=now)
    r2 = await _seed_run(db_session, p2, status="completed", completed_at=now)
    await _seed_result(db_session, r1, case_a, "passed", tested_at=now)
    await _seed_result(db_session, r2, case_b, "failed", tested_at=now)
    await _seed_result(db_session, r2, case_c, "passed", tested_at=now)

    resp = await report_service.get_cross_project_report_analytics(
        db_session, project_ids=[p1.id, p2.id]
    )
    assert _has_at_most_n_decimals(resp.summary.overall_pass_rate)
    for r in resp.runs:
        if r.pass_rate is not None:
            assert _has_at_most_n_decimals(r.pass_rate)
    for row in resp.per_project:
        if row.overall_pass_rate is not None:
            assert _has_at_most_n_decimals(row.overall_pass_rate)


@pytest.mark.asyncio
async def test_report_analytics_mean_of_run_rates_rounds_after_aggregation(
    db_session: AsyncSession,
) -> None:
    """Three completed runs with rates 1/3, 2/3, 1.0 → mean 0.667 (rounded
    after aggregation). Critically, this is NOT mean([0.333, 0.667, 1.000])
    = 0.667 by coincidence — it's the unrounded mean rounded once at the end.
    """
    project = await _seed_project(db_session)
    suite = await _seed_suite(db_session, project)
    case_a = await _seed_case(db_session, suite, "A")
    case_b = await _seed_case(db_session, suite, "B")
    case_c = await _seed_case(db_session, suite, "C")
    now = datetime.now(UTC)

    # Run 1: 1/3 passed
    r1 = await _seed_run(db_session, project, status="completed", completed_at=now)
    await _seed_result(db_session, r1, case_a, "passed", tested_at=now)
    await _seed_result(db_session, r1, case_b, "failed", tested_at=now)
    await _seed_result(db_session, r1, case_c, "failed", tested_at=now)

    # Run 2: 2/3 passed
    r2 = await _seed_run(db_session, project, status="completed", completed_at=now)
    await _seed_result(db_session, r2, case_a, "passed", tested_at=now)
    await _seed_result(db_session, r2, case_b, "passed", tested_at=now)
    await _seed_result(db_session, r2, case_c, "failed", tested_at=now)

    # Run 3: 3/3 passed
    r3 = await _seed_run(db_session, project, status="completed", completed_at=now)
    await _seed_result(db_session, r3, case_a, "passed", tested_at=now)
    await _seed_result(db_session, r3, case_b, "passed", tested_at=now)
    await _seed_result(db_session, r3, case_c, "passed", tested_at=now)

    resp = await report_service.get_report_analytics(db_session, project.id)
    # Expected: round((0.333 + 0.667 + 1.000) / 3, 3) = round(0.667, 3) = 0.667
    assert resp.summary.overall_pass_rate == 0.667
