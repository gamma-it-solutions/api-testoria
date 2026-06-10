# DB SCHEMA — Authoritative Backend Data Model
# Derived from: app/models/*.py + alembic/versions/
# Update this file when models or migrations change.

---

## Tables

### `users`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK | |
| username | VARCHAR(100) | UNIQUE, NOT NULL, INDEX | Login name |
| email | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | |
| hashed_password | VARCHAR(255) | NOT NULL | bcrypt hash — never returned to clients |
| full_name | VARCHAR(255) | NULL | |
| role | VARCHAR(50) | NOT NULL, DEFAULT `'lead'`, CHECK IN (`no_access`,`read_only`,`tester`,`lead`,`admin`) | `no_access` \| `read_only` \| `tester` \| `lead` \| `admin` |
| is_active | BOOLEAN | DEFAULT TRUE | False = deactivated, rejected on auth |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |
| updated_at | TIMESTAMPTZ | ON UPDATE NOW() | |
| deleted_at | TIMESTAMPTZ | NULL, INDEX | Soft-delete timestamp; rows with `deleted_at IS NOT NULL` are excluded from queries by default |

---

### `projects`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| name | VARCHAR(255) | NOT NULL, INDEX | |
| description | TEXT | NULL | |
| is_archived | BOOLEAN | NOT NULL, DEFAULT FALSE | |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| deleted_at | TIMESTAMPTZ | NULL, INDEX | Soft-delete timestamp |

---

### `test_suites`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| project_id | INTEGER | FK → projects.id ON DELETE RESTRICT, NOT NULL, INDEX | RESTRICT to prevent hard cascade — service owns cascade soft-delete |
| parent_suite_id | INTEGER | FK → test_suites.id ON DELETE SET NULL, NULL, INDEX | NULL = root suite |
| name | VARCHAR(255) | NOT NULL | |
| description | TEXT | NULL | |
| display_order | INTEGER | NULL | Explicit user-driven ordering. Sort = `(display_order NULLS LAST, created_at, id)`. Added by migration `e9f0a1b2c3d5` (plan 037). |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| deleted_at | TIMESTAMPTZ | NULL, INDEX | Soft-delete timestamp |

---

### `test_cases`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| suite_id | INTEGER | FK → test_suites.id ON DELETE RESTRICT, NOT NULL, INDEX | RESTRICT; service owns cascade soft-delete |
| title | VARCHAR(500) | NOT NULL, INDEX | |
| description | TEXT | NULL | |
| preconditions | TEXT | NULL | |
| steps | JSON | NOT NULL, DEFAULT `[]` | `[{"step": "...", "expected": "..."}]` — stored as JSONB on PostgreSQL |
| priority | VARCHAR(50) | NOT NULL, DEFAULT `'medium'` | `low` \| `medium` \| `high` \| `critical` |
| type | VARCHAR(50) | NOT NULL, DEFAULT `'manual'` | `manual` \| `automated` |
| status | VARCHAR(50) | NOT NULL, DEFAULT `'draft'` | `draft` \| `active` \| `deprecated` |
| automation_id | VARCHAR(255) | NULL, INDEX | External test framework identifier (e.g. pytest node id, Playwright spec) |
| display_order | INTEGER | NULL | Explicit user-driven ordering inside a suite. Sort = `(display_order NULLS LAST, created_at, id)`. Added by migration `f0a1b2c3d4e5` (plan 046, TES-69). |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| deleted_at | TIMESTAMPTZ | NULL, INDEX | Soft-delete timestamp |

---

### `milestones`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| project_id | INTEGER | FK → projects.id ON DELETE CASCADE, NOT NULL, INDEX | |
| name | VARCHAR(255) | NOT NULL | |
| description | TEXT | NULL | |
| target_date | DATE | NULL | |
| is_completed | BOOLEAN | NOT NULL, DEFAULT FALSE | |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| deleted_at | TIMESTAMPTZ | NULL, INDEX | Soft-delete timestamp |

---

### `test_runs`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| project_id | INTEGER | FK → projects.id ON DELETE RESTRICT, NOT NULL, INDEX | RESTRICT; service owns cascade soft-delete |
| suite_id | INTEGER | FK → test_suites.id ON DELETE SET NULL, NULL, INDEX | If set, run is scoped to that suite |
| milestone_id | INTEGER | FK → milestones.id ON DELETE SET NULL, NULL, INDEX | |
| assigned_to | INTEGER | FK → users.id ON DELETE SET NULL, NULL, INDEX | |
| name | VARCHAR(255) | NOT NULL | |
| status | VARCHAR(50) | NOT NULL, DEFAULT `'planned'` | `planned` \| `active` \| `completed` \| `aborted`. Lifecycle: new runs are `planned`; auto-transitions to `active` on first meaningful result write (plan 039); `POST /close` is the only path to `completed`. Migration `a4f9c1d27e53` rewrote existing `in_progress` rows to `active`. |
| cases_mode | VARCHAR(20) | NOT NULL, DEFAULT `'auto'`, CHECK IN (`'auto'`, `'explicit'`) | `auto` derives cases from `suite_id`/project; `explicit` uses rows in `test_run_test_cases` verbatim (may be empty). Added by migration `d8e9f0a1b2c4` (plan 034). |
| config | JSON | NOT NULL, DEFAULT `{}` | Arbitrary run config (env, browser, build, etc.) |
| completed_at | TIMESTAMPTZ | NULL | Set when `POST /close` is called |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| deleted_at | TIMESTAMPTZ | NULL, INDEX | Soft-delete timestamp |

---

### `test_results`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| test_run_id | INTEGER | FK → test_runs.id ON DELETE RESTRICT, NOT NULL, INDEX | RESTRICT; service owns cascade soft-delete |
| test_case_id | INTEGER | FK → test_cases.id ON DELETE RESTRICT, NOT NULL, INDEX | RESTRICT — keep result even if case is soft-deleted |
| tested_by | INTEGER | FK → users.id ON DELETE SET NULL, NULL, INDEX | |
| status | VARCHAR(50) | NOT NULL | `passed` \| `failed` \| `blocked` \| `no_run` (migration `c5d7e9f1a2b3` rewrote `skipped` → `no_run`) |
| comment | TEXT | NULL | |
| message | TEXT | NULL | Short error message or assertion failure |
| stack_trace | TEXT | NULL | Full stack trace for automated failures |
| execution_time | INTEGER | NULL | Duration in seconds |
| defects | JSON | NOT NULL, DEFAULT `[]` | List of defect references |
| step_results | JSON | NULL | `[{index, status, comment?}]` — per-step outcomes; null = not used |
| tested_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| updated_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| deleted_at | TIMESTAMPTZ | NULL, INDEX | Soft-delete timestamp |
| UNIQUE | | `(test_run_id, test_case_id)` — `uq_result_run_case` | Upsert semantics: submit again → update |

---

### `result_history`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| test_result_id | INTEGER | FK → test_results.id ON DELETE CASCADE, NOT NULL, INDEX | |
| changed_by | INTEGER | FK → users.id ON DELETE SET NULL, NULL | |
| status | VARCHAR(50) | NOT NULL | Status value at the time of change |
| comment | TEXT | NULL | Comment at the time of change |
| changed_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |

Append-only. Rows written on initial submit and on every status change via `PUT /test-results/{id}`.

---

### `result_attachments`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| test_result_id | INTEGER | FK → test_results.id ON DELETE CASCADE, NOT NULL, INDEX | |
| uploaded_by | INTEGER | FK → users.id ON DELETE SET NULL, NULL | |
| filename | VARCHAR(255) | NOT NULL | Original filename (path-stripped) |
| object_key | VARCHAR(1024) | NOT NULL | Storage key. For `storage_backend='s3'`: object key `results/{result_id}/{uuid}-{name}` in MinIO/S3 bucket `S3_BUCKET_ATTACHMENTS`. For `storage_backend='local'` (pre-migration rows): legacy filesystem path. Renamed from `file_path` in plan 042. |
| storage_backend | VARCHAR(16) | NOT NULL, DEFAULT `'s3'` | `'s3'` (MinIO/S3) or `'local'` (pre-plan-042 rows). Drives URL resolution in `ResultAttachmentResponse.url`. |
| file_size | INTEGER | NOT NULL | Bytes |
| mime_type | VARCHAR(255) | NULL | |
| uploaded_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |

---

### `custom_fields`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK | |
| project_id | INTEGER | FK → projects.id ON DELETE CASCADE | |
| entity_type | VARCHAR(50) | NOT NULL | `TestCase` \| `TestRun` |
| name | VARCHAR(255) | NOT NULL | |
| field_type | VARCHAR(50) | NOT NULL | `text` \| `dropdown` \| `date` \| `user` \| `number` |
| options | JSONB | NULL | For dropdown: `["Option 1", "Option 2"]` |
| is_required | BOOLEAN | DEFAULT FALSE | |
| display_order | INTEGER | DEFAULT 0 | |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

---

### `custom_field_values`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK | |
| custom_field_id | INTEGER | FK → custom_fields.id ON DELETE CASCADE | |
| entity_id | INTEGER | NOT NULL | test_case.id or test_run.id |
| value | TEXT | NULL | Stored as text; parsed by client |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | |

---

### `tags`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| name | VARCHAR(100) | UNIQUE, NOT NULL, INDEX | Global unique tag name |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |

---

### `test_case_tags`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| test_case_id | INTEGER | FK → test_cases.id ON DELETE CASCADE | |
| tag_id | INTEGER | FK → tags.id ON DELETE CASCADE | |
| PRIMARY KEY | | (test_case_id, tag_id) | |

---

### `test_run_test_cases`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| test_run_id | INTEGER | FK → test_runs.id ON DELETE CASCADE | |
| test_case_id | INTEGER | FK → test_cases.id ON DELETE CASCADE | |
| PRIMARY KEY | | (test_run_id, test_case_id) | Explicit case selection for a run; empty = legacy suite_id scoping |

---

### `audit_logs`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| user_id | INTEGER | FK → users.id ON DELETE SET NULL, NULL, INDEX | NULL for system actions |
| action | VARCHAR(50) | NOT NULL, INDEX | `CREATE` \| `UPDATE` \| `DELETE` \| `LOGIN` \| `LOGOUT` \| `WELCOME_INVITE_SENT` \| `PASSWORD_RESET_REQUESTED` \| `PASSWORD_RESET` |
| entity_type | VARCHAR(100) | NOT NULL, INDEX | `Project` \| `TestCase` \| `TestRun` \| `User` |
| entity_id | INTEGER | NULL | PK of affected entity |
| changes | JSON | NULL | Changed fields for UPDATE actions |
| ip_address | VARCHAR(45) | NULL | Client IP (IPv4 or IPv6) |
| user_agent | TEXT | NULL | HTTP User-Agent header |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL, INDEX | |

---

### `email_outbox`

Durable email queue (plan 048). Rows are written in the same transaction as the
action that triggers the email; the in-process drain worker
(`app/core/email_worker.py`) claims `pending` rows over a reused SMTP connection
and retries with backoff. Migration `a1c2e3f40576`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | SERIAL | PK, INDEX | |
| to_email | VARCHAR(255) | NOT NULL | Recipient address |
| subject | VARCHAR(500) | NOT NULL | Rendered subject line |
| template | VARCHAR(100) | NOT NULL | Template stem under `app/templates/email/` (`welcome_invite`, `password_reset`) |
| context | JSONB | NOT NULL | Render context (link, username, full_name). JSON on SQLite |
| status | VARCHAR(20) | NOT NULL, DEFAULT `'pending'`, INDEX | `pending` \| `sending` \| `sent` \| `failed` |
| attempts | INTEGER | NOT NULL, DEFAULT 0 | Send attempts made |
| max_attempts | INTEGER | NOT NULL, DEFAULT 5 | Move to `failed` once reached |
| last_error | TEXT | NULL | Truncated last send error (≤1000 chars) |
| next_attempt_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL, INDEX | Earliest time the row is eligible to claim (backoff target) |
| created_at | TIMESTAMPTZ | DEFAULT NOW(), NOT NULL | |
| sent_at | TIMESTAMPTZ | NULL | Set when delivered |

---

## Relationships summary

```
User
  ├── created_by on Project, TestCase, TestRun
  ├── assigned_to on TestRun
  ├── tested_by on TestResult
  └── changed_by on ResultHistory

Project
  ├── TestSuite[] (RESTRICT; soft-delete cascades in service)
  │     └── TestCase[] (RESTRICT; soft-delete cascades in service)
  └── TestRun[] (RESTRICT; soft-delete cascades in service)
        └── TestResult[] (RESTRICT; soft-delete cascades in service, unique per case per run)
              ├── ResultHistory[] (cascade delete, append-only)
              └── ResultAttachment[] (cascade delete)

Tag ←M2M→ TestCase (via test_case_tags)
TestRun ←M2M→ TestCase (via test_run_test_cases, explicit case selection)
CustomField → CustomFieldValue[] (per entity_id)
Milestone → TestRun[]
```

---

## Indexes

| Index | Table | Column(s) | Purpose |
|-------|-------|-----------|---------|
| `idx_test_cases_suite_id` | test_cases | suite_id | List cases by suite |
| `ix_test_cases_automation_id` | test_cases | automation_id | Look up case by external automation id |
| `idx_test_suites_project_id` | test_suites | project_id | List suites by project |
| `idx_test_runs_project_id` | test_runs | project_id | List runs by project |
| `idx_test_results_run_id` | test_results | test_run_id | List results by run |
| `idx_test_results_case_id` | test_results | test_case_id | Look up result by case |
| `idx_result_history_result_id` | result_history | test_result_id | History by result |
| `idx_audit_logs_user_id` | audit_logs | user_id | Audit trail by user |
| `idx_audit_logs_created_at` | audit_logs | created_at | Time-range queries |
| `ix_projects_deleted_at` | projects | deleted_at | Soft-delete filter |
| `ix_email_outbox_status` | email_outbox | status | Filter claimable rows |
| `ix_email_outbox_next_attempt_at` | email_outbox | next_attempt_at | Backoff eligibility |
| `ix_email_outbox_status_next_attempt_at` | email_outbox | status, next_attempt_at | Drain-worker claim hot path |
| `ix_test_suites_deleted_at` | test_suites | deleted_at | Soft-delete filter |
| `ix_test_cases_deleted_at` | test_cases | deleted_at | Soft-delete filter |
| `ix_test_runs_deleted_at` | test_runs | deleted_at | Soft-delete filter |
| `ix_test_results_deleted_at` | test_results | deleted_at | Soft-delete filter |
| `ix_milestones_deleted_at` | milestones | deleted_at | Soft-delete filter |
| `ix_users_deleted_at` | users | deleted_at | Soft-delete filter |
