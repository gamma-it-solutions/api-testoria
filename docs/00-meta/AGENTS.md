# AGENTS — How to work in the backend repo

Instructions for LLM agents (Claude, Copilot, etc.) operating in the Testoria backend codebase.

---

## Read these first

Before doing anything, load and internalize:

1. `docs/02-architecture/ARCHITECTURE.md` — single source of truth for structure and invariants
2. `docs/07-references/llm/backend-rules.txt` — hard rules for code generation
3. `docs/07-references/llm/coding-standards.txt` — Python/FastAPI standards
4. `docs/04-execution/tech-debt.md` — known open items

---

## Orientation: "where is X?"

| Question | Where to look |
|----------|---------------|
| What does this API do? | `docs/01-product/index.md` |
| What endpoints exist? | `docs/06-generated/endpoints.md` or `app/api/v1/*.py` |
| What DB tables exist? | `docs/06-generated/db-schema.md` or `app/models/*.py` |
| Where does business logic live? | `app/services/<domain>_service.py` |
| How does auth work? | `docs/02-architecture/backend/auth.md` or `app/core/security.py` + `app/dependencies.py` |
| How are DB sessions managed? | `docs/02-architecture/backend/data-layer.md` or `app/database.py` |
| How are errors handled? | `docs/03-engineering/patterns/error-handling.md` or `app/core/exceptions.py` |
| Known tech debt? | `docs/04-execution/tech-debt.md` |
| In-progress work? | `docs/04-execution/exec-plans/active/` |

---

## How to navigate the codebase

```
app/
  api/v1/     → FastAPI routers, one file per domain — only HTTP concerns here
  services/   → Business logic, module-level async functions per domain
  models/     → SQLAlchemy ORM models, one file per entity
  schemas/    → Pydantic request/response schemas, one file per domain
  core/       → Security, permissions, exceptions, caching, Centrifugo client
  tasks/      → Celery async tasks
  utils/      → Pagination, validators, file handlers
  database.py → Async engine + session factory
  dependencies.py → FastAPI Depends: get_db, get_current_user
  config.py   → Pydantic Settings from .env
  main.py     → FastAPI app wiring
```

Layer flow: `Router → Service → Model/DB` — never skip a layer.

---

## Work cycle

All work follows a five-phase cycle. Short version:

1. **Orient** — check `docs/04-execution/exec-plans/active/` and `docs/04-execution/tech-debt.md`
2. **Plan** — read relevant docs, create plan in `docs/04-execution/exec-plans/active/`
3. **Execute** — implement code, tick plan checkboxes as tasks complete
4. **Quality check** — `pytest`, `ruff`, `mypy`, `docs/05-quality/checklists/pr-checklist.md`
5. **Update docs** — go through every row of the Phase 5 table in `CLAUDE.md` and update every applicable doc. Do not stop at `changelog.md`. Specifically:
   - `docs/08-decisions/changelog.md` — **always**, every plan and every standalone change
   - `docs/04-execution/tech-debt.md` — any new debt incurred **or** resolved
   - `docs/05-quality/QUALITY_SCORE.md` — when quality state changes (tests blocked/fixed, infra added, coverage shifts)
   - `docs/06-generated/endpoints.md` — new or changed endpoints
   - `docs/06-generated/db-schema.md` — new or changed models
   - `docs/01-product/features/<NNN>-<feature>.md` — new or changed feature: update existing file or create one if it doesn't exist (one file per feature/domain, named with a zero-padded 3-digit prefix: `001-auth.md`, `002-user-management.md`, etc.). Also update `docs/01-product/index.md` if it references the feature (e.g. remove "pending" markers, fix technology descriptions).
   - `docs/02-architecture/ARCHITECTURE.md` — new domain added or invariant changed
   - `docs/02-architecture/backend/*.md` — new domain, endpoint pattern, service, data-layer change, or migration added: update the relevant sub-doc (`api-layer.md`, `service-layer.md`, `data-layer.md`, `auth.md`) so inventories, examples, and migration history stay current
   - `docs/03-engineering/patterns/` — new pattern introduced
   - `docs/00-meta/CONTRIBUTING.md` — setup steps or first-run instructions changed
   - `docs/03-engineering/BACKEND.md` — dev commands or workflow changed
   Only then move the plan file to `completed/`.

> **Rule: docs are always updated after every change** — this applies to plan-driven work AND to small standalone changes (scripts, config, tooling). If code changed, at minimum `changelog.md` gets an entry. If a user-facing setup step changed, `CONTRIBUTING.md` and `BACKEND.md` are updated too. Never finish a task with stale docs.

---

## Making changes

### Adding a new endpoint

1. Create execution plan in `docs/04-execution/exec-plans/active/`
2. Add Pydantic schemas in `app/schemas/<domain>.py`
3. Add/update SQLAlchemy model in `app/models/<domain>.py`
4. Create Alembic migration if schema changed
5. Implement service method in `app/services/<domain>_service.py`
6. Add route in `app/api/v1/<domain>.py`
7. Wire router into `app/main.py` if it is a new file
8. Write unit tests for the service, integration tests for the endpoint
9. Update `docs/06-generated/endpoints.md`
10. Update `docs/06-generated/db-schema.md` if the model changed

### Adding a new domain (e.g., comments)

Create: `app/schemas/comment.py`, `app/models/comment.py`, `app/services/comment_service.py`, `app/api/v1/comments.py`. Follow existing domain files as templates. Add migration, wire router, write tests.

### Modifying an existing endpoint

- Read the existing router and service files before changing anything
- Check `ARCHITECTURE.md` invariants — do not violate them
- If the response shape changes, update the Pydantic schema and `docs/06-generated/endpoints.md`
- If the DB model changes, create a new Alembic migration — never edit an existing migration

---

## What NOT to do

- Do not put business logic in router functions — that belongs in `app/services/`
- Do not query the database directly from a router — always go through a service
- Do not use synchronous SQLAlchemy — all DB calls must be `async`/`await`
- Do not hardcode secrets or connection strings — all config comes from `app/config.py` via `.env`
- Do not edit an existing Alembic migration — always create a new revision
- Do not return SQLAlchemy model objects directly from routes — serialize via Pydantic schemas
- Do not bypass `get_current_user` dependency on protected routes

---

## Running the backend

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt

# Database
alembic upgrade head

# Dev server
uvicorn app.main:app --reload --port 8000

# Tests
pytest
pytest --cov=app --cov-report=term-missing

# Linting
ruff check app tests
ruff format app tests

# Type checking
mypy app
```

API docs auto-generated at `http://localhost:8000/docs` (Swagger) and `/redoc`.

---

## Key invariants (never break these)

1. All DB access is async — no synchronous `session.execute()` calls
2. Business logic lives in `app/services/` — routers are thin HTTP handlers only
3. All protected routes use `Depends(get_current_user)` — never bypass auth
4. All config is in `app/config.py` from environment — no hardcoded values
5. Never edit an existing Alembic migration file — create a new revision
6. Pydantic schemas are the only response types returned from routers
