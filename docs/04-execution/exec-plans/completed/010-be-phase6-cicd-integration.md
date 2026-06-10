# Execution Plan: 010 — Backend Phase 6: CI/CD Integration

**Date**: 2026-03-24
**Author**: gabi
**Status**: Complete
**Priority**: MEDIUM
**Dependency**: 003-be-phase3-test-execution must be complete

---

## Goal

Add GitHub Actions CI/CD pipelines for the backend and expose CI-facing API endpoints: a webhook receiver, a JUnit XML bulk result upload endpoint, and an SVG status badge generator.

---

## Context

The frontend already has working CI/CD pipelines (`.github/workflows/ci.yml` and `cd.yml` in `web-testoria`). The backend needs equivalent pipelines following the same patterns:

- **CI**: lint + type-check + unit tests + integration tests (with real Postgres/Redis via Docker services)
- **CD**: build FastAPI Docker image → push to GHCR → deploy to EC2 via SSH → run Alembic migrations → health check

The API endpoints let CI pipelines (GitHub Actions, GitLab CI, Jenkins) push test results and display live pass/fail badges without using the web UI.

---

## Scope

### In scope
- `.github/workflows/ci.yml` — lint, type-check, unit + integration tests
- `.github/workflows/cd.yml` — build Docker image, push to GHCR, deploy to EC2
- `Dockerfile` — production image for the FastAPI app
- `app/services/ci_service.py` — webhook processing, JUnit XML parsing, badge SVG generation
- `app/api/v1/ci_integration.py` — `/ci/webhooks`, `/ci/results/bulk`, `/ci/runs/{id}/badge`
- `tests/integration/test_ci_api.py`

### Out of scope
- `docker-compose.prod.yml` — production compose (separate plan)
- Webhook signature verification (security hardening, later)
- TestNG / NUnit XML formats (JUnit only)
- Push notifications back to CI systems

---

## Technical approach

### CI workflow (`.github/workflows/ci.yml`)

Mirrors the frontend CI structure: `check` job first, then `integration` job that needs `check`.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  check:
    name: Lint, Type-check & Unit Tests
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements-dev.txt

      - name: Lint
        run: ruff check app tests

      - name: Type-check
        run: mypy app

      - name: Unit tests
        run: pytest tests/unit/ -q

  integration:
    name: Integration Tests
    runs-on: ubuntu-latest
    needs: check

    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: testoria_test
          POSTGRES_USER: testoria
          POSTGRES_PASSWORD: testoria
        ports:
          - 5433:5432
        options: >-
          --health-cmd "pg_isready -U testoria -d testoria_test"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10

      redis:
        image: redis:7-alpine
        ports:
          - 6380:6379

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements-dev.txt

      - name: Run migrations
        env:
          DATABASE_URL: postgresql+asyncpg://testoria:testoria@localhost:5433/testoria_test
        run: alembic upgrade head

      - name: Integration tests
        env:
          TEST_DATABASE_URL: postgresql+asyncpg://testoria:testoria@localhost:5433/testoria_test
          TEST_REDIS_URL: redis://localhost:6380/0
          SECRET_KEY: ci-test-secret-key
        run: pytest tests/integration/ -q
```

### CD workflow (`.github/workflows/cd.yml`)

Mirrors the frontend CD exactly: triggers after CI on main, builds Docker image, pushes to GHCR, deploys to EC2 via SSH, runs Alembic migrations before restarting the container.

```yaml
name: CD

on:
  workflow_run:
    workflows: [CI]
    types: [completed]
    branches: [main]
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    name: Build & Push Docker Image
    runs-on: ubuntu-latest
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success' }}

    permissions:
      contents: read
      packages: write

    outputs:
      image_tag: ${{ steps.meta.outputs.tags }}

    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract Docker metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=sha,prefix=sha-
            type=raw,value=latest

      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=registry,ref=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:buildcache
          cache-to: type=registry,ref=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:buildcache,mode=max

  deploy:
    name: Deploy to EC2
    runs-on: ubuntu-latest
    needs: build-and-push
    environment: production

    concurrency:
      group: deploy-production
      cancel-in-progress: false

    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ${{ secrets.EC2_USER }}
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            git config --global credential.helper store
            printf "https://x-access-token:%s@github.com\n" "$GH_PAT" > ~/.git-credentials
            chmod 600 ~/.git-credentials

            if [ -d ~/api-testoria/.git ]; then
              cd ~/api-testoria && git pull origin main
            else
              git clone https://github.com/${{ github.repository }}.git ~/api-testoria
            fi

            rm -f ~/.git-credentials
            git config --global --unset credential.helper || true

            echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

            docker pull ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest

            cd ~/api-testoria

            # Run migrations before restarting
            docker run --rm \
              --env-file .env \
              --network host \
              ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest \
              alembic upgrade head

            # Restart API container
            docker compose -f docker-compose.prod.yml up -d --no-deps api

            # Health check — wait up to 30s
            echo "Waiting for health check..."
            curl --fail --retry 10 --retry-delay 3 --retry-connrefused http://localhost:8000/api/v1/health

            docker image prune -f
        env:
          GH_PAT: ${{ secrets.GH_PAT }}
          GHCR_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GHCR_USER: ${{ github.actor }}
```

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/ci/webhooks` | Bearer (tester) | Receive CI webhook payload |
| POST | `/api/v1/ci/results/bulk` | Bearer (tester) | Bulk import JUnit XML → upsert results |
| GET | `/api/v1/ci/runs/{id}/badge` | None (public) | SVG pass-rate badge |

### JUnit XML bulk import

```python
@staticmethod
async def import_junit_xml(
    db: AsyncSession,
    test_run_id: int,
    xml_content: bytes,
    user_id: int,
) -> dict:
    root = ET.fromstring(xml_content)
    submitted = skipped = 0

    for testcase in root.findall(".//testcase"):
        name = f"{testcase.get('classname', '')}.{testcase.get('name', '')}"
        case = await db.scalar(select(TestCase).where(TestCase.title == name))
        if not case:
            skipped += 1
            continue

        failure = testcase.find("failure") or testcase.find("error")
        skip_el = testcase.find("skipped")
        if failure is not None:
            status, comment = "Failed", failure.get("message") or failure.text
        elif skip_el is not None:
            status, comment = "Skipped", "Test skipped"
        else:
            status, comment = "Passed", None

        execution_time = int(float(testcase.get("time", 0)))
        await TestResultService.submit(db, test_run_id, TestResultCreate(
            test_case_id=case.id,
            status=status,
            comment=comment,
            execution_time=execution_time,
        ), user_id)
        submitted += 1

    return {"submitted": submitted, "skipped": skipped}
```

### Badge SVG

Pass rate ≥ 90% → green (`#4c1`), ≥ 70% → yellow, < 70% → red.

### GitHub Actions secrets required

| Secret | Description |
|--------|-------------|
| `EC2_HOST` | EC2 instance public IP or hostname |
| `EC2_USER` | SSH username (e.g. `ubuntu`) |
| `EC2_SSH_KEY` | Private SSH key for EC2 |
| `GH_PAT` | GitHub PAT with `repo` scope (for git pull on EC2) |
| `LHCI_GITHUB_APP_TOKEN` | Not needed for backend |

---

## Tasks

### GitHub Actions — CI
- [x] Create `.github/workflows/ci.yml` with `check` + `integration` jobs

### GitHub Actions — CD
- [x] Create `.github/workflows/cd.yml` mirroring frontend pattern (build → push GHCR → deploy EC2 → migrate → health check)

### Dockerfile
- [x] Write `Dockerfile` for the FastAPI app (python:3.11-slim, uvicorn entrypoint)

### Service
- [x] Write `app/services/ci_service.py`:
  - `import_junit_xml(db, run_id, xml_bytes, user_id)`
  - `process_webhook(db, payload)`
  - `generate_badge(db, run_id) → str`

### Router
- [x] Write `app/api/v1/ci_integration.py` — 3 endpoints
- [x] Register router with prefix `/api/v1/ci` in `app/main.py`

### Tests
- [x] `tests/integration/test_ci_api.py`:
  - POST `/ci/results/bulk` with valid JUnit XML → results created, submitted count correct
  - POST `/ci/results/bulk` with malformed XML → 422
  - GET `/ci/runs/{id}/badge` → `Content-Type: image/svg+xml`, contains pass rate
  - GET `/ci/runs/{id}/badge` for 100% pass run → green badge
  - POST `/ci/results/bulk` without auth → 401

### Quality check
- [x] `pytest` passes
- [x] `ruff check app tests` clean
- [x] `mypy app` clean

### Docs
- [x] `docs/06-generated/endpoints.md` — verify CI/CD rows
- [x] `docs/01-product/features/009-ci-cd-integration.md` — create feature doc
- [x] `docs/08-decisions/changelog.md` — add entry
- [x] Move plan to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| JUnit XML `classname.name` doesn't match TestCase title | High | Return `skipped` count; document naming convention in CLI docs |
| EC2 deploy secrets not configured | Medium | Document required secrets in this plan |
| Alembic migration fails mid-deploy | Low | Run migration in separate `docker run` before restarting container — old container stays up if it fails |

---

## Definition of done

- [x] `pytest tests/unit/` and `pytest tests/integration/` both pass in GitHub Actions CI
- [x] Docker image builds and pushes to GHCR on merge to main
- [x] `POST /ci/results/bulk` accepts JUnit XML, creates results, returns `{ submitted, skipped }`
- [x] `GET /ci/runs/{id}/badge` returns valid SVG coloured by pass rate
- [x] `POST /ci/webhooks` accepts JSON payload and returns 200
- [x] Malformed XML → 422; unknown run → 404; no auth → 401
- [x] Integration tests pass
