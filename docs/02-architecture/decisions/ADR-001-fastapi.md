# ADR-001 — FastAPI as the backend framework

**Date:** January 2026
**Status:** Accepted

---

## Context

Testoria needs a Python backend with a REST API. The main candidates were:

- **FastAPI** — modern async framework, auto-generates OpenAPI, Pydantic-native
- **Django REST Framework** — mature, batteries-included, sync-first
- **Flask** — minimal, sync-first, requires more manual wiring

---

## Decision

**FastAPI** with Uvicorn (ASGI).

---

## Rationale

| Criterion | FastAPI | DRF | Flask |
|-----------|---------|-----|-------|
| Async-native | Yes | No (ASGI add-on) | No (ASGI add-on) |
| Auto OpenAPI docs | Yes (built-in) | Partial (drf-spectacular) | No (flask-restx) |
| Request/response validation | Pydantic (built-in) | Serializers (manual) | Manual |
| Type safety | Full (Python type hints) | Partial | Minimal |
| Performance | High (async I/O) | Lower (sync) | Lower (sync) |
| Learning curve | Low | Medium | Low |

FastAPI's async-first design is the key factor: PostgreSQL (asyncpg) and Redis are both async, and handling concurrent WebSocket + REST traffic without threads is important for real-time result submission.

Auto-generated Swagger UI at `/docs` is a significant developer experience benefit during frontend/CLI integration.

---

## Consequences

- All DB queries must be async (SQLAlchemy 2.0 async)
- Pydantic v2 is the validation and serialization standard — no custom serializer classes
- Uvicorn is the production ASGI server (wrapped in Docker)
- pytest-asyncio required for testing async code
