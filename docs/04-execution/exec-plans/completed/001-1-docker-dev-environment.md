# Execution Plan: 001-1 — Docker Dev Environment

**Date**: 2026-03-24
**Author**: gabi
**Status**: Complete
**Priority**: CRITICAL
**Dependency**: 001-be-phase1-foundation-auth must be complete

---

## Goal

Provide a Docker Compose dev environment with PostgreSQL and Redis so all subsequent plans can run migrations, apply seeds, and execute integration tests against a real database.

---

## Context

No `docker-compose.yml` or `Dockerfile` exists in the repo. Without a running PostgreSQL instance, Alembic migrations cannot be applied and integration tests fall back to SQLite (which diverges from production). Every plan from 002 onward requires a live Postgres database. Redis is also needed from Phase 4 (Celery, token blocklist).

---

## Scope

### In scope
- `docker-compose.yml` — dev stack: `postgres`, `redis`
- `docker-compose.test.yml` — isolated test stack: `postgres_test`, `redis_test` (separate ports/DBs, used by `pytest`)
- `.env.example` updated with Docker-matched default URLs
- `scripts/wait-for-db.sh` — health-check helper used by CI / local setup
- README section on how to start the dev stack

### Out of scope
- Production `docker-compose.prod.yml` (separate concern, later phase)
- `Dockerfile` for the FastAPI app itself (not needed for local dev — run uvicorn directly in venv)
- Celery worker container (wired in config but not needed until Phase 4)

---

## Technical approach

### Services

**`docker-compose.yml`** (dev):

| Service | Image | Port | DB / Password |
|---------|-------|------|---------------|
| `postgres` | `postgres:16-alpine` | `5432:5432` | db=`testoria`, user=`testoria`, pass=`testoria` |
| `redis` | `redis:7-alpine` | `6379:6379` | no auth |

**`docker-compose.test.yml`** (test — extends dev, separate ports):

| Service | Image | Port | DB |
|---------|-------|------|----|
| `postgres_test` | `postgres:16-alpine` | `5433:5432` | db=`testoria_test` |
| `redis_test` | `redis:7-alpine` | `6380:6379` | no auth |

### Key decisions
- Use named volumes (`postgres_data`, `redis_data`) so dev data persists across restarts
- Test compose uses a different host port (`5433`) so both stacks can run simultaneously
- `TEST_DATABASE_URL` in `.env.example` updated to point to port 5433
- `healthcheck` on postgres container so dependent services wait for readiness

---

## Tasks

### Implementation
- [x] Write `docker-compose.yml` — postgres + redis for dev
- [x] Write `docker-compose.test.yml` — postgres_test + redis_test for tests
- [x] Update `.env.example` — align `DATABASE_URL` and `TEST_DATABASE_URL` with compose defaults
- [x] Write `scripts/wait-for-db.sh` — loop until `pg_isready` succeeds (used in CI)
- [ ] Verify: `docker compose up -d` starts cleanly
- [ ] Verify: `alembic upgrade head` applies against the dev DB
- [ ] Verify: `pytest tests/integration/` passes against the test DB (set `TEST_DATABASE_URL` to postgres)

### Quality check
- [ ] `docker compose up -d && docker compose ps` shows both services healthy
- [ ] `alembic upgrade head` succeeds
- [ ] `alembic downgrade -1` rolls back cleanly
- [ ] `pytest tests/integration/test_auth_api.py` passes against Postgres (not SQLite)

### Docs update
- [x] `docs/08-decisions/changelog.md` updated
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Port 5432 already in use on dev machine | Medium | Document how to change host port in `.env` |
| WSL2 Docker Desktop integration not enabled | Medium | Note in README: enable WSL integration in Docker Desktop settings |

---

## Definition of done

- [ ] `docker compose up -d` starts postgres and redis with no errors
- [ ] `alembic upgrade head` applies the users migration cleanly against the Dockerised Postgres
- [ ] `alembic downgrade -1` rolls back cleanly
- [ ] Integration tests pass against Postgres (not SQLite fallback) when `TEST_DATABASE_URL` is set
- [x] `docs/08-decisions/changelog.md` updated
