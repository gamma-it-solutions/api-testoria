# Feature: 004 — Test Execution

## What it does

Provides the full test execution loop: testers open a run, record pass/fail/blocked/no_run against each test case, upload evidence, and close the run. `no_run` replaces the older `skipped` label (plan 032); clients can still send `skipped` during the compat window and it is normalised to `no_run` on persist. `no_run` is also the default status when a result is submitted with no explicit pick.

### Run lifecycle (plan 039)

Runs move through three states on the happy path:

1. `planned` — freshly created, no work recorded yet.
2. `active` — auto-transitioned from `planned` the first time a tester submits or updates a result with a meaningful change (status or comment). The transition runs inside the same transaction as the result write and fires a `test_run_status` Centrifugo event. Subsequent result writes do not re-transition.
3. `completed` — only reachable via `POST /test-runs/{id}/close`. Closing a run freezes its case set and moves its results into the project-wide pass-rate KPI.

`aborted` remains off the happy path (set manually via `PUT /test-runs/{id}`) and is not counted in any pass-rate statistic. The old `in_progress` status has been renamed to `active`; input payloads still accept `in_progress` for one release cycle and are normalised to `active` before persist.

## API surface

### Milestones

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | `/api/v1/projects/{id}/milestones` | read_only | List milestones for project |
| POST | `/api/v1/projects/{id}/milestones` | lead | Create milestone |
| PUT | `/api/v1/milestones/{id}` | lead | Update milestone |
| DELETE | `/api/v1/milestones/{id}` | lead | Delete milestone |

### Test Runs

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | `/api/v1/projects/{id}/test-runs` | read_only | List runs (filterable by status) |
| POST | `/api/v1/projects/{id}/test-runs` | tester | Create run |
| GET | `/api/v1/test-runs/{id}` | read_only | Get run |
| PUT | `/api/v1/test-runs/{id}` | tester | Update run |
| DELETE | `/api/v1/test-runs/{id}` | lead | Delete run |
| POST | `/api/v1/test-runs/{id}/close` | tester | Close run (status → completed) |
| GET | `/api/v1/test-runs/{id}/progress` | read_only | Pass/fail/blocked/no_run counts (scoped to the run's current case-set) |
| PUT | `/api/v1/test-runs/{id}/cases` | tester | Replace the explicit case selection set |
| GET | `/api/v1/test-runs/{id}/cases` | read_only | All cases with current results (max 500) |

### Test Results

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | `/api/v1/test-runs/{id}/results` | read_only | All results for a run |
| POST | `/api/v1/test-runs/{id}/results` | tester | Submit result (upsert by test_case_id) |
| GET | `/api/v1/test-results/{id}` | read_only | Get single result |
| PUT | `/api/v1/test-results/{id}` | tester | Update result |
| GET | `/api/v1/test-results/{id}/history` | read_only | Status change audit trail |
| POST | `/api/v1/test-results/{id}/attachments` | tester | Upload file attachment |
| DELETE | `/api/v1/test-results/{id}/attachments/{attach_id}` | tester | Delete attachment |

## Key constraints

- `POST /results` is an upsert: one result per (run, test_case) pair. Second submit updates the existing row.
- History rows are append-only: one on initial submit, one per status change after that.
- Attachments stored at `{UPLOAD_DIR}/{result_id}/{filename}`. Missing files on delete are silently skipped.
- `GET /cases` accepts `limit` (default 500, max 2000), `offset`, and `sort`. Returns `total` alongside `cases`. No recursive CTE — only direct `suite_id` filtering.
- **Scope-consistent reads**: `GET /cases`, `GET /results`, and `GET /progress` all use the same rules to decide which cases belong to a run — junction rows for explicit mode, project/suite-derived for auto. Orphan `TestResult` rows (case removed from scope after submit) are hidden from `/results` and excluded from `/progress` counts by default. `GET /results?include_orphans=true` returns them for audit.
- `total_test_runs` and `pass_rate` in `/projects/{id}/stats` now return real data.
- **Explicit case selection**: `POST /test-runs` accepts `include_test_cases: list[int]` to scope a run to specific cases. When provided, `GET /cases` and `GET /progress` use the association table. When omitted (`null`), the run falls back to legacy `suite_id` scoping. An empty list (`[]`) means "no cases". All case ids must belong to the run's project.
- `PUT /test-runs/{id}/cases` replaces the case set atomically (PUT semantics). Validates project membership for all ids. Returns 409 if the run's status is `completed` — the case set is frozen on closed runs; reopen the run first (PUT its status back to `active`) to edit.
- **Per-step results**: `POST /results` and `PUT /test-results/{id}` accept an optional `step_results` list where each entry has `{index, status, comment?}`. The `index` refers to the position in the test case's `steps` array. Out-of-range or duplicate indices are rejected with 400. Partial coverage is allowed — not every step needs a result. The overall `status` remains manually set by the tester (not auto-derived from step results).
