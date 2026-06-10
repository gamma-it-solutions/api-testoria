# Execution Plan: 002 — Backend Phase 2: Core Test Management

**Date**: 2026-03-24
**Author**: gabi
**Status**: Completed
**Priority**: CRITICAL
**Dependency**: 012-be-phase1-foundation-auth must be complete

---

## Goal

Implement the full CRUD API for the core test management domain: Projects, Test Suites (hierarchical), and Test Cases (with JSONB steps, search, filtering, import/export).

---

## Context

Phase 2 of `backend-implementation.md`. After Phase 1 provides auth, this phase builds the test management backbone. The frontend's project list, suite tree, and test case editor all depend on these endpoints.

---

## Scope

### In scope

**Models & migrations:**
- `app/models/project.py` — Project
- `app/models/test_suite.py` — TestSuite (self-referential parent_suite_id)
- `app/models/test_case.py` — TestCase (steps as JSONB)
- `app/models/tag.py` — Tag + TestCaseTags join table
- Alembic migration for all new tables

**Schemas:**
- `app/schemas/project.py` — ProjectCreate, ProjectUpdate, ProjectResponse, ProjectStats
- `app/schemas/test_suite.py` — TestSuiteCreate, TestSuiteUpdate, TestSuiteResponse
- `app/schemas/test_case.py` — TestCaseCreate, TestCaseUpdate, TestCaseResponse, TestStep

**Services:**
- `app/services/project_service.py` — CRUD + stats calculation
- `app/services/test_suite_service.py` — CRUD + hierarchy helpers
- `app/services/test_case_service.py` — CRUD + search + filter
- `app/services/import_service.py` — CSV/Excel import
- `app/services/export_service.py` — CSV/Excel export

**Routers:**
- `app/api/v1/projects.py` — GET/POST/PUT/DELETE/stats
- `app/api/v1/test_suites.py` — GET/POST/PUT/DELETE under projects
- `app/api/v1/test_cases.py` — GET/POST/PUT/DELETE + import + export

**Tests:**
- `tests/integration/test_projects_api.py`
- `tests/integration/test_test_suites_api.py`
- `tests/integration/test_test_cases_api.py`

### Out of scope
- Test Runs and Results (Phase 3)
- Milestones (Phase 3)
- Custom Fields (Phase 8)

---

## Technical approach

### Endpoints

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| GET | `/projects` | viewer | List projects (pagination, include_archived) |
| POST | `/projects` | project_manager | Create project |
| GET | `/projects/{id}` | viewer | Get project |
| PUT | `/projects/{id}` | project_manager | Update project |
| DELETE | `/projects/{id}` | admin | Delete project |
| GET | `/projects/{id}/stats` | viewer | Project statistics |
| GET | `/projects/{id}/test-suites` | viewer | List suites (tree) |
| POST | `/projects/{id}/test-suites` | project_manager | Create suite |
| GET | `/test-suites/{id}` | viewer | Get suite |
| PUT | `/test-suites/{id}` | project_manager | Update suite |
| DELETE | `/test-suites/{id}` | project_manager | Delete suite |
| GET | `/projects/{id}/test-cases` | viewer | List cases (search, filter by suite/priority/type) |
| POST | `/projects/{id}/test-cases` | project_manager | Create case |
| GET | `/test-cases/{id}` | viewer | Get case |
| PUT | `/test-cases/{id}` | project_manager | Update case |
| DELETE | `/test-cases/{id}` | project_manager | Delete case |
| POST | `/projects/{id}/test-cases/import` | project_manager | Bulk import CSV/Excel |
| GET | `/projects/{id}/test-cases/export` | viewer | Export CSV/Excel |

### TestCase steps JSONB

```python
# steps stored as JSONB list
steps = Column(JSONB, nullable=False, default=[])
# each step: {"step": "Navigate to login", "expected": "Login page shown"}
```

### Suite hierarchy

TestSuite has `parent_suite_id INTEGER REFERENCES test_suites(id)` — nullable for root suites. Suite tree is fetched via multiple queries (recursive CTE for PostgreSQL or iterative for small datasets).

### Project stats query

```python
@staticmethod
async def get_stats(db: AsyncSession, project_id: int) -> dict:
    total_cases = await db.scalar(
        select(func.count(TestCase.id)).join(TestSuite).where(TestSuite.project_id == project_id)
    )
    total_runs = await db.scalar(
        select(func.count(TestRun.id)).where(TestRun.project_id == project_id)
    )
    # pass_rate calculated from latest results across all runs
    return {"total_test_cases": total_cases, "total_test_runs": total_runs, ...}
```

---

## Tasks

### Models & migration
- [ ] Write `app/models/project.py` — Project with relationships to TestSuite, TestRun
- [ ] Write `app/models/test_suite.py` — TestSuite with self-referential parent_suite_id
- [ ] Write `app/models/test_case.py` — TestCase with steps JSONB
- [ ] Write `app/models/tag.py` — Tag + test_case_tags join table
- [ ] `alembic revision --autogenerate -m "Add projects test_suites test_cases tags"` — review and apply

### Schemas
- [ ] Write `app/schemas/project.py` — ProjectCreate, ProjectUpdate, ProjectResponse, ProjectStats
- [ ] Write `app/schemas/test_suite.py` — TestSuiteCreate, TestSuiteUpdate, TestSuiteResponse
- [ ] Write `app/schemas/test_case.py` — TestStep, TestCaseCreate, TestCaseUpdate, TestCaseResponse

### Services
- [ ] Write `app/services/project_service.py` — list, get, create, update, delete, get_stats
- [ ] Write `app/services/test_suite_service.py` — list (with children), get, create, update, delete
- [ ] Write `app/services/test_case_service.py` — list (search + filter), get, create, update, delete
- [ ] Write `app/services/import_service.py` — parse CSV/Excel rows → bulk create TestCase rows
- [ ] Write `app/services/export_service.py` — query TestCases → generate CSV/Excel bytes

### Routers
- [ ] Write `app/api/v1/projects.py` — all project endpoints
- [ ] Write `app/api/v1/test_suites.py` — all suite endpoints
- [ ] Write `app/api/v1/test_cases.py` — all test case endpoints including import/export
- [ ] Register all three routers in `app/main.py`

### Tests
- [ ] `tests/integration/test_projects_api.py` — CRUD, stats, 401/403 checks
- [ ] `tests/integration/test_test_suites_api.py` — CRUD, parent/child relationships
- [ ] `tests/integration/test_test_cases_api.py` — CRUD, search by title, filter by priority/type, import CSV, export CSV

### Quality check
- [ ] `pytest` passes
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean

### Docs
- [ ] `api/docs/06-generated/endpoints.md` — verify projects/suites/test-cases rows
- [ ] `api/docs/06-generated/db-schema.md` — add projects, test_suites, test_cases, tags tables
- [ ] Move to `completed/`

---

## Definition of done

- [ ] All project CRUD endpoints return correct HTTP status codes and response shapes
- [ ] Test suites support parent-child hierarchy (parent_suite_id)
- [ ] Test cases store steps as JSONB and return them in responses
- [ ] Search by title and filter by suite/priority/type work correctly
- [ ] CSV import creates test cases and returns count
- [ ] CSV export returns downloadable file with all test case fields
- [ ] 401 without auth, 403 for insufficient role, 404 for unknown IDs
- [ ] Integration tests pass with >85% coverage on these endpoints
