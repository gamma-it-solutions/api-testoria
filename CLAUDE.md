# Testoria API Backend

FastAPI backend for the Testoria test management platform. Exposes a REST API consumed by the Vue 3 frontend and the Python CLI tool. Owns the PostgreSQL database, file storage, and event publishing pipeline to Centrifugo.
Core hierarchy: **Project → TestSuite → TestCase → TestRun → TestResult**
Stack: Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL · Alembic · Pydantic v2 · Redis · Celery · Centrifugo · pytest

---

## Hard invariants — never break these

1. No business logic in routers — routers call one service method and return a Pydantic schema
2. No direct DB queries in routers — always through a service
3. All DB calls are `async def` / `await` — no synchronous SQLAlchemy anywhere
4. Config from `app/config.py` only — no hardcoded secrets, URLs, or connection strings
5. Never return ORM model objects from routers — always serialize via Pydantic `response_model`
6. Never edit an existing Alembic migration — always create a new revision
7. All protected routes use `Depends(get_current_user)` or `Depends(require_role(...))` — no bypass

Layer flow: `Router → Service → Model/DB` — never skip a layer.

---

## Standard work cycle

Every piece of work — bug fix, improvement, or new feature — follows this five-phase cycle.

---

### Phase 1 — Orient (session start)

Always check before doing anything:
1. `docs/04-execution/exec-plans/active/` — any in-progress plans?
2. `docs/04-execution/tech-debt.md` — relevant open items?
3. `docs/00-meta/AGENTS.md` — invariants and orientation

---

### Phase 2 — Plan

**Read before writing the plan:**
1. `docs/01-product/index.md`
2. `docs/02-architecture/ARCHITECTURE.md`
3. `docs/04-execution/tech-debt.md`
4. `docs/06-generated/endpoints.md`
5. `docs/06-generated/db-schema.md`
6. Relevant `docs/02-architecture/backend/*.md` for the area being changed
7. Relevant `docs/03-engineering/patterns/*.md` for the patterns being used
8. `docs/04-execution/exec-plans/templates/plan-template.md`

**Write the plan:**
- Save as `docs/04-execution/exec-plans/active/<plan-name>.md`
- Use the template — fill every section, do not skip Definition of Done

---

### Phase 3 — Execute

**Read before writing code:**
1. `docs/07-references/llm/backend-rules.txt`
2. `docs/07-references/llm/coding-standards.txt`
3. `docs/02-architecture/ARCHITECTURE.md` (invariants)
4. Load only the pattern docs for what is being built:
   - New domain feature → `docs/00-meta/AGENTS.md`
   - Service patterns → `docs/03-engineering/patterns/service-patterns.md`
   - Error handling → `docs/03-engineering/patterns/error-handling.md`
   - Async DB patterns → `docs/03-engineering/patterns/async-patterns.md`
   - Auth / permissions → `docs/02-architecture/backend/auth.md`
   - API layer → `docs/02-architecture/backend/api-layer.md`
   - Data layer → `docs/02-architecture/backend/data-layer.md`
   - Service layer → `docs/02-architecture/backend/service-layer.md`

**While implementing:**
- Tick off plan task checkboxes as each task completes
- Write tests alongside code — see `docs/03-engineering/testing/`

**Adding a new domain (checklist):**
- [ ] `app/schemas/<domain>.py` — Create, Update, Response Pydantic schemas
- [ ] `app/models/<domain>.py` — SQLAlchemy model
- [ ] Alembic migration for the new table
- [ ] `app/services/<domain>_service.py` — CRUD + business logic
- [ ] `app/api/v1/<domain>.py` — FastAPI router
- [ ] Wire router in `app/main.py`
- [ ] `tests/unit/test_<domain>_service.py` — service unit tests
- [ ] `tests/integration/test_<domain>_api.py` — endpoint integration tests
- [ ] `docs/06-generated/endpoints.md` — add new endpoints
- [ ] `docs/06-generated/db-schema.md` — add new table

---

### Phase 4 — Quality check

Before marking the plan complete, verify:
1. `pytest` — all unit and integration tests pass
2. `ruff check app tests` — no lint errors
3. `mypy app` — no type errors
4. Read `docs/05-quality/checklists/pr-checklist.md` and confirm every item

---

### Phase 5 — Update docs

After all code is working and quality checks pass, update docs in this order:

| What changed | Doc to update |
|---|---|
| New or changed endpoints | **Read** `docs/06-generated/endpoints.md`, verify every row matches `app/api/v1/*.py`, add/remove/update rows as needed |
| New or changed DB model | **Read** `docs/06-generated/db-schema.md`, verify every table/column matches `app/models/*.py`, add/remove/correct rows as needed |
| Every plan (always) | Add an entry to `docs/08-decisions/changelog.md` — record decisions made, deviations from the original approach, library choices, or trade-offs. Do this even if nothing surprising happened. |
| New tech debt incurred | Add to `docs/04-execution/tech-debt.md` |
| Tech debt resolved | Move item to Resolved in `docs/04-execution/tech-debt.md` |
| New feature added or changed | **Read** `docs/01-product/features/<NNN>-<feature>.md` if it exists and update it, or create it if the feature is new. One file per feature, named with a zero-padded 3-digit numeric prefix followed by the domain name (e.g. `001-auth.md`, `002-user-management.md`). Describe what the feature does, its API surface, and any constraints. Also **read** `docs/01-product/index.md` and update any lines that reference the feature (e.g. remove "pending" markers, fix technology descriptions). |
| New domain added | **Read** `docs/02-architecture/ARCHITECTURE.md` and update the Codemap, "Where is the thing that does X?" table, Key types section, and any other section that no longer matches reality |
| New domain, endpoint pattern, service, or data-layer change | **Read** the relevant `docs/02-architecture/backend/` sub-doc (`api-layer.md`, `service-layer.md`, `data-layer.md`, `auth.md`) and update if the documented patterns, inventories, examples, or migration history no longer match reality |
| Architectural invariant changed | Update the invariants section in `docs/02-architecture/ARCHITECTURE.md` |
| New pattern introduced | Update relevant `docs/03-engineering/patterns/` doc |
| Quality metrics changed | Update `docs/05-quality/QUALITY_SCORE.md` |

**Finally:** confirm every doc touched by this plan accurately describes the current implementation, then move the plan file from `active/` to `docs/04-execution/exec-plans/completed/`

---

### Writing tests only

1. `docs/03-engineering/testing/strategy.md`
2. `docs/03-engineering/testing/unit.md` (unit tests)
3. `docs/03-engineering/testing/integration.md` (integration tests)

### Reviewing / merging a PR

Read: `docs/05-quality/checklists/pr-checklist.md`

### Debugging / investigating

Read: `docs/02-architecture/ARCHITECTURE.md` (codemap + "Where is X?" table), then the relevant `app/` files directly.

- **Slow queries**: set `DEBUG=True` in `.env` to enable SQLAlchemy echo
- **Auth issues**: decode the JWT at `jwt.io`; check `SECRET_KEY` matches
- **Migration conflicts**: `alembic history` to see revision chain; `alembic current` to see applied revision
- **Async greenlet errors**: caused by lazy-loading in async context — add `lazy="selectin"` to the relationship or use explicit `joinedload()`

---

## Dev commands

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # edit DATABASE_URL, SECRET_KEY at minimum

# Database
alembic upgrade head
python scripts/seed.py                              # create initial admin (requires ADMIN_PASSWORD in .env)
alembic revision --autogenerate -m "description"   # create migration
alembic downgrade -1                                # rollback last
alembic history                                     # show revision chain
alembic current                                     # show applied revision

# Dev server
uvicorn app.main:app --reload --port 8000   # API docs at http://localhost:8000/docs

# Tests
pytest
pytest -x                                   # stop on first failure
pytest tests/unit/                          # unit tests only
pytest tests/integration/                   # integration tests only
pytest --cov=app --cov-report=term-missing  # coverage report

# Linting / type checking
ruff check app tests
ruff format app tests
mypy app

# Seed
python scripts/seed.py
```
