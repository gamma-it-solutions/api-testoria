# Data Layer

SQLAlchemy async patterns and Alembic migrations in Testoria.

---

## Engine and session setup

`app/database.py` creates the async engine and session factory:

```python
engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False,
)
```

`expire_on_commit=False` prevents lazy-load errors after a commit — attributes remain accessible without re-querying.

The `get_db` dependency in `app/database.py` yields a session per request and commits/rollbacks automatically:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

---

## Model base class

All models inherit from `Base` (defined in `app/database.py`):

```python
class Base(DeclarativeBase):
    pass
```

Timestamps use `server_default=func.now()` for consistency regardless of app clock. Models use SQLAlchemy 2.0 `Mapped` annotations:

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), nullable=False
)
updated_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
)
```

---

## Async query patterns

Always `await` queries. Never use synchronous SQLAlchemy calls.

```python
# Fetch single row
result = await db.execute(select(User).where(User.id == user_id))
user = result.scalar_one_or_none()

# Fetch all rows
result = await db.execute(select(Project).where(Project.is_archived.is_(False)))
projects = list(result.scalars().all())

# Count via subquery
count_result = await db.execute(select(func.count()).select_from(query.subquery()))
total: int = count_result.scalar_one()

# Insert
obj = ModelClass(**data)
db.add(obj)
await db.flush()      # makes obj.id available
await db.refresh(obj) # reload from DB (gets server_default values + relationships)

# Update
obj.status = "completed"
await db.flush()
await db.refresh(obj)  # IMPORTANT: reload updated_at and other onupdate fields

# Delete
await db.delete(obj)
await db.flush()
```

**Important:** Always `await db.refresh(obj)` after flush before returning objects for Pydantic serialization. This prevents `MissingGreenlet` errors from expired attributes (e.g. `updated_at` with `onupdate`).

---

## Relationships

Relationships use `relationship()` with string-based model references and `TYPE_CHECKING` for type hints:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.test_suite import TestSuite

class Project(Base):
    test_suites: Mapped[list[TestSuite]] = relationship(
        "TestSuite", back_populates="project",
        cascade="all, delete-orphan", passive_deletes=True,
    )
```

Use `lazy="selectin"` only for relationships that are always needed (e.g. `TestCase.tags`, `TestRun.test_cases`). For others, use explicit `selectinload()` or `joinedload()` in queries.

### Many-to-many with legacy fallback (TestRun → TestCase)

`test_run_test_cases` is a many-to-many association table for explicit case selection on a run. Reads use a fallback pattern: if the association table has rows for a given run, those are the scoped cases; if empty, the run falls back to the legacy `suite_id` scoping (all cases under the suite). This avoids needing a backfill migration for existing runs.

Self-referential (TestSuite hierarchy):

```python
parent_suite: Mapped[TestSuite | None] = relationship(
    "TestSuite", back_populates="child_suites",
    remote_side=[id], foreign_keys=[parent_suite_id],
)
child_suites: Mapped[list[TestSuite]] = relationship(
    "TestSuite", back_populates="parent_suite",
    foreign_keys=[parent_suite_id], passive_deletes=True,
)
```

---

## JSON fields

Test case steps, test run config, and result defects are stored as `JSON` (maps to JSONB on PostgreSQL, JSON on SQLite for tests):

```python
steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
defects: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
```

`email_outbox.context` (plan 048) makes the JSONB-on-Postgres choice explicit via
a dialect variant, so the column is real `JSONB` in production but still `JSON`
for the SQLite test runner:

```python
context: Mapped[dict[str, Any]] = mapped_column(
    JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
)
```

Pydantic schemas deserialize these automatically when `model_config = ConfigDict(from_attributes=True)`.

---

## Alembic migrations

### Creating a migration

```bash
# After changing app/models/, auto-generate a migration:
alembic revision --autogenerate -m "add stack_trace to test_results"
# Review alembic/versions/<hash>_*.py before applying
alembic upgrade head
```

### Rules

- **Never edit an existing migration file** — it may already be applied to production
- Always review autogenerated migrations before applying
- Migrations must be reversible — implement both `upgrade()` and `downgrade()`
- All models must be imported in `alembic/env.py` for autogenerate to detect them

### Migration history

| Revision | Description |
|----------|-------------|
| `8c23843e1a84` | Initial schema: users table |
| `f7a3b2c1d9e0` | Rename role slugs (viewer→read_only, project_manager→lead), add CHECK constraint |
| `620e48c40917` | Add projects, test_suites, test_cases, tags, test_case_tags |
| `7e3155df2bc6` | Add milestones, test_runs, test_results, result_attachments, result_history |
| `b5c6d7e8f9a0` | Add audit_logs |
| `d04cd83a87fd` | Add automation_id column + index to test_cases |
| `11cd61046802` | Add test_run_test_cases association table |
| `b368c6900009` | Add step_results JSON column to test_results |
| `a1b2c3d4e5f6` | Add soft-delete (`deleted_at`) to core entities and tighten FK ondelete |
| `b4ea679b9619` | Merge soft-delete and step_results heads |
| `c5d7e9f1a2b3` | Rewrite `test_results.status` from `skipped` to `no_run` |
| `d8e9f0a1b2c4` | Add `cases_mode` column to test_runs |
| `e9f0a1b2c3d5` | Add `display_order` to test_suites |
| `a4f9c1d27e53` | Rewrite `test_runs.status` from `in_progress` to `active` (plan 039) |
| `c7f1a2b3d4e8` | Rename `result_attachments.file_path` to `object_key`; add `storage_backend` (plan 042) |
| `f0a1b2c3d4e5` | Add `display_order` to test_cases (plan 046 / TES-69) |
| `a1c2e3f40576` | Add `email_outbox` table (durable email queue; plan 048) |
| `c3d4e5f60789` | Add `api_keys` table (CI/CLI credentials; plan 050) |

---

## Database indexes

Indexes are created via `index=True` on `mapped_column()`. Key indexes:

| Table | Column(s) | Purpose |
|-------|-----------|---------|
| test_cases | suite_id | List cases by suite |
| test_cases | title | Search by title |
| test_suites | project_id | List suites by project |
| test_suites | parent_suite_id | Tree traversal |
| test_runs | project_id | List runs by project |
| test_results | test_run_id | List results by run |
| test_results | test_case_id | Look up result by case |
| result_history | test_result_id | History by result |
| result_attachments | test_result_id | Attachments by result |
| milestones | project_id | List milestones by project |
| projects / test_suites / test_cases / test_runs / test_results / milestones / users | deleted_at | Soft-delete filter (`WHERE deleted_at IS NULL`) |

---

## Soft delete

Domain entities use `SoftDeleteMixin` (`app/models/mixins.py`) instead of hard deletes. Applied to: `Project`, `TestSuite`, `TestCase`, `TestRun`, `TestResult`, `Milestone`, `User`.

```python
class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


def not_deleted(model: type[SoftDeleteMixin]) -> ColumnElement[bool]:
    return model.deleted_at.is_(None)
```

**Query rule**: every `list_*` / `get_*` query on a soft-deletable model adds `.where(not_deleted(Model))` unless the caller passes `include_deleted=True` (list APIs) or `allow_deleted=True` (internal get helpers used by restore logic).

**FK ondelete**: FKs on soft-deletable children were tightened from `CASCADE` to `RESTRICT` (or `SET NULL` for `test_suites.parent_suite_id`) so that nothing in the DB can silently hard-cascade past the service layer. Cascade soft-delete is done explicitly in services via `UPDATE` statements — see `project_service.delete_project`, `test_suite_service.delete_suite`, `test_run_service.delete_run`.

**Tables intentionally excluded**: `tags`, `test_case_tags`, `result_attachments`, `result_history`, `audit_logs` — lightweight join/append-only/system tables. `result_attachments` and `result_history` still cascade-hard on their parent (but the parent is soft-deleted, so this never fires).
