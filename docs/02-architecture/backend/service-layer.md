# Service Layer

Business logic patterns in Testoria.

---

## Overview

The service layer (`app/services/`) is where all business logic lives. Routers call services; services call models via SQLAlchemy. No business logic in routers, no HTTP concepts in services.

---

## Service function pattern

Each domain has a service module with module-level async functions (no classes — the DB session is passed in as the first argument):

```python
# app/services/project_service.py
async def get_project(db: AsyncSession, project_id: int) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def create_project(db: AsyncSession, data: ProjectCreate) -> Project:
    project = Project(name=data.name, description=data.description)
    db.add(project)
    await db.flush()   # flush to get the ID, commit happens in get_db
    await db.refresh(project)
    return project
```

`await db.flush()` makes the object available within the transaction without committing. The `get_db` dependency commits on success, so services do not call `db.commit()` directly.

---

## When to use `flush` vs `commit`

| Operation | Use |
|-----------|-----|
| Write an object and need its generated `id` immediately | `await db.flush()` + `await db.refresh(obj)` |
| Explicit transaction boundary (e.g. bulk import rollback-on-error) | `async with db.begin_nested()` (savepoint) |
| Normal CRUD | `await db.flush()` — `get_db` commits after the request completes |

Important: always call `await db.refresh(obj)` after flush before returning ORM objects that will be serialized by Pydantic. This prevents `MissingGreenlet` errors caused by lazy-loading of expired attributes (especially `updated_at` with `onupdate=func.now()`).

---

## Cross-entity operations

When an operation touches multiple entities, the service handles the orchestration:

```python
# app/services/test_result_service.py — upsert + history recording
async def submit(db, run_id, data, user_id):
    # 1. Check if result already exists (UNIQUE run + case)
    existing = await db.execute(
        select(TestResult).where(
            TestResult.test_run_id == run_id,
            TestResult.test_case_id == data.test_case_id,
        )
    )
    tr = existing.scalar_one_or_none()

    if tr is not None:
        # Update existing
        for field, value in data.model_dump(exclude={"test_case_id"}).items():
            setattr(tr, field, value)
        tr.tested_by = user_id
    else:
        # Create new
        tr = TestResult(test_run_id=run_id, tested_by=user_id, **data.model_dump())
        db.add(tr)

    await db.flush()

    # 2. Append history row
    await _record_history(db, tr.id, tr.status, tr.comment, user_id)
    await db.flush()

    await db.refresh(tr)
    return tr
```

---

## Computed fields

Computed values (e.g., test run progress, pass rate) are calculated in the service and returned as dedicated schemas, not stored in the DB.

**Run-scope consistency invariant.** `get_with_cases`, `get_progress`, `get_suite_tree`, `list_results`, and `batch_run_progress` all derive the run's case-set from the same rule — junction rows for `cases_mode="explicit"`, project/suite-derived (excluding soft-deleted cases and suites) for `"auto"`. Status counts and result listings are restricted to cases currently in that set, so `passed + failed + blocked + no_run == total` holds on `TestRunProgress` and orphan `TestResult` rows don't leak into reads. `GET /results?include_orphans=true` is the only opt-in escape hatch.

**Single source of truth for per-run pass rate (plan 041).** `batch_run_progress` (public since plan 041, renamed from `_batch_run_progress`) is the only place that derives a run's `pass_rate`. `project_service.get_stats` / `get_bulk_stats`, `report_service.get_report_analytics`, and `report_service.get_cross_project_report_analytics` (plan 043) delegate to it rather than compute their own per-run rate, so the run-list endpoint, Dashboard tile, per-project breakdown, and Reports KPI all show the same number for the same run. Denominator is `max(cases_in_scope, tested)` — untested cases count against the rate.

`pass_rate` everywhere goes through `app/utils/stats.pass_rate` (plan 035) and is a ratio in `[0, 1]` over all statuses. **Raw values flow through `batch_run_progress` and the mean-of-run-rates aggregations** so rounding never happens before averaging — `mean([round(1/3), 1.0]) = 0.666` vs `round(mean([1/3, 1.0])) = 0.667` (plan 044). Response-boundary rounding to 3 decimals (= 1 decimal of percent) lives at three sites: callers wrap the value in `stats.round_ratio()` when populating a response field, the local `report_service._pass_rate` helper composes both for run-list / trend rows, and `TestRunProgress.pass_rate` rounds via a Pydantic `field_serializer` so the in-memory value stays raw for downstream aggregation.

```python
# app/services/test_run_service.py (simplified)
async def get_progress(db: AsyncSession, run_id: int) -> TestRunProgress:
    run = await get_run(db, run_id)
    scope_q = _run_scope_case_ids(run)   # junction or project/suite subquery
    total = await _count(scope_q)
    counts = await _grouped_status_counts(run_id, scope_q)
    no_run = counts.get("no_run", 0) + max(0, total - sum(counts.values()))
    return TestRunProgress(
        passed=counts.get("passed", 0),
        failed=counts.get("failed", 0),
        blocked=counts.get("blocked", 0),
        no_run=no_run,
        total=total,
        pass_rate=stats.pass_rate(counts.get("passed", 0), total),
    )
```

---

## Service inventory

| Service | File | Responsibility |
|---------|------|----------------|
| `user_service` | `user_service.py` | User CRUD, bulk create, CSV/Excel export. Password optional on create → unusable random hash; enqueues a welcome set-password invite in the same transaction; `set_password` backs the reset flow (plan 048). |
| `project_service` | `project_service.py` | Project CRUD, stats (cases/suites/runs/pass_rate). `pass_rate` counts only results from runs with `status='completed'` (plan 039). |
| `test_suite_service` | `test_suite_service.py` | Suite CRUD, parent/project validation. `delete_suite` cascades soft-delete across the full descendant subtree (`parent_suite_id` chain) + every TestCase under it, via a recursive Postgres CTE — plan 045 / TES-70. |
| `test_case_service` | `test_case_service.py` | Test case CRUD, search/filter (delegates tag resolution to `tag_service`) |
| `tag_service` | `tag_service.py` | Tag list, prefix search, idempotent create, `get_or_create_many` (extracted from `test_case_service._resolve_tags`) |
| `milestone_service` | `milestone_service.py` | Milestone CRUD |
| `test_run_service` | `test_run_service.py` | Run CRUD, close, progress, get_with_cases, explicit case selection (`set_run_cases`), `transition_to_active` (idempotent planned→active flip triggered by the result service — plan 039). |
| `test_result_service` | `test_result_service.py` | Result upsert, update, history, per-step validation, attachment upload/delete. `submit_many` is the batch path for CI imports: validates the run once, fetches cases in one `IN` query, transitions the run at most once and publishes a single aggregate event — it shares `_should_record_history` with `submit` so history semantics cannot drift (plan 050). Calls `test_run_service.transition_to_active()` after any meaningful result write so the first submit/update on a `planned` run flips it to `active` in the same transaction (plan 039). |
| `report_service` | `report_service.py` | Dashboard + report analytics + custom reports. Pass-rate / result-distribution / trend count only results from completed runs (plan 039); `active_runs` KPI counts `planned + active`. |
| `import_service` | `import_service.py` | CSV/Excel parse → bulk test case creation |
| `export_service` | `export_service.py` | Test case → CSV/Excel bytes |
| `realtime_service` | `realtime_service.py` | Centrifugo event publishing (fire-and-forget) |
| `audit_service` | `audit_service.py` | Entity change audit logging (`log_action`) |
| `ci_service` | `ci_service.py` | CI webhook handling, **legacy** JUnit XML import (title matching), badge SVG generation. New integrations use `result_import_service` — see tech-debt for retirement. |
| `result_import_service` | `result_import_service.py` | Parse JUnit XML / JSON, resolve each entry to a TestCase (`automation_id` before `title`, via `dotted()` for pytest node ids), and submit the matches through `test_result_service.submit_many`. Returns a `ResultImportReport` naming every unmatched case and why (plan 050). |
| `api_key_service` | `api_key_service.py` | Mint / list / revoke API keys; `resolve()` turns an `X-API-Key` header into `(key, owner)`; `effective_role()` computes `min(key.role, owner.role, API_KEY_MAX_ROLE)` — the reason no key can satisfy `require_role(LEAD, ADMIN)` (plan 050). |
| `defect_service` | `defect_service.py` | Jira/GitHub/GitLab defect creation via external APIs |
| `email_service` | `email_service.py` | Mint a single-use token, build the `/set-password` or `/reset-password` link, enqueue the outbox row — `queue_welcome_invite`, `queue_password_reset` (plan 048). |
| `email_outbox_service` | `email_outbox_service.py` | Durable email queue: `enqueue` (joins caller's txn), `claim_batch` (`FOR UPDATE SKIP LOCKED` → `sending`), `mark_sent` / `mark_failed` (exponential backoff, `failed` at `max_attempts`), `requeue_orphaned_sending` (plan 048). |
| `password_token_service` | `password_token_service.py` | Single-use, expiring Redis tokens: `create_token`, `peek_token`, `consume_token` (GETDEL). Shared by invite + reset (plan 048). |
