# Backend Engineering Guide

Practical guide for developing features in the Testoria backend.

---

## Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements-dev.txt

# Configure environment
cp .env.example .env   # edit DATABASE_URL, SECRET_KEY at minimum

# Run migrations
alembic upgrade head

# Start dev server
uvicorn app.main:app --reload --port 8000
```

API docs: `http://localhost:8000/docs`

---

## Common tasks

### Add an endpoint to an existing domain

1. Add Pydantic schema to `app/schemas/<domain>.py` if the request/response shape is new
2. Add the service method to `app/services/<domain>_service.py`
3. Add the route to `app/api/v1/<domain>.py`
4. Write an integration test in `tests/integration/test_<domain>_api.py`
5. Update `docs/06-generated/endpoints.md`

### Add a new DB column

```bash
# 1. Edit the model in app/models/<domain>.py
# 2. Generate migration
alembic revision --autogenerate -m "add column_name to table_name"
# 3. Review the generated file in alembic/versions/
# 4. Apply
alembic upgrade head
# 5. Update docs/06-generated/db-schema.md
```

### Add a Celery task

```python
# app/tasks/<domain>_tasks.py
from app.tasks.celery_app import celery_app

@celery_app.task
def generate_report_async(run_id: int) -> str:
    # ... sync code (Celery runs in its own process)
    return report_path
```

Trigger from a service:
```python
from app.tasks.report_tasks import generate_report_async
generate_report_async.delay(run_id)  # fire and forget
```

### Publish a real-time event (Phase 4)

```python
# In a service method
await realtime_service.publish(
    channel=f"testrun:{run_id}",
    event={"type": "test_result", "data": result_dict}
)
```

### Add a permission check

```python
# In a router, use the require_role dependency:
from app.core.permissions import require_role

@router.delete("/{id}", status_code=204)
async def delete_project(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("project_manager"))
):
    await ProjectService.delete(db, id)
```

---

## Debugging

- **Slow queries**: set `DEBUG=True` in `.env` to enable SQLAlchemy echo (logs all SQL)
- **Auth issues**: decode the JWT at `jwt.io` to inspect payload; check `SECRET_KEY` matches
- **Migration conflicts**: run `alembic history` to see revision chain; `alembic current` to see applied revision
- **Async errors (greenlet)**: usually caused by lazy-loading in async context — add `lazy="selectin"` to the relationship or use explicit `joinedload()`

---

## Scripts reference

| Command | Description |
|---------|-------------|
| `uvicorn app.main:app --reload` | Dev server with auto-reload |
| `pytest` | Run all tests |
| `pytest -x` | Stop on first failure |
| `pytest tests/unit/` | Unit tests only |
| `pytest tests/integration/` | Integration tests only |
| `pytest --cov=app --cov-report=term-missing` | Coverage report |
| `ruff check app tests` | Lint |
| `ruff format app tests` | Format |
| `mypy app` | Type check |
| `alembic upgrade head` | Apply all pending migrations |
| `alembic revision --autogenerate -m "..."` | Create migration from model changes |
| `alembic downgrade -1` | Rollback last migration |
| `alembic history` | Show migration chain |
| `alembic current` | Show applied revision |
| `python scripts/seed.py` | Create initial admin user (requires `ADMIN_PASSWORD` in `.env`; idempotent) |

---

## Adding a new domain (checklist)

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
- [ ] `docs/02-architecture/ARCHITECTURE.md` — update codemap if significant
