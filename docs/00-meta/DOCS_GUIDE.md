# DOCS GUIDE — How the backend documentation is organized

---

## Directory structure

```
api/docs/
├── 00-meta/          Guides about the docs and repo itself
│   ├── AGENTS.md     — How LLMs/agents should work here
│   ├── CONTRIBUTING.md — Developer onboarding and workflow
│   └── DOCS_GUIDE.md — This file
│
├── 01-product/       Product-level context (backend perspective)
│   ├── index.md      — What the API does, who calls it, capability overview
│   └── features/     — One file per feature domain (001-auth.md, 002-user-management.md, ...)
│
├── 02-architecture/  Technical architecture
│   ├── ARCHITECTURE.md — Authoritative codemap + layer rules (read this first)
│   ├── backend/      — Deep-dives into specific backend topics
│   │   ├── api-layer.md     — FastAPI routers, request/response lifecycle
│   │   ├── service-layer.md — Business logic patterns, service classes
│   │   ├── data-layer.md    — SQLAlchemy async, Alembic migrations
│   │   └── auth.md          — JWT, RBAC, dependency injection
│   └── decisions/    — Architecture Decision Records (ADRs)
│       ├── ADR-001-fastapi.md
│       └── ADR-002-sqlalchemy-async.md
│
├── 03-engineering/   Implementation guides and patterns
│   ├── BACKEND.md    — Setup, environment, running locally, common tasks
│   ├── operations/   — Production operations runbooks
│   │   └── db-backups.md — PostgreSQL backup/restore (cron + S3) runbook
│   ├── patterns/     — How specific patterns are implemented
│   │   ├── service-patterns.md  — Service class structure and conventions
│   │   ├── error-handling.md    — Exception hierarchy, HTTP error mapping
│   │   └── async-patterns.md    — Async SQLAlchemy, background tasks, Celery
│   └── testing/      — Testing approach and examples
│       ├── strategy.md
│       ├── unit.md
│       └── integration.md
│
├── 04-execution/     Active project state
│   ├── tech-debt.md  — Known issues and deferred improvements
│   └── exec-plans/   — Feature execution plans
│       ├── templates/   — plan-template.md (copy from here)
│       ├── active/      — Plans currently in progress
│       └── completed/   — Finished plans
│
├── 05-quality/       Quality standards and checklists
│   ├── QUALITY_SCORE.md
│   ├── SECURITY.md
│   └── checklists/
│       └── pr-checklist.md
│
├── 06-generated/     Manually-synced reference docs
│   ├── endpoints.md  — All API endpoints (sync with app/api/v1/*.py)
│   └── db-schema.md  — Authoritative database schema
│
├── 07-references/    Quick-reference material
│   └── llm/          — Context files for LLM-assisted development
│       ├── backend-rules.txt     — Hard rules for code generation
│       └── coding-standards.txt  — Python/FastAPI standards
│
└── 08-decisions/     Decision log
    └── changelog.md  — Record of significant architectural decisions
```

---

## What to read first

**New developer**: `CONTRIBUTING.md` → `ARCHITECTURE.md` → `03-engineering/BACKEND.md`

**LLM agent**: `AGENTS.md` → `docs/07-references/llm/` (both files) → `ARCHITECTURE.md`

**Adding a new endpoint**: `AGENTS.md` → `02-architecture/backend/api-layer.md` → `03-engineering/patterns/service-patterns.md`

**Debugging a bug**: `ARCHITECTURE.md` (codemap) → relevant `02-architecture/backend/` doc

**Operating production**: `03-engineering/operations/` — runbooks (e.g. `db-backups.md` for DB backup/restore)

---

## Keeping docs up to date

| Trigger | Doc to update |
|---------|---------------|
| New endpoint added or changed | `docs/06-generated/endpoints.md` |
| DB schema changed (migration) | `docs/06-generated/db-schema.md` |
| New service or model added | `docs/02-architecture/ARCHITECTURE.md` (codemap) |
| Architectural decision made | `docs/08-decisions/changelog.md` |
| New tech debt | `docs/04-execution/tech-debt.md` (Active section) |
| Tech debt resolved | `docs/04-execution/tech-debt.md` (move to Resolved) |
| New pattern established | Relevant `docs/03-engineering/patterns/` doc |
| Quality metric changed | `docs/05-quality/QUALITY_SCORE.md` |
| Plan finished | Move from `exec-plans/active/` → `exec-plans/completed/` |

**Invariant rules:**
- ADRs in `docs/02-architecture/decisions/` are append-only — never edit a closed ADR.
- LLM reference files in `docs/07-references/llm/` must stay in sync with actual code patterns.
- `docs/06-generated/` files are manually maintained — not auto-generated.

---

## Document format conventions

- All markdown files use ATX headings (`#`, `##`, `###`).
- Code blocks specify language (`python`, `sql`, `bash`, `yaml`, etc.).
- Tables preferred over bullet lists for structured comparisons.
- Keep files focused — one clear topic per file, cross-link rather than duplicate.
