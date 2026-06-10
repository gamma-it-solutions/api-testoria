# Quality Score

Current state of quality metrics and targets for the Testoria backend.

---

## Current state (as of 2026-06-03)

| Dimension | Status | Notes |
|-----------|--------|-------|
| Python strict typing (mypy) | Enabled | Strict mode configured in `pyproject.toml` |
| Linting (ruff) | Passing | Replaces flake8 + isort |
| Unit test coverage | Partial | Auth service: high. Plan-048 email/token/outbox services: 100% (`email_outbox_service`, `password_token_service`, `email_service`). Other services: partial. Run `pytest --cov=app` to check gap. |
| Integration test coverage | Blocked | Docker Postgres test stack available (port 5433). Tests blocked by asyncpg greenlet conflict in session fixture — see tech-debt.md. (The plan-048 outbox SKIP-LOCKED concurrency test is Postgres-only and skips on SQLite.) |
| E2E tests | Partial | Full workflow test in `tests/e2e/test_full_workflow.py`. Needs expansion. |
| API documentation | Complete | Auto-generated Swagger at `/docs`, Redoc at `/redoc`. |
| DB schema documented | Complete | `docs/06-generated/db-schema.md` |
| Endpoint reference documented | Complete | `docs/06-generated/endpoints.md` — all implemented phases documented |
| Security | Partial | JWT auth, bcrypt, RBAC implemented. Plan 048: single-use Redis reset/invite tokens, no user enumeration. Plan 049: no public self-registration; user management is Lead+Admin with a Lead-capped-at-Lead escalation guard; creation is invite-only (no password accepted). Token blocklist on logout not yet done. |
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
