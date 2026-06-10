# Execution Plan: [Feature Name]

**Date**: YYYY-MM-DD
**Author**:
**Status**: Draft | In Progress | Complete

> **Lifecycle**: Save this file as `docs/04-execution/exec-plans/active/<plan-name>.md` while in progress.
> Move to `docs/04-execution/exec-plans/completed/` once all Definition of Done items are checked off.

---

## Goal

One sentence: what does this plan deliver?

---

## Context

Why is this being built? What problem does it solve? Link to any relevant issues or decisions.

---

## Scope

### In scope
- Item 1
- Item 2

### Out of scope
- Item A (deferred to later)

---

## Technical approach

### Changes required

| Layer | File(s) | What changes |
|-------|---------|--------------|
| schemas | `app/schemas/<domain>.py` | New/updated Pydantic schemas |
| models | `app/models/<domain>.py` | New/updated SQLAlchemy models |
| migration | `alembic/versions/` | New Alembic revision |
| services | `app/services/<domain>_service.py` | New service methods |
| router | `app/api/v1/<domain>.py` | New endpoints |
| main | `app/main.py` | Router registration (if new file) |
| tests | `tests/unit/`, `tests/integration/` | New tests |

### Key decisions

- Decision 1 and rationale
- Decision 2 and rationale

---

## Tasks

### Implementation
- [ ] Define Pydantic schemas in `app/schemas/<domain>.py`
- [ ] Add/update SQLAlchemy model in `app/models/<domain>.py`
- [ ] Create Alembic migration (`alembic revision --autogenerate -m "..."`)
- [ ] Review and apply migration (`alembic upgrade head`)
- [ ] Implement service method(s) in `app/services/<domain>_service.py`
- [ ] Add route(s) in `app/api/v1/<domain>.py`
- [ ] Wire router in `app/main.py` (if new router file)
- [ ] Write unit tests for service
- [ ] Write integration tests for endpoint(s)

### Quality check
- [ ] `pytest` — all tests pass
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors
- [ ] `docs/05-quality/checklists/pr-checklist.md` reviewed

### Docs update
- [ ] `docs/06-generated/endpoints.md` updated (if endpoints added/changed)
- [ ] `docs/06-generated/db-schema.md` updated (if schema changed)
- [ ] `docs/02-architecture/ARCHITECTURE.md` updated (if codemap changed)
- [ ] `docs/08-decisions/changelog.md` updated (if architectural decision made)
- [ ] `docs/04-execution/tech-debt.md` updated (if debt added or resolved)
- [ ] `docs/05-quality/QUALITY_SCORE.md` updated (if quality metrics changed)
- [ ] This plan moved from `active/` to `completed/`

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| | | |

---

## Definition of done

- [ ] All new endpoints return correct status codes and response shapes
- [ ] Auth and role enforcement tested
- [ ] Unit test coverage ≥ 85% for new service code
- [ ] Integration tests cover happy path + 401/403/404
- [ ] Migration applies cleanly and is reversible
- [ ] Docs updated
