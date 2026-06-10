# api-testoria

FastAPI backend for the Testoria test management platform.

## Dev environment

Requirements: Docker Desktop with WSL2 integration enabled (Settings → Resources → WSL Integration).

### Start the dev stack

```bash
# Start PostgreSQL (port 5432) and Redis (port 6379)
docker-compose up -d

# Verify both services are healthy
docker-compose ps
```

### Start the test stack (separate ports, no data volume)

```bash
# Start postgres_test (port 5433) and redis_test (port 6380)
docker-compose -f docker-compose.test.yml up -d
```

### Apply migrations

```bash
cp .env.example .env   # edit SECRET_KEY at minimum
alembic upgrade head
```

> **Note:** `CORS_ORIGINS` in `.env` must be JSON array format — pydantic-settings v2 requires this for list fields:
> ```
> CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]
> ```
> `.env.example` already has the correct format.

### Run the API

```bash
uvicorn app.main:app --reload --port 8000
# Docs: http://localhost:8000/docs
```

### CI helper

`scripts/wait-for-db.sh` polls `pg_isready` until Postgres is ready:

```bash
./scripts/wait-for-db.sh localhost 5432 testoria testoria
```

Override retry count and sleep via `MAX_ATTEMPTS` and `SLEEP_SECONDS` env vars (defaults: 30 and 2).

### Port conflicts

If port 5432 is already in use, change the host port in `docker-compose.yml` and update `DATABASE_URL` in `.env` accordingly.