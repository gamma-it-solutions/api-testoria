# Execution Plan: 013 — Backend Phase 9: Testing & Documentation

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: HIGH
**Dependency**: Plans 012–018 must be complete (all features implemented)

---

## Goal

Reach the final quality bar: unit test coverage >90%, integration test coverage >85%, E2E workflow tests, enriched OpenAPI documentation, and backend README with setup, migration, and security guides.

---

## Context

Phase 9 of `backend-implementation.md`. This is the hardening and documentation pass that makes the backend maintainable and trustworthy. It is a pure quality-and-docs phase — no new features. Run after all functional phases are complete.

---

## Scope

### In scope

**Unit tests (fill coverage gaps):**
- `tests/unit/test_auth_service.py` — password hashing, token creation, token decode
- `tests/unit/test_project_service.py` — service layer logic (mocked DB)
- `tests/unit/test_test_case_service.py` — search, filter logic
- `tests/unit/test_report_service.py` — pass rate calculation
- `tests/unit/test_ci_service.py` — JUnit XML parsing, badge SVG output
- `tests/unit/test_audit_service.py` — log_action builds correct AuditLog fields
- `tests/unit/test_permissions.py` — has_permission returns correct booleans per role

**Integration tests (expand existing):**
- Fill any endpoint not covered by plans 012–018 integration tests
- Verify all 401/403/404 paths for every router

**E2E tests:**
- `tests/e2e/test_full_workflow.py` — create project → add suite → add test case → create run → submit result → get dashboard → close run

**OpenAPI documentation:**
- Update `app/main.py` with enriched title, description, openapi_tags, version
- Add `summary=`, `description=`, `response_description=` to all router endpoints

**Docs:**
- `README.md` — setup instructions (Docker, venv), running migrations, env vars table, running tests
- `docs/MIGRATIONS.md` — migration guide (how to create, apply, roll back)
- `docs/SECURITY.md` — auth flow, RBAC, secrets management, HTTPS requirements

### Out of scope
- Load testing / performance benchmarks
- API versioning beyond v1
- New features of any kind

---

## Technical approach

### Coverage measurement

```bash
pytest --cov=app --cov-report=term-missing --cov-report=html
# Targets:
#   Unit:        >90%
#   Integration: >85%
#   E2E:         >70%
```

### E2E test structure

```python
# tests/e2e/test_full_workflow.py

@pytest.mark.asyncio
async def test_full_tester_workflow(client, admin_headers, tester_headers):
    # 1. Admin creates project
    proj = (await client.post("/api/v1/projects", json={"name": "E2E Project", "key": "E2E"},
                               headers=admin_headers)).json()

    # 2. Admin creates suite
    suite = (await client.post(f"/api/v1/projects/{proj['id']}/test-suites",
                                json={"name": "Smoke"}, headers=admin_headers)).json()

    # 3. Admin creates test case
    case = (await client.post(f"/api/v1/projects/{proj['id']}/test-cases",
                               json={"title": "Login works", "suite_id": suite["id"],
                                     "steps": [{"step": "Open app", "expected": "Login shown"}],
                                     "priority": "High", "type": "Functional"},
                               headers=admin_headers)).json()

    # 4. Tester creates run
    run = (await client.post(f"/api/v1/projects/{proj['id']}/test-runs",
                              json={"name": "Sprint 1"}, headers=tester_headers)).json()

    # 5. Tester submits result
    result = (await client.post(f"/api/v1/test-runs/{run['id']}/results",
                                 json={"test_case_id": case["id"], "status": "Passed"},
                                 headers=tester_headers)).json()
    assert result["status"] == "Passed"

    # 6. Dashboard reflects the result
    dashboard = (await client.get(f"/api/v1/projects/{proj['id']}/dashboard",
                                   headers=tester_headers)).json()
    assert dashboard["pass_rate"] == 100.0

    # 7. Tester closes run
    close = await client.post(f"/api/v1/test-runs/{run['id']}/close", headers=tester_headers)
    assert close.status_code == 200
```

### OpenAPI enrichment

```python
app = FastAPI(
    title="Testoria API",
    description="""
# Testoria — Self-Hosted Test Management Platform

## Authentication
All endpoints require `Authorization: Bearer <access_token>` except `/auth/login`.

## Rate Limiting
100 requests per minute per user (enforced at reverse proxy level).

## Versioning
Current API version: **v1**
    """,
    version="1.0.0",
    openapi_tags=[
        {"name": "Authentication", "description": "Login, token refresh, current user"},
        {"name": "Projects",       "description": "Project CRUD and statistics"},
        {"name": "Test Suites",    "description": "Hierarchical test suite management"},
        {"name": "Test Cases",     "description": "Test case CRUD, import, export"},
        {"name": "Test Runs",      "description": "Run creation and execution tracking"},
        {"name": "Test Results",   "description": "Result submission and attachments"},
        {"name": "Reports",        "description": "Dashboard, metrics, PDF/Excel export"},
        {"name": "CI/CD",          "description": "Webhook receiver, bulk import, badges"},
        {"name": "Defects",        "description": "Jira, GitHub, GitLab issue creation"},
        {"name": "WebSocket",      "description": "Centrifugo connection and subscription tokens"},
        {"name": "Users",          "description": "User management (admin only)"},
        {"name": "Health",         "description": "Health check"},
    ],
)
```

---

## Tasks

### Unit tests
- [ ] Write `tests/unit/test_auth_service.py` — verify_password, create_access_token, decode_token
- [ ] Write `tests/unit/test_project_service.py` — list, get, create, stats (mocked AsyncSession)
- [ ] Write `tests/unit/test_test_case_service.py` — search filter logic
- [ ] Write `tests/unit/test_report_service.py` — pass_rate formula edge cases (0 results, all failed)
- [ ] Write `tests/unit/test_ci_service.py` — JUnit XML parsing with all result types; badge SVG output
- [ ] Write `tests/unit/test_audit_service.py` — log_action creates correct AuditLog object
- [ ] Write `tests/unit/test_permissions.py` — has_permission for every role/permission combination

### Integration tests (gaps)
- [ ] Audit all existing integration test files — add missing 401/403/404 test cases for each endpoint
- [ ] Confirm every route registered in `app/main.py` has at least one integration test

### E2E tests
- [ ] Write `tests/e2e/test_full_workflow.py` — full tester journey (see above)
- [ ] Write `tests/e2e/test_admin_workflow.py` — admin creates user, assigns role, user logs in

### OpenAPI & in-code docs
- [ ] Update `app/main.py` — enriched FastAPI() constructor with description and openapi_tags
- [ ] Add `summary=` and `response_description=` to all router endpoints across `app/api/v1/`

### Written documentation
- [ ] Write `README.md` — quick start (Docker Compose), venv setup, env vars table, running tests
- [ ] Write `docs/MIGRATIONS.md` — how to create, apply, and roll back Alembic migrations
- [ ] Write `docs/SECURITY.md` — JWT lifecycle, RBAC, secrets, HTTPS, audit log

### Quality check
- [ ] `pytest --cov=app` — unit coverage >90%, integration coverage >85%
- [ ] `ruff check app tests` clean
- [ ] `mypy app` clean
- [ ] `docker compose up` starts all services cleanly

### Docs
- [ ] `api/docs/05-quality/QUALITY_SCORE.md` — update coverage numbers
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `pytest --cov=app` reports >90% unit coverage and >85% integration coverage
- [ ] E2E test: full project → suite → case → run → result → dashboard → close workflow passes
- [ ] Every router endpoint has `summary=` in its decorator
- [ ] `README.md` lets a new developer start the backend from scratch with Docker Compose
- [ ] `docs/MIGRATIONS.md` explains create/apply/rollback with exact commands
- [ ] OpenAPI UI at `/docs` renders all endpoint descriptions and tags
