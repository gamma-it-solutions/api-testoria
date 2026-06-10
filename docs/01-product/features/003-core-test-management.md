# Feature: 003 — Core Test Management

## What it does

Provides full CRUD API for the core test management hierarchy: **Projects → TestSuites → TestCases**.

## API surface

### Projects

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | `/api/v1/projects` | read_only | List projects (pagination, optional archived) |
| POST | `/api/v1/projects` | lead | Create project |
| GET | `/api/v1/projects/{id}` | read_only | Get project |
| PUT | `/api/v1/projects/{id}` | lead | Update project |
| DELETE | `/api/v1/projects/{id}` | admin | Delete project (cascade) |
| GET | `/api/v1/projects/{id}/stats` | read_only | Counts: test cases, suites, runs, pass rate |

### Test Suites

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | `/api/v1/projects/{id}/test-suites` | read_only | Flat list of suites (client builds tree) |
| POST | `/api/v1/projects/{id}/test-suites` | lead | Create suite (optional parent_suite_id) |
| GET | `/api/v1/test-suites/{id}` | read_only | Get suite |
| PUT | `/api/v1/test-suites/{id}` | lead | Update suite |
| DELETE | `/api/v1/test-suites/{id}` | lead | Delete suite (cascades to children and test cases) |

### Test Cases

| Method | Path | Min Role | Description |
|--------|------|----------|-------------|
| GET | `/api/v1/projects/{id}/test-cases` | read_only | List cases with search + filter (by suite, priority, type, status, tag_ids, automation_id) |
| POST | `/api/v1/projects/{id}/test-cases` | lead | Create test case |
| GET | `/api/v1/test-cases/{id}` | read_only | Get test case |
| PUT | `/api/v1/test-cases/{id}` | lead | Update test case |
| DELETE | `/api/v1/test-cases/{id}` | lead | Delete test case |
| POST | `/api/v1/projects/{id}/test-cases/import` | lead | Bulk import from CSV or Excel |
| GET | `/api/v1/projects/{id}/test-cases/export` | read_only | Export all cases as CSV or Excel |

## Key schemas

**TestCaseCreate / TestCaseUpdate**
- `suite_id: int`
- `title: str`
- `steps: [{ step, expected }]` — stored as JSON
- `priority: low | medium | high | critical`
- `type: manual | automated`
- `status: draft | active | deprecated`
- `tags: list[str]` — auto-created if new
- `automation_id: str | null` — optional external test framework identifier (e.g. pytest node id, Playwright spec)

**Import CSV columns:** `title, description, preconditions, steps_json, priority, type, status, suite_id, tags`

## Constraints

- Suite hierarchy is unlimited depth; deleting a parent deletes all descendants (DB CASCADE).
- Tags are global (not per-project); reused across projects.
- `total_test_runs` and `pass_rate` in stats are 0/null until Phase 3 is implemented.
- Import/export run in-request (no Celery offload); large files may be slow.
