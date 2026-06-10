# Service Patterns

Conventions for implementing service classes in Testoria.

---

## Standard service structure

```python
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.project import Project
from app.schemas.project import ProjectCreate, ProjectUpdate

class ProjectService:
    """Business logic for project management."""

    @staticmethod
    async def list(
        db: AsyncSession,
        page: int = 1,
        page_size: int = 50,
        include_archived: bool = False,
    ) -> tuple[list[Project], int]:
        """Return (items, total) for pagination."""
        q = select(Project)
        if not include_archived:
            q = q.where(Project.is_archived == False)

        total = await db.scalar(select(func.count()).select_from(q.subquery()))
        q = q.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(q)
        return result.scalars().all(), total or 0

    @staticmethod
    async def get_by_id(db: AsyncSession, project_id: int) -> Project | None:
        return await db.get(Project, project_id)

    @staticmethod
    async def create(db: AsyncSession, data: ProjectCreate, user_id: int) -> Project:
        project = Project(**data.model_dump(), created_by=user_id)
        db.add(project)
        await db.flush()
        await db.refresh(project)
        return project

    @staticmethod
    async def update(db: AsyncSession, project: Project, data: ProjectUpdate) -> Project:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await db.flush()
        await db.refresh(project)
        return project

    @staticmethod
    async def delete(db: AsyncSession, project: Project) -> None:
        await db.delete(project)
```

---

## Pagination pattern

Services return `(items, total)`. Routers wrap with `paginate()`:

```python
# app/utils/pagination.py
def paginate(items: list, total: int, page: int, page_size: int) -> dict:
    total_pages = (total + page_size - 1) // page_size
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
```

```python
# In router
items, total = await ProjectService.list(db, page=page, page_size=page_size)
return paginate(items, total, page, page_size)
```

---

## Upsert pattern (TestResult)

Test results use upsert — if a result already exists for (run_id, case_id), update it; otherwise create it:

```python
@staticmethod
async def upsert(db, run_id: int, data: TestResultCreate, user_id: int) -> TestResult:
    existing_q = select(TestResult).where(
        TestResult.test_run_id == run_id,
        TestResult.test_case_id == data.test_case_id,
    )
    result = await db.execute(existing_q)
    obj = result.scalar_one_or_none()

    if obj:
        for field, val in data.model_dump(exclude_unset=True).items():
            if field not in ("test_run_id", "test_case_id"):
                setattr(obj, field, val)
        obj.tested_by = user_id
    else:
        obj = TestResult(**data.model_dump(), test_run_id=run_id, tested_by=user_id)
        db.add(obj)

    await db.flush()
    await db.refresh(obj)
    return obj
```

---

## Filtering pattern

For list endpoints with multiple optional filters, build the query conditionally:

```python
@staticmethod
async def list_test_cases(
    db: AsyncSession,
    project_id: int,
    suite_id: int | None = None,
    priority: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[TestCase], int]:
    q = (
        select(TestCase)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(TestSuite.project_id == project_id)
    )
    if suite_id is not None:
        q = q.where(TestCase.suite_id == suite_id)
    if priority:
        q = q.where(TestCase.priority == priority)
    if search:
        q = q.where(TestCase.title.ilike(f"%{search}%"))

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return result.scalars().all(), total or 0
```

---

## External API calls (Defect tracking)

Services that call external APIs (Jira, GitHub) use `httpx.AsyncClient`:

```python
class DefectService:
    @staticmethod
    async def create_jira_issue(config: JiraConfig, summary: str, description: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config.base_url}/rest/api/3/issue",
                auth=(config.username, config.api_token),
                json={"fields": {"summary": summary, "description": description, ...}},
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
```

Always set a timeout. Let HTTP errors propagate — the router will catch and return 502/503.

---

## Soft delete pattern

Services that own a soft-deletable entity must follow the same shape so that `not_deleted()` filtering, cascade soft-delete, and restore all compose predictably. Using `project_service` as the reference:

```python
from sqlalchemy import func, select, update

from app.models.mixins import not_deleted
from app.models.project import Project
from app.models.test_suite import TestSuite


async def get_project(
    db: AsyncSession, project_id: int, allow_deleted: bool = False
) -> Project:
    query = select(Project).where(Project.id == project_id)
    if not allow_deleted:
        query = query.where(not_deleted(Project))
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


async def list_projects(
    db: AsyncSession, include_deleted: bool = False, ...
) -> tuple[list[Project], int]:
    query = select(Project)
    if not include_deleted:
        query = query.where(not_deleted(Project))
    ...


async def delete_project(db: AsyncSession, project_id: int, user_id: int | None) -> None:
    project = await get_project(db, project_id)
    now = func.now()
    project.deleted_at = now

    # Cascade soft-delete: explicit bulk UPDATE, not db.delete() + ORM cascade
    suite_ids = [r[0] for r in (await db.execute(
        select(TestSuite.id).where(
            TestSuite.project_id == project_id, not_deleted(TestSuite)
        )
    )).all()]
    if suite_ids:
        await db.execute(
            update(TestSuite)
            .where(TestSuite.id.in_(suite_ids), not_deleted(TestSuite))
            .values(deleted_at=now)
        )
        # ... cascade further to test_cases

    await audit_service.log_action(db, user_id, "DELETE", "Project", project_id)
    await db.flush()


async def restore_project(db: AsyncSession, project_id: int, user_id: int | None) -> Project:
    project = await get_project(db, project_id, allow_deleted=True)
    if project.deleted_at is None:
        raise BadRequestError(f"Project {project_id} is not deleted")
    project.deleted_at = None
    await db.flush()
    await db.refresh(project)
    await audit_service.log_action(db, user_id, "RESTORE", "Project", project.id)
    return project
```

**Rules to keep consistent across services:**

- Every `get_*` / `list_*` / helper on a soft-deletable model filters `not_deleted(Model)` by default.
- Every `get_*` for a model that has a restore flow takes `allow_deleted: bool = False`.
- Every `list_*` exposed via a router takes `include_deleted: bool = False`.
- `delete_*` sets `deleted_at = func.now()` and cascade-soft-deletes owned children via bulk `UPDATE`. Never rely on ORM-level `cascade="all, delete-orphan"` + DB `ON DELETE CASCADE` — both hard-delete.
- `restore_*` guards against restoring a child whose parent is still deleted (`BadRequestError` → HTTP 400).
- Both delete and restore emit audit log entries (`DELETE` / `RESTORE`).
