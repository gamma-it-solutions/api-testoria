# Quality Score

Current state of quality metrics and targets for the Testoria backend.

---

## Current state (as of 2026-08-10)

| Dimension | Status | Notes |
|-----------|--------|-------|
| Python strict typing (mypy) | Enabled | Strict mode configured in `pyproject.toml` |
| Linting (ruff) | Passing | Replaces flake8 + isort |
| Unit test coverage | Partial | Auth service: high. Plan-048 email/token/outbox services: 100%. Plan-050: `api_key_service` 88%, `result_import_service` 93% (target ≥85%, measured). Other services: partial. Run `pytest --cov=app` to check gap. |
| Integration test coverage | Partial | Runs green on SQLite (default) — 509 passing. Plan-050's 95 new tests were also verified against real Postgres 16 (docker, port 5433). The asyncpg greenlet conflict in the session fixture still affects some older fixtures — see tech-debt.md. |
| E2E tests | Partial | Full workflow test in `tests/e2e/test_full_workflow.py`. Needs expansion. |
| API documentation | Complete | Auto-generated Swagger at `/docs`, Redoc at `/redoc`. |
| DB schema documented | Complete | `docs/06-generated/db-schema.md` |
| Endpoint reference documented | Complete | `docs/06-generated/endpoints.md` — all implemented phases documented |
| Security | Partial | JWT auth, bcrypt, RBAC implemented. Plan 050: revocable, project-scopable API keys for CI, SHA-256 hashed, effective role capped at `tester` so no key can reach lead/admin routes; keys cannot mint or revoke keys (`require_jwt`). Plan 048: single-use Redis reset/invite tokens, no user enumeration. Plan 049: no public self-registration; user management is Lead+Admin with a Lead-capped-at-Lead escalation guard; creation is invite-only (no password accepted). Token blocklist on logout not yet done. |
| Phase 3 Amendment gaps | Closed | All 4 API contract gaps shipped (message/stack_trace, result history, run-cases endpoint, attachment delete) — see tech-debt.md Resolved |
| Phase 4 (WebSockets) | Complete | Centrifugo integration done (Plan 008) |
| Phase 5 (Reporting) | Complete | Dashboard, run reports, metrics, custom reports, PDF/Excel export (Plan 009) |

---

## Targets

| Dimension | Target |
|-----------|--------|
| Unit test coverage (services) | ≥ 90% |
| Integration test coverage (endpoints) | ≥ 85% |
| E2E coverage | Full workflow: login → project → test cases → run → results |
| `ruff check` warnings | 0 |
| `mypy` errors | 0 |
| Phase 3 Amendment | All 4 gaps closed before frontend integration |
| Phase 4 | Centrifugo connected and live updates working |
| Phase 5 | All reporting endpoints working with tests |

---

## How to measure

```bash
# Unit + integration test coverage
pytest --cov=app --cov-report=term-missing
# Opens detailed coverage report

# Lint
ruff check app tests

# Type check
mypy app

# All quality checks (run before every PR)
ruff check app tests && mypy app && pytest
```

---

## Open quality improvements

1. Add JWT blocklist in Redis for true logout security
2. Fix asyncpg greenlet conflict in session-scoped test fixtures (blocks all integration tests against Postgres)
3. Expand integration test coverage for CI/CD and Defect tracking endpoints
4. Add meaningful error codes in API responses for stable client handling
5. AV-scan attachment uploads and add orphan-object GC for the MinIO bucket — see tech-debt.md
