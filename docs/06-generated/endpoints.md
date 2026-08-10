# ENDPOINTS — Backend API Reference
# Derived from: app/api/v1/*.py
# Base URL: /api/v1
# Update this file when endpoints are added or changed.

---

## Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Health check → `{"status": "healthy"}` |

---

## Auth (`app/api/v1/auth.py`)

| Method | Path | Auth | Input | Output |
|--------|------|------|-------|--------|
| POST | `/auth/login` | None | `form: username, password` | `Token` |
| POST | `/auth/refresh` | None | `body: refresh_token` | `Token` |
| GET | `/auth/me` | Bearer or API key | — | `UserResponse` |
| GET | `/auth/principal` | Bearer or API key | — | `PrincipalResponse` |
| POST | `/auth/logout` | Bearer | — | `{"message": "..."}` |
| POST | `/auth/forgot-password` | None | `body: email` | `{"message": "..."}` 202 |
| POST | `/auth/reset-password` | None | `body: token, new_password` | `{"message": "..."}` |
| GET | `/auth/reset-password/validate` | None | `query: token` | `{valid, username}` |

**Token** = `{ access_token, refresh_token, token_type: "bearer" }`

**PrincipalResponse** = `{ user_id, username, account_role, effective_role, project_id, via }`.
`/auth/me` answers "which account is this" and returns the account's own role;
`/auth/principal` answers "what may this credential do" — for an API key the
`effective_role` is capped below `account_role`. See
`docs/02-architecture/backend/auth.md`.

---

## API Keys (`app/api/v1/api_keys.py`)

| Method | Path | Auth | Input | Output |
|--------|------|------|-------|--------|
| POST | `/api-keys` | **Bearer only** (tester+) | `ApiKeyCreate` | `ApiKeyCreateResponse` 201 |
| GET | `/api-keys` | **Bearer only** (tester+) | `query: user_id?, include_revoked?` | `list[ApiKeyResponse]` |
| DELETE | `/api-keys/{key_id}` | **Bearer only** (owner or admin) | — | 204 |

**Bearer only**: these three routes reject API-key principals with 403 (`require_jwt`).
An API key that could mint or revoke keys would turn a leak from a revocable
credential into a persistent foothold.

**ApiKeyCreate** = `{ name, project_id?, role=tester, expires_in_days?, never_expires=false, user_id? }`.
`user_id` (mint for someone else) requires lead/admin. Role is capped at
`API_KEY_MAX_ROLE` (default `tester`) **and** at the owner's own role.

**ApiKeyCreateResponse** = `ApiKeyResponse` + `key` — the plaintext, returned
once and never retrievable again. `ApiKeyResponse` carries `key_prefix` only.

Requests authenticate with `X-API-Key: tsk_<prefix>_<secret>`. Sending both an
`Authorization` header and `X-API-Key` is a 400 — the server never guesses.

There is **no public self-registration** (the former `POST /auth/register` was removed in plan 049). Accounts are created only by a Lead or Admin via `POST /users` / `POST /users/bulk`, and are always invite-only (see Users below).

**Password-reset / set-password flow** (public by design, no user enumeration):
- `POST /auth/forgot-password` always returns `202` with the same message whether or not the address exists. If an active user matches, a single-use reset token is minted (Redis, 1h TTL) and a reset email is enqueued via the outbox.
- `POST /auth/reset-password` consumes the token (single use via `GETDEL`) and sets the new password. Serves both the welcome set-password invite and forgot-password. Returns `400` for an invalid/expired/used token, `422` for a password shorter than 8 chars (validated before the token is consumed).
- `GET /auth/reset-password/validate?token=...` peeks the token without consuming it → `200 {valid: true, username}` or `400`.

---

## Users (`app/api/v1/users.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/users` | lead | `search?, status?, role?, page?, page_size?` | `PaginatedResponse[UserResponse]` |
| POST | `/users` | lead | `UserCreate` | `UserResponse` 201 |
| POST | `/users/bulk` | lead | `UserBulkCreate` | `UserBulkCreateResult` |
| GET | `/users/export` | lead | `format: csv\|excel` | file download |
| GET | `/users/{id}` | lead | — | `UserResponse` |
| PUT | `/users/{id}` | lead | `UserUpdate` | `UserResponse` |
| DELETE | `/users/{id}` | lead | — | 204 |

**Who may manage users (plan 049):** all `/users*` endpoints require **Lead or Admin** (`require_role(LEAD, ADMIN)`); tester / read_only / no_access get 403. A **Lead is capped at Lead**: creating a user with `role=admin`, changing any user's role to `admin`, or updating/deleting a user who is currently an Admin all return **403** — only an Admin can manage Admins. (Enforced in the service via `_assert_can_manage_role` / `_assert_can_manage_user`; the router passes the authenticated actor.)

`DELETE /users/{id}` returns 409 if `user.role == "lead"`. Delete is a soft delete — sets `deleted_at`; list/get endpoints then exclude the row. `UserUpdate` accepts an optional `password` field; when present, the actor updates the user's password (hashed via bcrypt). Omitting the field leaves the password unchanged.

`POST /users` and `POST /users/bulk` are **invite-only**: `UserCreate` carries **no `password` field** (single or bulk). Every account is created with an unusable random hash and a welcome **set-password invite** email (Redis token + outbox row) is enqueued in the same transaction as the user INSERT — so a committed user always has its invite recorded, and `POST /users/bulk` of ~100 users writes ~100 outbox rows in one transaction (drained later over a single SMTP connection).

Conflicts name the colliding field: `POST /users` → 409 with `Email '<x>' is already taken` / `Username '<x>' is already taken`. `POST /users/bulk` is best-effort and returns per-row `BulkCreateError { index, username, email, detail }` — `detail` is the same specific message, and `username`/`email` echo the failing row so the client can show which one (`UserBulkCreateResult { created: int, errors: [...] }`).

## Roles (`app/api/v1/users.py`)

| Method | Path | Auth | Output |
|--------|------|------|--------|
| GET | `/roles` | Bearer | `RoleResponse[]` |

Returns the 5 predefined roles with metadata (slug, label, is_default, is_deletable, description).

---

## Projects (`app/api/v1/projects.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/projects` | read_only | `page?, page_size?, include_archived?, include_deleted?` | `PaginatedResponse[ProjectResponse]` |
| POST | `/projects` | lead | `ProjectCreate` | `ProjectResponse` 201 |
| GET | `/projects/{id}` | read_only | — | `ProjectResponse` |
| PUT | `/projects/{id}` | lead | `ProjectUpdate` | `ProjectResponse` |
| DELETE | `/projects/{id}` | admin | — | 204 (soft) |
| POST | `/projects/{id}/restore` | admin | — | `ProjectResponse` |
| GET | `/projects/{id}/stats` | read_only | — | `ProjectStats` |
| GET | `/projects/stats` | read_only | `include_archived?, project_ids?` | `ProjectStatsBulkResponse` |

`DELETE /projects/{id}` performs a soft delete: sets `deleted_at` on the project and cascade-soft-deletes suites, test cases, runs, and results that belong to it. `POST /restore` clears `deleted_at` on the project only; children must be restored explicitly. `ProjectResponse` includes a `deleted_at: datetime | null` field.

**ProjectStats** = `{ total_test_cases, total_test_suites, total_test_runs, pass_rate }`
`total_test_runs` counts every run regardless of status. `pass_rate` is a ratio in `[0, 1]` (see plan 035), **rounded to 3 decimal places** at the response boundary (= 1 decimal of percent, plan 044), computed as the **arithmetic mean of each completed run's own pass rate** (plan 041) — runs with zero results don't contribute, and in-flight runs are excluded entirely (plan 039). `null` when there are no completed runs with results.

**ProjectStatsItem** = `{ project_id, name, is_archived, total_test_cases, total_test_suites, total_test_runs, active_runs, pass_rate }` — one row per project. `pass_rate` is the **arithmetic mean of each completed run's own pass rate** across the project's completed runs (plan 041); runs with zero results don't contribute. `pass_rate` is `null` when the project has no completed runs with results; `active_runs` counts runs in status `planned` or `active` (work in flight).
**ProjectStatsBulkResponse** = `{ items: ProjectStatsItem[], total }` — single-round-trip counts for the home dashboard. Built with four grouped SQL queries regardless of how many projects exist. `include_archived=false` (default) hides archived projects entirely. `project_ids` (repeated query param) restricts the response to a caller-supplied subset.

---

## Test Suites (`app/api/v1/test_suites.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/projects/{project_id}/test-suites` | read_only | `include_deleted?` | `TestSuiteResponse[]` |
| POST | `/projects/{project_id}/test-suites` | lead | `TestSuiteCreate` | `TestSuiteResponse` 201 |
| GET | `/test-suites/{id}` | read_only | — | `TestSuiteResponse` |
| PUT | `/test-suites/{id}` | lead | `TestSuiteUpdate` | `TestSuiteResponse` |
| DELETE | `/test-suites/{id}` | lead | — | 204 (soft, cascades to all descendant suites + their cases — plan-045 / TES-70) |
| POST | `/test-suites/{id}/restore` | lead | — | `TestSuiteResponse` (400 if parent project is deleted) |

Returns flat list with `parent_suite_id` — client builds the tree. Sort order is stable: `(display_order NULLS LAST, created_at ASC, id ASC)` (plan 037). `display_order` is an optional int on create/update/response; `null` = unordered (legacy / not yet positioned).

`PUT /test-suites/{id}` rejects with `400 BadRequest` when `parent_suite_id` is set to one of the suite's own descendants (plan-046 / TES-69). The cycle check reuses the recursive CTE shipped for cascade soft-delete (plan-045), so re-parenting and cascade share a single canonical "is X under Y?" answer.

---

## Test Cases (`app/api/v1/test_cases.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/projects/{project_id}/test-cases` | read_only | `TestCaseFilters, include_deleted?` | `PaginatedResponse[TestCaseResponse]` |
| POST | `/projects/{project_id}/test-cases` | lead | `TestCaseCreate` | `TestCaseResponse` 201 |
| GET | `/test-cases/{id}` | read_only | — | `TestCaseResponse` |
| PUT | `/test-cases/{id}` | lead | `TestCaseUpdate` | `TestCaseResponse` |
| DELETE | `/test-cases/{id}` | lead | — | 204 (soft) |
| POST | `/test-cases/{id}/restore` | lead | — | `TestCaseResponse` (400 if parent suite is deleted) |
| POST | `/projects/{project_id}/test-cases/import` | lead | `multipart: file (CSV/Excel)` | `ImportResult` |
| GET | `/projects/{project_id}/test-cases/export` | read_only | `format: csv\|excel` | file download |

**TestCaseFilters**: `suite_id?`, `priority?`, `type?`, `status?`, `search?`, `tag_ids?` (repeated), `automation_id?` (exact match), `has_automation_id?` (bool), `page?`, `page_size?`
`tag_ids` accepts repeated query params (e.g. `?tag_ids=1&tag_ids=2`). OR semantics — returns test cases that have *any* of the given tags.
**priority**: `low` \| `medium` \| `high` \| `critical`

Sort order on the list endpoint is `(display_order NULLS LAST, created_at ASC, id ASC)` — `apply_case_order` helper (plan-046 / TES-69). `display_order` is an optional `int | None` on Create / Update / Response; `null` sorts last so legacy (pre-migration) cases preserve their existing relative order until they're first reordered.
**type**: `manual` \| `automated`
**status**: `draft` \| `active` \| `deprecated`
**ImportResult** = `{ created: int, errors: [{ row: int, detail: str }] }`

`automation_id` is an optional string field on create/update/response. Empty strings are coerced to `null`. Filter by exact match via `?automation_id=...`, or by presence via `?has_automation_id=false` — which lists the cases no automated run can link to (what `testoria case list --unmapped` calls). Omitting the param is unchanged.

CSV/Excel import columns: `title, description, preconditions, steps_json, priority, type, status, suite_id, tags`

`TestCaseCreate.tags` / `TestCaseUpdate.tags` accept a `list[str]` of names — tags are created if they don't exist.
`TestCaseResponse.tags` returns a `list[{id, name}]` so the frontend can identify tags by ID. The response also includes `deleted_at: datetime | null`.

---

## Tags (`app/api/v1/tags.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/tags` | read_only | `q?`, `limit?` (1–200, default 50) | `TagResponse[]` |
| POST | `/tags` | tester | `TagCreate` | `TagResponse` (201 if new, 200 if existing) |

**TagCreate** = `{ name: str }` — normalized to lowercase, trimmed
**TagResponse** = `{ id: int, name: str }`
`GET /tags?q=foo` — case-insensitive prefix search (ILIKE 'foo%')
`POST /tags` is **idempotent**: if a tag with the same normalized name exists, returns it with 200 (not 409 or 500).

---

## Milestones (`app/api/v1/milestones.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/projects/{project_id}/milestones` | read_only | `include_deleted?` | `MilestoneResponse[]` |
| POST | `/projects/{project_id}/milestones` | lead | `MilestoneCreate` | `MilestoneResponse` 201 |
| PUT | `/milestones/{id}` | lead | `MilestoneUpdate` | `MilestoneResponse` |
| DELETE | `/milestones/{id}` | lead | — | 204 (soft) |
| POST | `/milestones/{id}/restore` | lead | — | `MilestoneResponse` (400 if parent project is deleted) |

---

## Test Runs (`app/api/v1/test_runs.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/projects/{project_id}/test-runs` | read_only | `status?, page?, page_size?, include_deleted?` | `PaginatedResponse[TestRunResponse]` |
| POST | `/projects/{project_id}/test-runs` | tester | `TestRunCreate` | `TestRunResponse` 201 |
| GET | `/test-runs/{id}` | read_only | — | `TestRunResponse` |
| PUT | `/test-runs/{id}` | tester | `TestRunUpdate` | `TestRunResponse` |
| DELETE | `/test-runs/{id}` | lead | — | 204 (soft, cascades to results) |
| POST | `/test-runs/{id}/restore` | lead | — | `TestRunResponse` (400 if parent project is deleted) |
| POST | `/test-runs/{id}/close` | tester | — | `TestRunResponse` (status=completed) |
| GET | `/test-runs/{id}/progress` | read_only | — | `TestRunProgress` |
| PUT | `/test-runs/{id}/cases` | tester | `TestRunCasesUpdate` | 204 |
| GET | `/test-runs/{id}/cases` | read_only | — | `TestRunWithCases` |

**TestRunCreate**: `name, suite_id?, milestone_id?, assigned_to?, config?, include_test_cases?`
`include_test_cases` accepts an optional list of test case ids. When `null` (omitted), the run uses `cases_mode="auto"` — cases are derived from `suite_id` / project. When provided (even `[]`), the run uses `cases_mode="explicit"` — only the supplied case ids (possibly none) are in scope. `cases_mode` is returned on every `TestRunResponse`; `PUT /test-runs/{id}/cases` always flips the run to `explicit`.
**TestRunCasesUpdate**: `{ test_case_ids: list[int] }` — replaces the entire case set (PUT semantics). All ids must belong to the run's project or the request is rejected with 400. Returns 409 if the run's status is `completed` (case set is locked once the run is closed).
**run status**: `planned` \| `active` \| `completed` \| `aborted` (plan 039). Lifecycle: new runs start as `planned`; the first meaningful `POST /test-runs/{id}/results` or `PUT /test-results/{id}` flips the run to `active` in the same transaction; `POST /test-runs/{id}/close` is the only path to `completed`. `in_progress` is accepted on `TestRunUpdate.status` input for one release as a compat alias and is normalised to `active` before persist.
**TestRunProgress** = `{ passed, failed, blocked, no_run, total, pass_rate }` — `no_run` includes both explicit `no_run` results and cases with no result row yet (the old `untested` field was folded in). `pass_rate` is a `float \| null` ratio in `[0, 1]` computed as `passed / max(cases_in_scope, tested)` (plan 035; untested cases count against the rate). The wire value is **rounded to 3 decimal places** at serialisation (plan 044) — the in-memory value is raw so report aggregations don't drift. Consumed as the single per-run definition by `project_service.get_stats` / `get_bulk_stats` and `report_service.get_report_analytics` since plan 041 — Dashboard, per-project breakdown, and Reports KPI all derive from this same value.
**Counts are scoped to the run's current case-set** — the same rules `/cases` uses (junction rows for explicit mode, project/suite-derived for auto). Orphan results (case removed from an explicit selection, soft-deleted, or moved out of the auto suite after a result was submitted) do **not** inflate the status counts. Consequently `passed + failed + blocked + no_run == total` holds.
`TestRunResponse.progress` is always populated on the list endpoint in one batched query (`passed + failed + blocked + no_run == total` invariant holds per item). Single-item reads (`GET /test-runs/{id}`) omit it — use `GET /test-runs/{id}/progress` instead.
**TestRunWithCases** = `{ run: TestRunResponse, cases: TestCaseWithResult[], total: int }`
**TestCaseWithResult** = `{ ...TestCase, status, case_status, automation_id, tags, result: TestResultResponse \| null }` — `status` is the case's own workflow status (deprecated alias); prefer `case_status` (disambiguates from `result.status`). `status` will be removed one release after plan 033.
`GET /test-runs/{id}/cases` accepts `limit` (default 500, max 2000), `offset` (default 0), and `sort` (`suite_id,id` (default) \| `id` \| `title` \| `priority` \| `suite`). Returns `total` alongside `cases` so the UI can show "N of M".
With `?group_by=suite` the same endpoint returns `TestRunSuiteTree` = `{ run, roots: TestRunSuiteNode[], total }` where each node is `{ suite: {id, name, parent_id}, progress: {total, passed, failed, blocked, no_run, other}, cases: TestCaseWithResult[], children: TestRunSuiteNode[] }`. Suites with zero run-cases are omitted; `progress` counts are per-node (no recursive rollup). The grouped projection is not paginated.

---

## Test Results (`app/api/v1/test_results.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/test-runs/{run_id}/results` | read_only | `include_orphans?: bool` | `TestResultResponse[]` |
| POST | `/test-runs/{run_id}/results` | tester | `TestResultCreate` | `TestResultResponse` 201 (upsert) |
| POST | `/test-runs/{run_id}/results/import` | tester | `multipart: file` + `form: format=auto\|junit\|json` | `ResultImportReport` |
| GET | `/test-results/{id}` | read_only | — | `TestResultResponse` |
| PUT | `/test-results/{id}` | tester | `TestResultUpdate` | `TestResultResponse` |
| GET | `/test-results/{id}/history` | read_only | — | `TestResultHistoryResponse[]` |
| POST | `/test-results/{id}/attachments` | tester | `multipart: file` | `ResultAttachmentResponse` 201 |
| POST | `/test-results/{id}/attachments/bulk` | tester | `multipart: files[]` (image/* whitelist, 10 max, 10MB each) | `ResultAttachmentBulkResponse` 201 |
| DELETE | `/test-results/{id}/attachments/{attach_id}` | tester | — | 204 |
| GET | `/files/legacy/{attachment_id}` | read_only | — | Streams the file for `storage_backend='local'` rows; 410 Gone otherwise. Removed after every row migrates to `'s3'`. |

**Import** (`/test-runs/{run_id}/results/import`): accepts JUnit XML or a JSON list.
Accepts an API key as well as a Bearer token; a project-scoped key may only write
to runs in its project (403 otherwise). Always returns **200 with a report** — an
unmatched test is information, not an error. `--strict` is a client policy.

**ResultImportReport** = `{ run_id, total, matched, submitted, unmatched, unmatched_cases[], matched_by{}, status_counts{} }`.
`unmatched_cases` is capped at 100 entries; `unmatched` keeps the true count.
Each entry is `{ identifier, classname, name, status, reason }` where `reason` is
`no_match` \| `ambiguous` \| `out_of_scope`.

**Match order** — `matched_by` reports which rule hit, so a suite matching on the
fragile `title` rules instead of `automation_id` is visible:

| # | Rule key | Matches `classname.name` against |
|---|----------|----------------------------------|
| 1 | `automation_id` | `TestCase.automation_id` |
| 2 | `automation_id_dotted` | `dotted(automation_id)` — **pytest node IDs** |
| 3 | `automation_id_name` | `automation_id` == the bare test name |
| 4 | `title` | `TestCase.title` (legacy `/ci/results/bulk` behaviour) |
| 5 | `title_dotted` | `dotted(title)` |

`dotted()` rewrites `a/b.py::C::test_x` → `a.b.C.test_x`. Only that direction is
well-defined, so normalisation is applied to the **stored** identifier, never to
the incoming XML. It exists because pytest's JUnit output carries no node ID —
`junit_family=xunit2` emits neither `file` nor `line`. A duplicate key is reported
as `ambiguous`, never resolved first-wins. Import is idempotent: results upsert on
`(run, case)` and no-op resubmits write no history.

**TestResultCreate**: `test_case_id, status?, comment?, message?, stack_trace?, execution_time?, defects?, step_results?`
**TestResultUpdate**: `status?, comment?, message?, stack_trace?, execution_time?, defects?, step_results?`
**result status**: `passed` \| `failed` \| `blocked` \| `no_run` (default when omitted). `skipped` is still accepted on input for one release (compat shim) — the service normalises it to `no_run` before persist.
**StepResult** = `{ index: int, status: 'passed'|'failed'|'blocked'|'no_run', comment?: str (max 1000) }` (`skipped` accepted, normalised to `no_run`)
`step_results` is an optional list of per-step outcomes. Each `index` must be in range `[0, len(test_case.steps))`. Duplicate indices are rejected (400). Partial coverage is allowed (not every step needs a result). `null` = tester didn't use per-step mode; `[]` = per-step mode opened but nothing marked.
**`GET /test-runs/{id}/results` is scoped to the run's current case-set** — results for cases that have been removed from an explicit selection, soft-deleted, or moved out of an auto-scoped suite are hidden. Pass `?include_orphans=true` to return them all (audit use case).
**Upsert**: second POST with same `test_case_id` updates the existing result (UNIQUE run+case)
**History**: a row is appended on initial submit, on any `status` change, and on any `comment` change (plan 038). A no-op resubmit (same status + same comment) adds no row. `step_results` changes are not yet tracked — tech debt.

---

## Reporting & Analytics (`app/api/v1/reports.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| GET | `/projects/{id}/dashboard` | read_only | — | `DashboardResponse` |
| GET | `/projects/{id}/report-analytics` | read_only | `date_from?, date_to?, run_status?, include_trend?: bool (default true)` | `ProjectReportAnalyticsResponse` |
| GET | `/reports/analytics` | read_only | `project_ids?` (repeated), `date_from?, date_to?, run_status?, include_trend?: bool (default true), include_archived?: bool (default false)` | `CrossProjectReportAnalyticsResponse` |
| GET | `/test-runs/{id}/report` | read_only | `format?: json\|pdf\|excel` | `RunReportResponse` / file download |
| GET | `/projects/{id}/metrics` | read_only | `days?: 1-365 (default 30)` | `MetricsResponse` |
| POST | `/reports/custom` | read_only | `CustomReportRequest` | `CustomReportResponse` |

**DashboardResponse** = `{ total_test_cases, total_test_runs, total_test_suites, pass_rate, active_runs, recent_runs: RunSummary[], result_distribution: {status: count} }`. `pass_rate` and `result_distribution` count only results from **completed** runs (plan 039); `active_runs` still counts `planned + active` as work-in-flight. `recent_runs` is the latest 5 regardless of status.
**ProjectReportAnalyticsResponse** = `{ project_id, date_from, date_to, summary: ReportAnalyticsSummary, runs: RunAnalyticsItem[], test_case_distribution: TestCaseDistribution, trend: TrendPoint[] }` — single round-trip payload for the Reports & Analytics page.
**RunAnalyticsItem** carries `project_id: int` (always) and `project_name: str | null` (populated only by the cross-project endpoint — the per-project endpoint already knows the project from the URL and skips the join).
**CrossProjectReportAnalyticsResponse** = `{ project_ids: int[] | null, date_from, date_to, summary: ReportAnalyticsSummary, runs: RunAnalyticsItem[], test_case_distribution: TestCaseDistribution, trend: TrendPoint[], per_project: PerProjectAnalyticsRow[] }` — `GET /reports/analytics` aggregated payload for the Reports page in "All projects" mode (plan 043). Same aggregation rules as the per-project endpoint (plans 035/039/041): `summary.overall_pass_rate` is the arithmetic mean of every completed run's own pass rate across all in-scope projects; `summary.result_distribution` / `total_results` count completed runs only; `runs` and `trend` respect the date window. `project_ids` echoes the explicit subset the caller passed, or `null` when omitted (full visible set). Unknown ids in `project_ids` are silently dropped. `include_archived=false` hides archived projects entirely (matches `/projects/stats`).
**PerProjectAnalyticsRow** = `{ project_id, project_name, is_archived, total_test_runs, completed_runs, overall_pass_rate: float | null, total_results }` — one row per in-scope project. `overall_pass_rate` follows the same per-project mean-of-run-rates rule (plan 041) so each row agrees with `/projects/stats` for that project. `summary.result_distribution`, `summary.total_results`, `summary.overall_pass_rate`, and `trend` are computed from completed runs only (plan 039); `summary.active_runs` counts `planned + active`. `summary.overall_pass_rate` is the arithmetic mean of each completed run's own `pass_rate` (plan 041) — per-run rate comes from `TestRunProgress` so Dashboard and Reports agree. `test_case_distribution.by_automation.automated` counts cases whose `type == 'automated'` (not cases with an `automation_id` linkage). The `runs` list itself still surfaces every run's counts regardless of its status. `runs` and `trend` are filtered by the date window (matches on `completed_at` with fallback to `created_at`). `trend` is zero-filled day-by-day when both date bounds are provided.
**RunReportResponse** = `{ run_id, run_name, run_status, project_id, created_at, completed_at, passed, failed, blocked, no_run, total, pass_rate, cases: RunReportCaseResult[] }` — `no_run` includes cases with no result row yet (the separate `untested` field was removed).
**MetricsResponse** = `{ project_id, days, data: MetricsDataPoint[] }` — time-series pass rate by day
**CustomReportRequest** = `{ project_id, suite_id?, run_id?, status?: string[], date_from?, date_to?, page?, page_size? }`
**CustomReportResponse** = paginated `CustomReportRow[]`

`format=pdf` returns `Content-Type: application/pdf`; `format=excel` returns `.xlsx`.

---

## CI/CD Integration (`app/api/v1/ci_integration.py`)

| Method | Path | Auth | Input | Output |
|--------|------|------|-------|--------|
| POST | `/ci/webhooks` | Bearer (tester) | `dict[str, Any]` JSON body | `{ status: "accepted" }` |
| POST | `/ci/results/bulk` | Bearer (tester) | `multipart: file` + query `test_run_id` | `{ submitted: int, skipped: int }` |
| GET | `/ci/runs/{id}/badge` | None (public) | — | SVG (`image/svg+xml`) |

**Bulk import**: Accepts JUnit XML. Matches `classname.name` against `TestCase.title`. Unmatched test cases are counted in `skipped` (response field name — unrelated to result status). JUnit `<skipped>` elements are imported as result status `no_run`.
**Badge**: Pass rate ≥ 90% → green, ≥ 70% → yellow, < 70% → red. Returns `Cache-Control: no-cache`.

> `POST /ci/results/bulk` is the **legacy** import path (title matching, counts-only
> response). New integrations use `POST /test-runs/{run_id}/results/import` — see
> Test Results. The old route is unchanged and still supported.

---

## Defect Tracking (`app/api/v1/defects.py`)

| Method | Path | Min Role | Input | Output |
|--------|------|----------|-------|--------|
| POST | `/defects/jira` | tester | `JiraDefectCreate` | `DefectResponse` 201 |
| POST | `/defects/github` | tester | `GitHubDefectCreate` | `DefectResponse` 201 |
| POST | `/defects/gitlab` | tester | `GitLabDefectCreate` | `DefectResponse` 201 |

**JiraDefectCreate**: `test_result_id, jira_url, jira_username, jira_api_token, project_key, summary, description`
**GitHubDefectCreate**: `test_result_id, repo_owner, repo_name, token, title, body`
**GitLabDefectCreate**: `test_result_id, gitlab_url, project_id, token, title, description`
**DefectResponse**: `{ tracker, key, url, summary }`

After successful creation the defect reference is appended to `test_results.defects` (JSONB list).
Credentials are supplied per-request in the body — no server-side token storage.
Invalid credentials from tracker → 422; network error → 502.

---

## WebSocket Tokens (`app/api/v1/websocket.py`) — Phase 4

| Method | Path | Auth | Input | Output |
|--------|------|------|-------|--------|
| GET | `/websocket/connection-token` | Bearer | — | `{ token: string }` |
| POST | `/websocket/subscription-tokens` | Bearer | `{ channels: string[] }` | `{ tokens: { [channel]: string } }` |

---

## Response envelopes

```python
PaginatedResponse[T] = {
    items: T[],
    total: int,
    page: int,
    page_size: int,
    total_pages: int
}

ErrorResponse = { detail: str }
ValidationErrorResponse = { detail: list[ValidationError] }  # FastAPI 422
```
