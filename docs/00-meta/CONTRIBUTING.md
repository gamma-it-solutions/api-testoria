# CONTRIBUTING — Backend Developer Guide

Getting started with the Testoria backend.

---

## Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Docker (for running dependencies locally)

---

## Local setup

### 1. Clone and create virtual environment

```bash
cd backend/
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
```

### 2. Start dependencies with Docker

```bash
docker compose up -d postgres redis
```

Or use the full stack:
```bash
docker compose up -d
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your local values:
#   DATABASE_URL=postgresql+asyncpg://testoria_user:password@localhost:5432/testoria_dev
#   REDIS_URL=redis://localhost:6379/0
#   SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
```

### 4. Run migrations

```bash
alembic upgrade head
```

### 5. Create the initial admin user

Set `ADMIN_PASSWORD` in `.env` (also set `ADMIN_USERNAME` and `ADMIN_EMAIL` if you want non-default values), then run:

```bash
python scripts/seed.py
```

This creates one admin user if no admin exists yet. Safe to re-run — skips if an admin is already present.

### 6. Start the dev server

```bash
uvicorn app.main:app --reload --port 8000
```

API available at `http://localhost:8000`
Swagger docs at `http://localhost:8000/docs`
Redoc at `http://localhost:8000/redoc`

---

## Development workflow

### Scripts reference

| Command | Description |
|---------|-------------|
| `uvicorn app.main:app --reload` | Dev server with auto-reload |
| `pytest` | Run all tests |
| `pytest -x` | Stop on first failure |
| `pytest --cov=app --cov-report=term-missing` | Tests with coverage |
| `pytest tests/unit/` | Unit tests only |
| `pytest tests/integration/` | Integration tests only |
| `ruff check app tests` | Lint |
| `ruff format app tests` | Format |
| `mypy app` | Type check |
| `alembic upgrade head` | Apply all migrations |
| `alembic revision --autogenerate -m "description"` | Create migration |
| `alembic downgrade -1` | Roll back last migration |

### Adding a feature

See `docs/00-meta/AGENTS.md` → "Adding a new endpoint" checklist.

### Creating a migration

```bash
# After changing a model in app/models/
alembic revision --autogenerate -m "add stack_trace to test_results"
# Review the generated file in alembic/versions/ before applying
alembic upgrade head
```

**Never edit an existing migration.** Always create a new revision.

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL async connection string |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection string |
| `SECRET_KEY` | Yes | — | JWT signing secret (32+ random bytes) |
| `ALGORITHM` | No | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `7` | Refresh token lifetime |
| `CORS_ORIGINS` | No | `["http://localhost:3000"]` | Allowed CORS origins |
| `UPLOAD_DIR` | No | `/app/uploads` | File upload directory |
| `MAX_UPLOAD_SIZE` | No | `10485760` | Max file size in bytes (10 MB) |
| `DEFAULT_PAGE_SIZE` | No | `50` | Default pagination page size |
| `MAX_PAGE_SIZE` | No | `100` | Maximum pagination page size |
| `DEBUG` | No | `False` | Enables SQLAlchemy echo |
| `CENTRIFUGO_URL` | Phase 4 | `http://centrifugo:8000` | Centrifugo server URL |
| `CENTRIFUGO_API_KEY` | Phase 4 | — | Centrifugo API key |
| `CENTRIFUGO_TOKEN_SECRET` | Phase 4 | — | Centrifugo JWT signing secret |

---

## Code style

- **Formatter**: `ruff format` (Black-compatible, 88 char line length)
- **Linter**: `ruff check` (replaces flake8, isort)
- **Type checker**: `mypy` in strict mode
- **Docstrings**: Google style, on all public service methods
- **Imports**: `ruff` handles sorting automatically

Pre-commit hooks (if configured):
```bash
pip install pre-commit
pre-commit install
```

---

## PR process

1. Branch from `main`: `git checkout -b feat/your-feature`
2. Implement with tests
3. Run quality checks: `pytest && ruff check app tests && mypy app`
4. Review `docs/05-quality/checklists/pr-checklist.md`
5. Open PR against `main`
6. CI must pass before merge
