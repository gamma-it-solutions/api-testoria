# ADR-002 — SQLAlchemy 2.0 async + asyncpg

**Date:** January 2026
**Status:** Accepted

---

## Context

The ORM and DB driver must be async-compatible to work with FastAPI's async request handlers.

Candidates:
- **SQLAlchemy 2.0 async** + asyncpg — mature ORM, proven at scale, async session support
- **Tortoise ORM** — Django-like async ORM, newer ecosystem
- **Databases** + raw SQL — minimal library, full control, verbose
- **SQLModel** — thin wrapper around SQLAlchemy + Pydantic, less flexible

---

## Decision

**SQLAlchemy 2.0 async API** with **asyncpg** as the PostgreSQL driver and **Alembic** for migrations.

---

## Rationale

- SQLAlchemy 2.0's `AsyncSession` is the industry standard for async Python ORM
- asyncpg is the fastest async PostgreSQL driver (binary protocol, no GIL overhead)
- Alembic is the de-facto migration tool for SQLAlchemy and handles schema evolution reliably
- SQLAlchemy's relationship system and `lazy="selectin"` work correctly in async context
- Tortoise ORM and SQLModel have smaller communities and fewer production references

---

## Consequences

- `create_async_engine` + `AsyncSessionLocal` + `AsyncSession` throughout
- `lazy="selectin"` on all relationships (not `lazy="dynamic"` — incompatible with async)
- All service methods are `async def` and accept `AsyncSession` as a parameter
- Migrations in `alembic/versions/` — never edit existing files, always `alembic revision --autogenerate`
- `expire_on_commit=False` on `AsyncSessionLocal` to prevent greenlet errors post-commit
