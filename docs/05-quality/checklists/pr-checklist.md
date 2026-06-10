# PR Checklist — Backend

Review this before opening or merging a pull request.

---

## Code quality

- [ ] `pytest` passes — all unit and integration tests green
- [ ] `ruff check app tests` — no lint errors
- [ ] `mypy app` — no type errors
- [ ] No `print()` statements in committed code (use logging)
- [ ] No hardcoded secrets, passwords, or connection strings
- [ ] No raw SQL strings — all queries use SQLAlchemy ORM

## Architecture

- [ ] No business logic in router functions (only: validate input, call service, return schema)
- [ ] No direct DB queries in routers — always via service
- [ ] All new protected routes use `Depends(get_current_user)` or `Depends(require_role(...))`
- [ ] Pydantic schemas used for all request/response types — no raw dicts returned from routers
- [ ] `hashed_password` not included in any response schema
- [ ] Config values come from `app/config.py` (Settings) — no hardcoded values

## Database

- [ ] If model changed: new Alembic migration created (`alembic revision --autogenerate`)
- [ ] Migration reviewed manually — autogenerate can miss renamed columns, constraints
- [ ] Migration is reversible (`downgrade()` implemented)
- [ ] `alembic upgrade head` applies cleanly on a fresh DB
- [ ] No existing migration file edited

## Python / FastAPI

- [ ] All DB functions are `async def` and awaited
- [ ] `lazy="selectin"` (or explicit `joinedload()`) on all relationship accesses in async context
- [ ] `await db.flush()` + `await db.refresh(obj)` after insert when ID is needed immediately
- [ ] `expire_on_commit=False` on `AsyncSessionLocal` — do not change this setting

## Testing

- [ ] New service methods have unit tests
- [ ] New endpoints have integration tests covering: happy path, 401 (no auth), 404 (not found)
- [ ] Role-protected endpoints tested with a user of the wrong role (expect 403)
- [ ] Test coverage ≥ 85% for new code

## Documentation

- [ ] If endpoints added/changed: `docs/06-generated/endpoints.md` updated
- [ ] If DB schema changed: `docs/06-generated/db-schema.md` updated
- [ ] If architectural decision made: `docs/08-decisions/changelog.md` entry added
- [ ] If new pattern introduced: relevant `docs/03-engineering/patterns/` doc updated
- [ ] If quality metrics changed: `docs/05-quality/QUALITY_SCORE.md` updated
- [ ] If tech debt resolved: moved to Resolved in `docs/04-execution/tech-debt.md`
- [ ] Execution plan moved from `active/` to `docs/04-execution/exec-plans/completed/`
