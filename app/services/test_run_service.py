from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.mixins import not_deleted
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun, test_run_test_cases
from app.models.test_suite import TestSuite
from app.schemas.test_run import TestRunCreate, TestRunProgress, TestRunUpdate
from app.services import audit_service, realtime_service
from app.utils import stats


async def _get_project(db: AsyncSession, project_id: int) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, not_deleted(Project))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def _validate_case_ids_for_project(
    db: AsyncSession, project_id: int, case_ids: list[int]
) -> None:
    if len(case_ids) > 1000:
        raise BadRequestError("Cannot include more than 1000 test cases in a run")
    unique_ids = set(case_ids)
    result = await db.execute(
        select(TestCase.id)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(TestSuite.project_id == project_id, TestCase.id.in_(unique_ids))
    )
    valid_ids = {row[0] for row in result.all()}
    invalid = unique_ids - valid_ids
    if invalid:
        raise BadRequestError(
            f"Test case ids not found in project {project_id}: {sorted(invalid)}"
        )


async def get_run(
    db: AsyncSession, run_id: int, allow_deleted: bool = False
) -> TestRun:
    query = select(TestRun).where(TestRun.id == run_id)
    if not allow_deleted:
        query = query.where(not_deleted(TestRun))
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"TestRun {run_id} not found")
    return run


async def list_runs(
    db: AsyncSession,
    project_id: int,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
    include_progress: bool = False,
) -> tuple[list[TestRun], int, dict[int, TestRunProgress] | None]:
    await _get_project(db, project_id)
    query = select(TestRun).where(TestRun.project_id == project_id)
    if not include_deleted:
        query = query.where(not_deleted(TestRun))
    if status is not None:
        query = query.where(TestRun.status == status)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total: int = count_result.scalar_one()

    offset = (page - 1) * page_size
    result = await db.execute(
        query.order_by(TestRun.created_at.desc()).offset(offset).limit(page_size)
    )
    runs = list(result.scalars().all())

    progress_map: dict[int, TestRunProgress] | None = None
    if include_progress and runs:
        progress_map = await batch_run_progress(db, runs)

    return runs, total, progress_map


async def batch_run_progress(
    db: AsyncSession, runs: list[TestRun]
) -> dict[int, TestRunProgress]:
    run_ids = [r.id for r in runs]
    counts_per_run: dict[int, dict[str, int]] = {rid: {} for rid in run_ids}

    # Total case-set per run: explicit runs → junction count; auto runs → derived
    explicit_runs = [r for r in runs if r.cases_mode == "explicit"]
    auto_runs = [r for r in runs if r.cases_mode != "explicit"]

    totals: dict[int, int] = {}

    if explicit_runs:
        explicit_ids = [r.id for r in explicit_runs]
        explicit_rows = (
            await db.execute(
                select(
                    test_run_test_cases.c.test_run_id,
                    func.count(test_run_test_cases.c.test_case_id),
                )
                .where(test_run_test_cases.c.test_run_id.in_(explicit_ids))
                .group_by(test_run_test_cases.c.test_run_id)
            )
        ).all()
        explicit_totals = {rid: n for rid, n in explicit_rows}
        for r in explicit_runs:
            totals[r.id] = explicit_totals.get(r.id, 0)

        # Status counts restricted to cases still in the junction (no orphans).
        explicit_status_rows = (
            await db.execute(
                select(
                    TestResult.test_run_id,
                    TestResult.status,
                    func.count(TestResult.id),
                )
                .join(
                    test_run_test_cases,
                    and_(
                        test_run_test_cases.c.test_run_id
                        == TestResult.test_run_id,
                        test_run_test_cases.c.test_case_id
                        == TestResult.test_case_id,
                    ),
                )
                .where(
                    TestResult.test_run_id.in_(explicit_ids),
                    not_deleted(TestResult),
                )
                .group_by(TestResult.test_run_id, TestResult.status)
            )
        ).all()
        for run_id, status, count in explicit_status_rows:
            counts_per_run[run_id][status] = count

    for r in auto_runs:
        auto_scope_q = (
            select(TestCase.id)
            .join(TestSuite, TestCase.suite_id == TestSuite.id)
            .where(
                TestSuite.project_id == r.project_id,
                not_deleted(TestCase),
                not_deleted(TestSuite),
            )
        )
        if r.suite_id is not None:
            auto_scope_q = auto_scope_q.where(TestCase.suite_id == r.suite_id)

        totals[r.id] = (
            await db.execute(select(func.count()).select_from(auto_scope_q.subquery()))
        ).scalar_one()

        auto_status = (
            await db.execute(
                select(TestResult.status, func.count(TestResult.id))
                .where(
                    TestResult.test_run_id == r.id,
                    TestResult.test_case_id.in_(auto_scope_q),
                    not_deleted(TestResult),
                )
                .group_by(TestResult.status)
            )
        ).all()
        for status, count in auto_status:
            counts_per_run[r.id][status] = count

    result: dict[int, TestRunProgress] = {}
    for r in runs:
        counts = counts_per_run.get(r.id, {})
        tested = sum(counts.values())
        total = totals.get(r.id, 0)
        passed = counts.get("passed", 0)
        denominator = max(total, tested)
        no_run = counts.get("no_run", 0) + max(0, total - tested)
        result[r.id] = TestRunProgress(
            passed=passed,
            failed=counts.get("failed", 0),
            blocked=counts.get("blocked", 0),
            no_run=no_run,
            total=total,
            pass_rate=stats.pass_rate(passed, denominator),
        )
    return result


async def create_run(
    db: AsyncSession,
    project_id: int,
    data: TestRunCreate,
    user_id: int | None = None,
) -> TestRun:
    await _get_project(db, project_id)

    if data.suite_id is not None:
        suite_result = await db.execute(
            select(TestSuite).where(
                TestSuite.id == data.suite_id,
                TestSuite.project_id == project_id,
                not_deleted(TestSuite),
            )
        )
        if suite_result.scalar_one_or_none() is None:
            raise BadRequestError(
                f"Suite {data.suite_id} not found in project {project_id}"
            )

    if data.include_test_cases is not None and len(data.include_test_cases) > 0:
        await _validate_case_ids_for_project(db, project_id, data.include_test_cases)

    run = TestRun(
        project_id=project_id,
        name=data.name,
        suite_id=data.suite_id,
        milestone_id=data.milestone_id,
        assigned_to=data.assigned_to,
        config=data.config,
        status="planned",
        cases_mode="explicit" if data.include_test_cases is not None else "auto",
    )
    db.add(run)
    await db.flush()

    if data.include_test_cases is not None:
        unique_ids = list(dict.fromkeys(data.include_test_cases))
        if unique_ids:
            await db.execute(
                insert(test_run_test_cases),
                [
                    {"test_run_id": run.id, "test_case_id": cid}
                    for cid in unique_ids
                ],
            )
            await db.flush()

    await db.refresh(run)
    await audit_service.log_action(db, user_id, "CREATE", "TestRun", run.id)
    return run


async def update_run(db: AsyncSession, run_id: int, data: TestRunUpdate) -> TestRun:
    run = await get_run(db, run_id)
    old_status = run.status

    if data.name is not None:
        run.name = data.name
    if "suite_id" in data.model_fields_set:
        run.suite_id = data.suite_id
    if "milestone_id" in data.model_fields_set:
        run.milestone_id = data.milestone_id
    if "assigned_to" in data.model_fields_set:
        run.assigned_to = data.assigned_to
    if data.status is not None:
        run.status = data.status
    if data.config is not None:
        run.config = data.config

    await db.flush()
    await db.refresh(run)

    if run.status != old_status:
        await realtime_service.publish_run_status(
            run_id=run.id, project_id=run.project_id, status=run.status
        )

    return run


async def close_run(
    db: AsyncSession, run_id: int, user_id: int | None = None
) -> TestRun:
    run = await get_run(db, run_id)
    if run.status == "completed":
        return run
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(run)

    await realtime_service.publish_run_status(
        run_id=run.id, project_id=run.project_id, status=run.status
    )
    await audit_service.log_action(
        db, user_id, "UPDATE", "TestRun", run.id, changes={"status": "completed"}
    )
    return run


async def transition_to_active(
    db: AsyncSession, run_id: int, user_id: int | None = None
) -> bool:
    """Flip a `planned` run to `active` on the first real result write.

    Idempotent and safe under concurrent submissions: the guarded UPDATE only
    touches the row when it is still `planned`, so a second caller sees
    rowcount == 0 and no duplicate event or audit entry is emitted.
    Returns True when the row actually transitioned.
    """
    result = await db.execute(
        update(TestRun)
        .where(
            TestRun.id == run_id,
            TestRun.status == "planned",
            not_deleted(TestRun),
        )
        .values(status="active")
    )
    if result.rowcount == 0:
        return False

    run = await get_run(db, run_id)
    await realtime_service.publish_run_status(
        run_id=run.id, project_id=run.project_id, status=run.status
    )
    await audit_service.log_action(
        db, user_id, "UPDATE", "TestRun", run.id, changes={"status": "active"}
    )
    return True


async def delete_run(
    db: AsyncSession, run_id: int, user_id: int | None = None
) -> None:
    run = await get_run(db, run_id)
    now = func.now()
    run.deleted_at = now
    await db.execute(
        update(TestResult)
        .where(TestResult.test_run_id == run_id, not_deleted(TestResult))
        .values(deleted_at=now)
    )
    await audit_service.log_action(db, user_id, "DELETE", "TestRun", run_id)
    await db.flush()


async def restore_run(
    db: AsyncSession, run_id: int, user_id: int | None = None
) -> TestRun:
    run = await get_run(db, run_id, allow_deleted=True)
    if run.deleted_at is None:
        raise BadRequestError(f"TestRun {run_id} is not deleted")

    project_result = await db.execute(
        select(Project).where(Project.id == run.project_id)
    )
    project = project_result.scalar_one_or_none()
    if project is None or project.deleted_at is not None:
        raise BadRequestError("Cannot restore run: parent project is deleted")

    run.deleted_at = None
    await db.flush()
    await db.refresh(run)
    await audit_service.log_action(db, user_id, "RESTORE", "TestRun", run.id)
    return run


async def get_progress(db: AsyncSession, run_id: int) -> TestRunProgress:
    run = await get_run(db, run_id)

    # Subquery: ids of cases currently in this run's scope.
    if run.cases_mode == "explicit":
        scope_q = select(test_run_test_cases.c.test_case_id).where(
            test_run_test_cases.c.test_run_id == run_id
        )
    else:
        scope_q = (
            select(TestCase.id)
            .join(TestSuite, TestCase.suite_id == TestSuite.id)
            .where(
                TestSuite.project_id == run.project_id,
                not_deleted(TestCase),
                not_deleted(TestSuite),
            )
        )
        if run.suite_id is not None:
            scope_q = scope_q.where(TestCase.suite_id == run.suite_id)

    total: int = (
        await db.execute(select(func.count()).select_from(scope_q.subquery()))
    ).scalar_one()

    # Count results by status — restricted to cases still in scope, so orphan
    # results (case removed after submit) don't inflate the counts.
    counts_result = await db.execute(
        select(TestResult.status, func.count(TestResult.id))
        .where(
            TestResult.test_run_id == run_id,
            TestResult.test_case_id.in_(scope_q),
            not_deleted(TestResult),
        )
        .group_by(TestResult.status)
    )
    counts: dict[str, int] = {row[0]: row[1] for row in counts_result}

    tested = sum(counts.values())
    passed = counts.get("passed", 0)
    # After scoping, tested <= total by construction, but keep max() as a safety
    # net so pass_rate stays in [0, 1] if the two queries race against writes.
    denominator = max(total, tested)
    no_run = counts.get("no_run", 0) + max(0, total - tested)

    return TestRunProgress(
        passed=passed,
        failed=counts.get("failed", 0),
        blocked=counts.get("blocked", 0),
        no_run=no_run,
        total=total,
        pass_rate=stats.pass_rate(passed, denominator),
    )


_CASE_SORT_COLUMNS = {
    "suite_id,id": (TestCase.suite_id, TestCase.id),
    "id": (TestCase.id,),
    "title": (TestCase.title, TestCase.id),
    "priority": (TestCase.priority, TestCase.id),
    "suite": (TestCase.suite_id, TestCase.id),
}


async def get_with_cases(
    db: AsyncSession,
    run_id: int,
    *,
    limit: int = 500,
    offset: int = 0,
    sort: str = "suite_id,id",
) -> dict[str, Any]:
    if sort not in _CASE_SORT_COLUMNS:
        raise BadRequestError(
            f"Invalid sort '{sort}'. Allowed: {sorted(_CASE_SORT_COLUMNS)}"
        )
    if limit < 1 or limit > 2000:
        raise BadRequestError("limit must be between 1 and 2000")
    if offset < 0:
        raise BadRequestError("offset must be >= 0")

    run = await get_run(db, run_id)

    if run.cases_mode == "explicit":
        base = (
            select(TestCase)
            .join(
                test_run_test_cases,
                test_run_test_cases.c.test_case_id == TestCase.id,
            )
            .where(test_run_test_cases.c.test_run_id == run_id)
        )
    else:
        base = (
            select(TestCase)
            .join(TestSuite, TestCase.suite_id == TestSuite.id)
            .where(TestSuite.project_id == run.project_id)
        )
        if run.suite_id is not None:
            base = base.where(TestCase.suite_id == run.suite_id)

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    cases_query = (
        base.add_columns(TestResult)
        .outerjoin(
            TestResult,
            and_(
                TestResult.test_case_id == TestCase.id,
                TestResult.test_run_id == run_id,
            ),
        )
        .options(
            selectinload(TestCase.tags),
            selectinload(TestResult.attachments),
        )
        .order_by(*_CASE_SORT_COLUMNS[sort])
        .offset(offset)
        .limit(limit)
    )

    rows = await db.execute(cases_query)
    case_list: list[dict[str, Any]] = []
    for tc, res in rows.all():
        case_dict: dict[str, Any] = {
            col: getattr(tc, col) for col in tc.__table__.columns.keys()
        }
        case_dict["case_status"] = tc.status
        case_dict["tags"] = [t.name for t in tc.tags]
        case_dict["result"] = res
        case_list.append(case_dict)

    return {"run": run, "cases": case_list, "total": total}


def _case_to_dict(tc: TestCase, res: TestResult | None) -> dict[str, Any]:
    case_dict: dict[str, Any] = {
        col: getattr(tc, col) for col in tc.__table__.columns.keys()
    }
    case_dict["case_status"] = tc.status
    case_dict["tags"] = [t.name for t in tc.tags]
    case_dict["result"] = res
    return case_dict


async def get_suite_tree(db: AsyncSession, run_id: int) -> dict[str, Any]:
    run = await get_run(db, run_id)

    if run.cases_mode == "explicit":
        base = (
            select(TestCase, TestResult)
            .join(
                test_run_test_cases,
                test_run_test_cases.c.test_case_id == TestCase.id,
            )
            .where(test_run_test_cases.c.test_run_id == run_id)
            .outerjoin(
                TestResult,
                and_(
                    TestResult.test_case_id == TestCase.id,
                    TestResult.test_run_id == run_id,
                ),
            )
            .where(not_deleted(TestCase))
            .options(
                selectinload(TestCase.tags),
                selectinload(TestResult.attachments),
            )
        )
    else:
        base = (
            select(TestCase, TestResult)
            .join(TestSuite, TestCase.suite_id == TestSuite.id)
            .outerjoin(
                TestResult,
                and_(
                    TestResult.test_case_id == TestCase.id,
                    TestResult.test_run_id == run_id,
                ),
            )
            .where(
                TestSuite.project_id == run.project_id,
                not_deleted(TestCase),
                not_deleted(TestSuite),
            )
            .options(
                selectinload(TestCase.tags),
                selectinload(TestResult.attachments),
            )
        )
        if run.suite_id is not None:
            base = base.where(TestCase.suite_id == run.suite_id)

    rows = (await db.execute(base.order_by(TestCase.id))).all()

    suite_ids = {tc.suite_id for tc, _ in rows}
    suites_by_id: dict[int, TestSuite] = {}
    if suite_ids:
        suite_rows = (
            await db.execute(
                select(TestSuite).where(
                    TestSuite.id.in_(suite_ids), not_deleted(TestSuite)
                )
            )
        ).scalars().all()
        suites_by_id = {s.id: s for s in suite_rows}

    nodes: dict[int, dict[str, Any]] = {
        sid: {
            "suite": {
                "id": s.id,
                "name": s.name,
                "parent_id": s.parent_suite_id,
            },
            "progress": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "blocked": 0,
                "no_run": 0,
                "other": 0,
            },
            "cases": [],
            "children": [],
        }
        for sid, s in suites_by_id.items()
    }

    total = 0
    for tc, res in rows:
        node = nodes.get(tc.suite_id)
        if node is None:
            continue
        node["cases"].append(_case_to_dict(tc, res))
        node["progress"]["total"] += 1
        total += 1
        status = res.status if res is not None else "no_run"
        if status in ("passed", "failed", "blocked", "no_run"):
            node["progress"][status] += 1
        else:
            node["progress"]["other"] += 1

    roots: list[dict[str, Any]] = []
    for sid, node in nodes.items():
        parent_id = node["suite"]["parent_id"]
        if parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)

    return {"run": run, "roots": roots, "total": total}


async def set_run_cases(
    db: AsyncSession, run_id: int, case_ids: list[int]
) -> None:
    run = await get_run(db, run_id)
    if run.status == "completed":
        raise ConflictError(
            "Cannot edit cases on a completed test run"
        )
    unique_ids = list(dict.fromkeys(case_ids))

    if unique_ids:
        await _validate_case_ids_for_project(db, run.project_id, unique_ids)

    await db.execute(
        delete(test_run_test_cases).where(
            test_run_test_cases.c.test_run_id == run_id
        )
    )
    if unique_ids:
        await db.execute(
            insert(test_run_test_cases),
            [{"test_run_id": run_id, "test_case_id": cid} for cid in unique_ids],
        )
    run.cases_mode = "explicit"
    await db.flush()
