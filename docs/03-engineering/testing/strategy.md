# Testing Strategy

Testing approach for the Testoria backend.

---

## Layers

| Layer | Tool | What it tests |
|-------|------|---------------|
| Unit | pytest + pytest-asyncio | Service methods, utility functions, security helpers |
| Integration | pytest + httpx `AsyncClient` | Full endpoint flow (HTTP → service → DB) |
| E2E | pytest (full workflow) | Multi-step scenarios across multiple endpoints |

---

## Philosophy

**Test services and endpoints, not implementation details.**

Services contain the business logic. Routers are thin HTTP handlers. Prioritize:

1. Service methods (create, update, delete, edge cases, error paths)
2. API integration tests (HTTP status codes, response shapes, auth enforcement)
3. Security: auth required, role enforcement, 401/403 responses
4. E2E: the critical workflow (login → create project → add test cases → run → submit results)

Do not test:
- Pydantic validation (FastAPI/Pydantic handles it)
- SQLAlchemy internals
- Python standard library code

---

## Test database

Integration tests use a separate test database (`testoria_test`). The `conftest.py` in `tests/` creates all tables at session start and drops them at the end:

```python
@pytest.fixture(scope="session")
async def test_db():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
```

Each test gets its own session that rolls back after the test — no data bleeds between tests.

---

## Test fixtures (conftest.py)

Key fixtures available in all tests:

| Fixture | Scope | What it provides |
|---------|-------|-----------------|
| `event_loop` | session | asyncio event loop |
| `test_db` | session | test engine, creates/drops schema |
| `db_session` | function | `AsyncSession`, auto-rollback after test |
| `client` | function | `httpx.AsyncClient` against the FastAPI app |
| `test_user` | function | `tester` role User in DB |
| `admin_user` | function | `admin` role User in DB |
| `auth_headers` | function | `{"Authorization": "Bearer <token>"}` for `test_user` |
| `admin_headers` | function | `{"Authorization": "Bearer <token>"}` for `admin_user` |

---

## File placement

```
tests/
├── conftest.py               — session-scoped fixtures
├── unit/
│   ├── test_auth_service.py
│   ├── test_project_service.py
│   ├── test_test_result_service.py
│   └── test_security.py
├── integration/
│   ├── test_auth_api.py
│   ├── test_projects_api.py
│   ├── test_test_cases_api.py
│   ├── test_test_runs_api.py
│   ├── test_test_results_api.py
│   └── test_reports_api.py
└── e2e/
    └── test_full_workflow.py
```

---

## Coverage targets

| Layer | Target |
|-------|--------|
| Unit (services) | ≥ 90% |
| Integration (endpoints) | ≥ 85% |
| E2E | Critical user paths |

Run: `pytest --cov=app --cov-report=term-missing`
