# Testoria — Backend API Overview

---

## What this service does

The Testoria backend is a **FastAPI REST API** that powers the Testoria test management platform. It provides:

- Authentication and role-based access control
- Full CRUD for projects, test suites, test cases, test runs, and test results
- File attachment upload and download
- Result history (audit trail of status changes)
- Import/export for test cases (CSV, Excel) and bulk result submission (JUnit XML)
- Metrics and reporting endpoints consumed by the dashboard
- CI/CD integration hooks for automated result submission
- Defect tracking integrations (Jira, GitHub, GitLab)
- Real-time updates via Centrifugo WebSocket server
- Soft delete + restore for projects, suites, cases, runs, results, milestones, users

---

## Who calls this API?

| Caller | How |
|--------|-----|
| **Testoria Web Frontend** (Vue 3 SPA) | REST/JSON over HTTPS — authenticated with JWT |
| **Testoria CLI** (Python tool) | REST/JSON — used in CI/CD pipelines |
| **CI/CD systems** (GitHub Actions, GitLab CI, Jenkins) | Webhook + bulk result submission endpoints |
| **Centrifugo** (WebSocket server) | Backend publishes events via HTTP API; Centrifugo uses Redis engine and broadcasts to frontend via WebSocket |

---

## Core domain hierarchy

```
Project
  ├── TestSuite (hierarchical, n levels deep)
  │     └── TestCase (reusable spec with steps)
  └── TestRun (planned execution for a specific build/environment)
        └── TestResult (one execution record per TestCase per TestRun)
              ├── ResultHistory (audit trail of status changes)
              ├── Attachment (uploaded files/screenshots)
              └── Defect[] (linked bugs in external trackers)
```

The key distinction: **TestCase** is a specification; **TestResult** is one execution of that spec in the context of a TestRun.

---

## API surface summary

| Domain | Prefix | Key operations |
|--------|--------|----------------|
| Auth | `/api/v1/auth` | login, refresh, logout, me, forgot/reset-password (no public register) |
| Users | `/api/v1/users` | CRUD by Lead+Admin (invite-only, no password field; Lead capped at Lead), role management |
| Projects | `/api/v1/projects` | CRUD, stats, dashboard |
| Test Suites | `/api/v1/projects/:id/test-suites` | CRUD, tree |
| Test Cases | `/api/v1/projects/:id/test-cases` | CRUD, import, export |
| Test Runs | `/api/v1/projects/:id/test-runs` | CRUD, cases list |
| Test Results | `/api/v1/test-runs/:id/results` | submit, update, history, attachments |
| Reports | `/api/v1/projects/:id/dashboard`, `/test-runs/:id/report`, `/reports/custom` | dashboard, run reports (JSON/PDF/Excel), time-series metrics, custom filtered reports |
| CI/CD | `/api/v1/ci` | webhooks, bulk submit, badge |
| Defects | `/api/v1/defects` | create in Jira/GitHub/GitLab |
| WebSocket tokens | `/api/v1/websocket` | connection + subscription JWTs for Centrifugo |

Full endpoint reference: `docs/06-generated/endpoints.md`

---

## Technology stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI (async) |
| Runtime | Python 3.11+ |
| ASGI server | Uvicorn |
| Validation | Pydantic v2 |
| ORM | SQLAlchemy 2.0 async |
| Migrations | Alembic |
| Database | PostgreSQL 15+ (asyncpg driver) |
| Cache | Redis (redis-py) |
| Task queue | Celery + Redis broker |
| Auth | python-jose (JWT) + bcrypt |
| Real-time | Centrifugo v5 (HTTP API publish, Redis engine) |
| File uploads | python-multipart |
| Export / Reports | ReportLab (PDF), openpyxl (Excel) |
| Testing | pytest + pytest-asyncio + httpx |

---

## Build and run

```bash
# Development
uvicorn app.main:app --reload --port 8000

# Production (Docker)
docker compose -f docker-compose.prod.yml up -d
```

Health check: `GET /api/v1/health` → `{"status": "healthy"}`
