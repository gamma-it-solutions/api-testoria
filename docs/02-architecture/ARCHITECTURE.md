# ARCHITECTURE — Testoria Backend

## What this is

Testoria is a test management platform. This is the FastAPI backend that exposes a REST API consumed by the Vue 3 frontend and the Python CLI tool. The backend owns the PostgreSQL database, file storage, and the event publishing pipeline to Centrifugo.

---

## Codemap

```
app/
├── main.py          — FastAPI app: routers wired, middleware configured, health endpoint; lifespan starts the email drain worker + closes Redis
├── config.py        — Pydantic Settings (reads .env). Single source for all config.
├── database.py      — Async SQLAlchemy engine + AsyncSessionLocal factory + get_db dependency + Base
├── dependencies.py  — FastAPI Depends: get_current_user, require_role()
│
├── api/v1/          — HTTP layer. One file per domain. Thin: validate input → call service → return schema.
│   ├── auth.py          POST /auth/login, /auth/refresh, /auth/logout, GET /auth/me, POST /auth/forgot-password, /auth/reset-password, GET /auth/reset-password/validate (no public register — invite-only, plan 049)
│   ├── users.py         User management (Lead or Admin; Lead capped at Lead) + roles list
│   ├── projects.py      Project CRUD + stats
│   ├── test_suites.py   Suite CRUD (nested under projects + standalone by ID)
│   ├── test_cases.py    Test case CRUD + search/filter + import/export
│   ├── tags.py          Tag list/search/create (idempotent)
│   ├── milestones.py    Milestone CRUD (nested under projects + standalone by ID)
│   ├── test_runs.py     Test run CRUD + close + progress + cases-with-results
│   ├── test_results.py  Result submit/update + history + attachment upload/delete
│   ├── reports.py       Dashboard, run report (JSON/PDF/Excel), metrics, custom report
│   ├── ci_integration.py  CI webhooks, JUnit XML bulk submit, badge SVG
│   ├── defects.py       Jira/GitHub/GitLab defect creation from test results
│   └── websocket.py     Centrifugo connection/subscription token endpoints
│
├── models/          — SQLAlchemy ORM models. One file per DB table group.
│   ├── mixins.py           SoftDeleteMixin (deleted_at + is_deleted) + not_deleted() helper
│   ├── user.py             User (soft-delete)
│   ├── project.py          Project (soft-delete)
│   ├── test_suite.py       TestSuite (self-referential hierarchy, soft-delete)
│   ├── test_case.py        TestCase (JSON steps, M2M tags, soft-delete)
│   ├── tag.py              Tag + test_case_tags join table
│   ├── milestone.py        Milestone
│   ├── test_run.py         TestRun (JSON config, FK to suite/milestone/user) + test_run_test_cases M2M join table
│   ├── test_result.py      TestResult (UNIQUE run+case, upsert semantics)
│   ├── result_attachment.py  ResultAttachment (object_key in MinIO/S3, storage_backend default "s3"; plan 042)
│   ├── result_history.py    ResultHistory (append-only audit trail)
│   ├── audit_log.py         AuditLog (entity change tracking)
│   └── email_outbox.py      EmailOutbox (durable email queue; pending→sending→sent/failed; plan 048)
│
├── schemas/         — Pydantic request/response models. Never expose ORM models directly.
│   ├── token.py         Token, TokenPayload
│   ├── auth.py          ForgotPasswordRequest, ResetPasswordRequest, ResetTokenValidateResponse, MessageResponse (plan 048)
│   ├── user.py          UserCreate (password optional), UserUpdate, UserResponse, UserBulkCreate, PaginatedResponse[T], RoleResponse
│   ├── project.py       ProjectCreate, ProjectUpdate, ProjectResponse, ProjectStats
│   ├── test_suite.py    TestSuiteCreate, TestSuiteUpdate, TestSuiteResponse
│   ├── test_case.py     TestStep, TestCaseCreate, TestCaseUpdate, TestCaseResponse, TestCaseListFilters, ImportResult
│   ├── tag.py           TagCreate, TagResponse
│   ├── milestone.py     MilestoneCreate, MilestoneUpdate, MilestoneResponse
│   ├── test_run.py      TestRunCreate, TestRunUpdate, TestRunResponse, TestRunProgress, TestCaseWithResult, TestRunWithCases
│   ├── test_result.py   TestResultCreate, TestResultUpdate, TestResultResponse, TestResultHistoryResponse, ResultAttachmentResponse
│   ├── report.py        DashboardResponse, RunReportResponse, MetricsResponse, CustomReportRequest/Response
│   ├── defect.py        JiraDefectCreate, GitHubDefectCreate, GitLabDefectCreate, DefectResponse
│   └── websocket.py     ConnectionTokenResponse, SubscriptionTokensRequest/Response
│
├── services/        — Business logic. Module-level async functions per domain.
│   ├── user_service.py         User CRUD, bulk create, CSV/Excel export, unusable-password fallback + welcome-invite enqueue, set_password (reset flow)
│   ├── project_service.py      Project CRUD, stats (cases/suites/runs/pass_rate)
│   ├── test_suite_service.py   Suite CRUD, parent/project validation, recursive subtree soft-delete cascade (plan-045)
│   ├── test_case_service.py    Test case CRUD, search/filter (delegates tag resolution to tag_service)
│   ├── tag_service.py          Tag list, search, idempotent create, get_or_create_many
│   ├── milestone_service.py    Milestone CRUD
│   ├── test_run_service.py     Run CRUD, close, progress, get_with_cases
│   ├── test_result_service.py  Result upsert, update, history recording, attachment upload/delete
│   ├── import_service.py       CSV/Excel parse → bulk test case creation
│   ├── export_service.py       Test case → CSV/Excel export
│   ├── report_service.py      Dashboard metrics, run reports, project metrics, PDF/Excel generation
│   ├── realtime_service.py    Centrifugo event publishing (fire-and-forget)
│   ├── audit_service.py       Entity change audit logging
│   ├── ci_service.py          CI webhook handling, JUnit XML import, badge generation
│   ├── defect_service.py      Jira/GitHub/GitLab defect creation via external APIs
│   ├── email_outbox_service.py  Outbox enqueue / claim_batch (SKIP LOCKED) / mark_sent / mark_failed (backoff) (plan 048)
│   ├── password_token_service.py  Single-use, expiring Redis tokens (create / peek / consume via GETDEL) (plan 048)
│   └── email_service.py       Mint token + build link + enqueue welcome-invite / password-reset (plan 048)
│
├── core/            — Utilities shared across layers.
│   ├── security.py      JWT encode/decode, bcrypt hash/verify
│   ├── roles.py         UserRole StrEnum (5 values), ROLE_HIERARCHY, ROLE_METADATA
│   ├── exceptions.py    Custom exception classes mapped to HTTP codes
│   ├── centrifugo.py    Centrifugo JWT generation + HTTP publish
│   ├── redis.py         Async redis.asyncio singleton client (first real async Redis consumer; plan 048)
│   ├── email.py         aiosmtplib STARTTLS sender (reusable connection, no-op when disabled) + Jinja2 render (plan 048)
│   └── email_worker.py  Lifespan-managed outbox drain loop (plan 048)
│
├── templates/email/ — Jinja2 email templates: layout.html + welcome_invite.{html,txt} + password_reset.{html,txt} (plan 048)
├── tasks/           — Celery task stubs (report_tasks.py — async generation deferred)
└── utils/           — (empty — pure utility functions planned as needed)
```

### Not yet implemented (future phases)

| File | Phase | Purpose |
|------|-------|---------|
| `app/models/custom_field.py` | Phase 8 | Custom fields + values |
| `app/core/cache.py` | Phase 8 | Redis get/set helpers |

---

## Layer boundaries

```
HTTP Request
     ↓
app/api/v1/<domain>.py   ← input validation (Pydantic), auth check (Depends), HTTP codes
     ↓
app/services/<domain>_service.py   ← business logic, orchestration
     ↓
app/models/<entity>.py   ← SQLAlchemy ORM models
     ↓
PostgreSQL (via asyncpg)
```

**Routers are thin.** They validate input, call one service method, and return a Pydantic schema. No business logic in routers.

**Services own the logic.** Validation rules, computed fields, cross-entity operations — all in services. Services are module-level async functions (not classes), receiving `db: AsyncSession` as the first argument.

**Models are pure data.** SQLAlchemy column definitions and relationships only. No methods with business logic.

---

## "Where is the thing that does X?"

| X | Look here |
|---|-----------|
| JWT creation and verification | `app/core/security.py` |
| Auth dependency (current user) | `app/dependencies.py` → `get_current_user` |
| Role definitions and hierarchy | `app/core/roles.py` → `UserRole`, `ROLE_HIERARCHY`, `ROLE_METADATA` |
| Role/permission checks | `app/dependencies.py` → `require_role(*roles)` |
| DB session management | `app/database.py` → `get_db` (auto commit/rollback) |
| Password hashing | `app/core/security.py` → `get_password_hash`, `verify_password` |
| Pagination envelope | `app/schemas/user.py` → `PaginatedResponse[T]` |
| File upload (attachments) | `app/services/test_result_service.py` → `upload_attachment()` |
| File delete (attachments) | `app/services/test_result_service.py` → `delete_attachment()` |
| Tag list, search, create | `app/services/tag_service.py` |
| CSV/Excel test case import | `app/services/import_service.py` |
| CSV/Excel test case export | `app/services/export_service.py` |
| Result history (audit trail) | `app/models/result_history.py` + `app/services/test_result_service.py` → `_record_history()`, `get_history()` |
| Test run progress (pass/fail counts) | `app/services/test_run_service.py` → `get_progress()` |
| Per-run pass-rate batch (single source of truth) | `app/services/test_run_service.py` → `batch_run_progress()` (consumed by run-list, project stats, and report analytics since plan 041) |
| Test run with cases + results | `app/services/test_run_service.py` → `get_with_cases()` |
| Project statistics | `app/services/project_service.py` → `get_stats()` |
| Project dashboard (full metrics) | `app/services/report_service.py` → `get_dashboard()` |
| Aggregated analytics (dashboard page, per-project) | `app/services/report_service.py` → `get_report_analytics()` |
| Aggregated analytics (cross-project, "All projects" mode) | `app/services/report_service.py` → `get_cross_project_report_analytics()` + `_resolve_project_scope()` (plan 043) |
| Pass-rate rounding / precision constant | `app/utils/stats.py` → `round_ratio()`, `PASS_RATE_DECIMALS` (plan 044) |
| Cascade soft-delete across suite subtree | `app/services/test_suite_service.py` → `delete_suite()` + `_descendant_suite_ids()` (recursive Postgres CTE, plan 045) |
| Stable sibling sort (suites and cases) | `app/services/test_suite_service.py` → `apply_suite_order()` (plan 037); `app/services/test_case_service.py` → `apply_case_order()` (plan 046). Both sort `(display_order NULLS LAST, created_at ASC, id ASC)` |
| Suite re-parent cycle check | `app/services/test_suite_service.py` → `update_suite()` rejects with `BadRequestError` when `parent_suite_id` ∈ `_descendant_suite_ids(self)` (plan 046 / TES-69) |
| Per-run status counts (shared helper) | `app/services/report_service.py` → `_aggregate_run_status_counts()` |
| Run report (JSON/PDF/Excel) | `app/services/report_service.py` → `get_run_report()`, `generate_run_report_pdf()`, `generate_run_report_excel()` |
| Time-series metrics | `app/services/report_service.py` → `get_project_metrics()` |
| Custom filtered report | `app/services/report_service.py` → `run_custom_report()` |
| Result upsert logic | `app/services/test_result_service.py` → `submit()` |
| Centrifugo JWT generation | `app/core/centrifugo.py` → `generate_connection_token()`, `generate_subscription_token()` |
| Centrifugo HTTP publish | `app/core/centrifugo.py` → `publish()` |
| Real-time event publishing | `app/services/realtime_service.py` (fire-and-forget, errors logged and dropped) |
| WebSocket token endpoints | `app/api/v1/websocket.py` |
| Audit logging (entity changes) | `app/services/audit_service.py` → `log_action()` |
| Welcome set-password invite + password reset | `app/services/email_service.py` → `queue_welcome_invite()`, `queue_password_reset()`; endpoints in `app/api/v1/auth.py` |
| Single-use password/invite tokens (Redis) | `app/services/password_token_service.py` → `create_token()`, `peek_token()`, `consume_token()` (GETDEL) |
| Durable email queue (enqueue / claim / retry) | `app/services/email_outbox_service.py`; drained by `app/core/email_worker.py` |
| SMTP send + email template render | `app/core/email.py` → `EmailSender`, `render_email()` |
| Shared async Redis client | `app/core/redis.py` → `get_redis()`, `close_redis()` |
| CI webhooks, JUnit XML import | `app/services/ci_service.py` |
| CI badge SVG | `app/api/v1/ci_integration.py` → `GET /ci/runs/{id}/badge` |
| Defect creation (Jira/GitHub/GitLab) | `app/services/defect_service.py` |
| Per-step result validation | `app/services/test_result_service.py` → `_validate_step_results()` |
| Explicit test case selection for runs | `app/services/test_run_service.py` → `set_run_cases()` |
| All configuration / env vars | `app/config.py` |

---

## Key types (Pydantic schemas)

- `Token` — JWT pair returned on login: `access_token`, `refresh_token`, `token_type`
- `UserResponse` — serialized user (never expose `hashed_password`)
- `ProjectResponse` — project with `name`, `description`, `is_archived`, timestamps
- `ProjectStats` — `total_test_cases`, `total_test_suites`, `total_test_runs`, `pass_rate` (ratio in [0, 1], rounded to 3 decimal places at the response boundary — plan 044)
- `TestSuiteResponse` — suite with `parent_suite_id` (client builds tree)
- `TestCaseResponse` — test case with `steps: list[TestStep]`, `tags: list[str]` (from JSON + M2M), `automation_id: str | None`
- `TagResponse` — `id`, `name`
- `TestRunResponse` — test run with `status`, `config`, `completed_at`
- `TestRunProgress` — `passed`, `failed`, `blocked`, `no_run`, `total`, `pass_rate` (ratio in [0, 1]; counts scoped to the run's current case-set; wire value rounded to 3 decimals via Pydantic `field_serializer` while the in-memory value stays raw for aggregation — plan 044)
- `TestRunWithCases` — test run + all test cases with their current results (outer join)
- `TestRunCasesUpdate` — `test_case_ids: list[int]` — replaces explicit case set on a run
- `TestResultResponse` — result with `status`, `comment`, `message`, `stack_trace`, `defects[]`, `step_results: list[StepResult] | None`, timestamps
- `StepResult` — per-step outcome: `index`, `status`, `comment?`
- `TestResultHistoryResponse` — one history row: `status`, `comment`, `changed_by`, `changed_at`
- `ResultAttachmentResponse` — attachment metadata: `filename`, `file_size`, `mime_type` (no `file_path`)
- `MilestoneResponse` — milestone with `target_date`, `is_completed`
- `DashboardResponse` — project-level dashboard: counts, pass rate, active runs, recent runs, result distribution
- `RunReportResponse` — full run report: `passed`/`failed`/`blocked`/`no_run`/`total` counts + all cases with results
- `MetricsResponse` — time-series pass rate by day with `MetricsDataPoint[]`
- `ProjectReportAnalyticsResponse` — single round-trip payload for the per-project Reports page: summary, runs, distributions, trend (plan 027)
- `CrossProjectReportAnalyticsResponse` — same shape as above but aggregated across `project_ids` (or all visible projects when omitted), plus `per_project: PerProjectAnalyticsRow[]` for the breakdown table (plan 043)
- `RunAnalyticsItem` — per-run row carrying `project_id` (always) and `project_name?` (populated only by the cross-project endpoint)
- `PerProjectAnalyticsRow` — one row per project in scope: `project_id`, `project_name`, `is_archived`, `total_test_runs`, `completed_runs`, `overall_pass_rate`, `total_results`
- `CustomReportResponse` — paginated `CustomReportRow[]` from filtered result query
- `DefectResponse` — defect reference: `tracker`, `key`, `url`, `summary`
- `PaginatedResponse[T]` — standard list envelope: `items`, `total`, `page`, `page_size`, `pages`

---

## Architectural invariants

1. **No business logic in routers** — routers call one service method and return a schema
2. **No raw SQL in services** — all DB access through SQLAlchemy ORM
3. **All DB calls are async** — no synchronous `session.execute()` anywhere
4. **Config from environment only** — all values come from `app/config.py` via `.env`
5. **Never return ORM models from routers** — always serialize via Pydantic schemas
6. **Never edit existing Alembic migrations** — create new revisions only
7. **All protected routes use `Depends(get_current_user)` or `Depends(require_role(...))`** — no manual token parsing
8. **Soft delete by default for domain entities** — services set `deleted_at` instead of `db.delete()`. All `list_*`/`get_*` queries filter `not_deleted(Model)` unless an explicit `include_deleted=True` / `allow_deleted=True` opt-in is passed. Cascade soft-delete is done explicitly in the service layer.

---

## Alembic migration history

| Revision | Description |
|----------|-------------|
| `8c23843e1a84` | Initial schema: users table |
| `f7a3b2c1d9e0` | Rename role slugs (viewer→read_only, project_manager→lead), add CHECK constraint |
| `620e48c40917` | Add projects, test_suites, test_cases, tags, test_case_tags |
| `7e3155df2bc6` | Add milestones, test_runs, test_results, result_attachments, result_history |
| `b5c6d7e8f9a0` | Add audit_logs |
| `d04cd83a87fd` | Add automation_id column + index to test_cases |
| `11cd61046802` | Add test_run_test_cases association table |
| `b368c6900009` | Add step_results JSON column to test_results |
| `a1c2e3f40576` | Add email_outbox table (durable email queue; plan 048) |

---

## Production deployment

Docker stack via `docker-compose.prod.yml`, all on a single private `internal`
network and published on `127.0.0.1` only:
- `api` — Uvicorn + FastAPI (`127.0.0.1:8000`)
- `postgres` — PostgreSQL 16
- `redis` — Redis 7
- `centrifugo` — Centrifugo v5 (real-time WebSocket events)
- `minio` — S3-compatible object storage for attachments (`127.0.0.1:9000`)

The `api` container also runs the **email drain worker** in its FastAPI lifespan
(no separate process) and makes **outbound SMTP** connections to Gmail (STARTTLS,
port 587) when `EMAIL_ENABLED=true`. Redis additionally backs the single-use
password/invite tokens. See `docs/03-engineering/operations/email.md`.

**The public edge is host-level nginx + system certbot — not a container.** Host
nginx is the only public listener on `:80`/`:443` and reverse-proxies to the
loopback container ports. Each app owns its own vhost; this repo ships
`deploy/api.vhost.conf` (api + s3) and `deploy/nginx-maps.conf`. Per-app TLS
certs are renewed by the system `certbot.timer`. See `deploy/README.md` for the
host runbook. (The old shared `testoria-proxy` docker network and the dockerized
nginx-proxy/certbot in the frontend repo are retired — see plan 047.)
