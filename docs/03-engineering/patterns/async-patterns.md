# Async Patterns

Async Python patterns used in the Testoria backend.

---

## Core rule

Every function that touches the database or external I/O must be `async def` and must be awaited. Never call synchronous I/O in an async context — it blocks the event loop.

---

## Async SQLAlchemy

```python
# Always await queries
result = await db.execute(select(User).where(User.id == user_id))
user = result.scalar_one_or_none()

# Await get() for PK lookup
project = await db.get(Project, project_id)

# Await flush/refresh — never commit in services
await db.flush()
await db.refresh(obj)
```

See `docs/02-architecture/backend/data-layer.md` for full patterns.

---

## Async context managers

Use `async with` for connections and file I/O:

```python
# External HTTP calls
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.post(url, json=payload)
    response.raise_for_status()
    return response.json()

# File writing (use aiofiles for truly non-blocking I/O)
import aiofiles
async with aiofiles.open(file_path, "wb") as f:
    await f.write(content)
```

---

## Background tasks vs Celery

**FastAPI `BackgroundTasks`** — for lightweight, fire-and-forget work within the same process:

```python
@router.post("/", response_model=TestResultResponse)
async def submit_result(
    result: TestResultCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    obj = await TestResultService.submit(db, result, user.id)
    background_tasks.add_task(send_notification_email, user.email, obj.id)
    return obj
```

Use for: audit log writes, lightweight notifications.

**Celery tasks** — for long-running, retry-able, or resource-intensive work:

```python
# In a router
from app.tasks.report_tasks import generate_report_async
generate_report_async.delay(run_id=run_id)
return {"message": "Report generation started", "run_id": run_id}
```

Use for: PDF/Excel report generation, bulk imports (large files), email with retries.

---

## Avoiding async pitfalls

### Do not lazy-load relationships in async context

```python
# WRONG — will raise MissingGreenlet error
user = await db.get(User, user_id)
print(user.projects)  # lazy load triggers sync query

# RIGHT — use selectin loading or explicit join
result = await db.execute(
    select(User).options(selectinload(User.projects)).where(User.id == user_id)
)
user = result.scalar_one()
```

Or declare on the model:
```python
projects = relationship("Project", back_populates="creator", lazy="selectin")
```

### Do not run sync code in async handlers

```python
# WRONG — blocks the event loop
import time
time.sleep(1)

# RIGHT — use asyncio.sleep for delays (test/mock use only)
import asyncio
await asyncio.sleep(1)

# RIGHT — run blocking I/O in a thread pool
import asyncio
result = await asyncio.to_thread(blocking_function, arg1, arg2)
```

### Session is not thread-safe

Never share an `AsyncSession` across concurrent tasks:

```python
# WRONG
asyncio.gather(
    service_a(db, ...),
    service_b(db, ...),  # same session — race condition
)

# RIGHT — create separate sessions or run sequentially
results = await asyncio.gather(
    service_a_with_own_session(...),
    service_b_with_own_session(...),
)
```

---

## Celery + async

Celery tasks run in a synchronous worker process. If you need async code in a Celery task, run it explicitly:

```python
import asyncio
from app.tasks.celery_app import celery_app

@celery_app.task
def generate_report_async(run_id: int) -> str:
    return asyncio.run(_generate_report(run_id))

async def _generate_report(run_id: int) -> str:
    async with AsyncSessionLocal() as db:
        # ... async service calls
        return file_path
```
