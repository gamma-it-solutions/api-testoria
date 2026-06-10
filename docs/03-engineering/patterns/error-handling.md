# Error Handling

Exception hierarchy and HTTP error patterns in Testoria.

---

## Custom exceptions (`app/core/exceptions.py`)

```python
class TestoriaError(Exception):
    """Base exception for all Testoria errors."""
    status_code: int = 500
    detail: str = "Internal server error"

class NotFoundError(TestoriaError):
    status_code = 404

class ForbiddenError(TestoriaError):
    status_code = 403

class ConflictError(TestoriaError):
    status_code = 409

class ValidationError(TestoriaError):
    status_code = 422

class ExternalServiceError(TestoriaError):
    """Raised when a 3rd-party API (Jira, GitHub) fails."""
    status_code = 502
```

---

## Raising errors in services

Services raise domain exceptions. Routers convert them to HTTP responses:

```python
# In a service
async def get_by_id(db, project_id: int) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError(f"Project {project_id} not found")
    return project
```

```python
# In a router (thin pattern — let exceptions bubble up)
@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    return await ProjectService.get_by_id(db, project_id)
```

A global exception handler in `app/main.py` catches `TestoriaError` and returns the right HTTP code:

```python
@app.exception_handler(TestoriaError)
async def testoria_exception_handler(request: Request, exc: TestoriaError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc)}
    )
```

---

## Using FastAPI HTTPException directly

For simple, one-off cases in routers (not services), `HTTPException` is acceptable:

```python
if not project:
    raise HTTPException(status_code=404, detail="Project not found")
```

Use custom exceptions when the same error condition is raised from multiple places or when you need the error to be testable in unit tests without HTTP context.

---

## Error response format

All errors return:
```json
{ "detail": "Human-readable error message" }
```

FastAPI 422 validation errors return:
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "name"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

Clients should handle both shapes.

---

## Database integrity errors

Catch `IntegrityError` for unique constraint violations:

```python
from sqlalchemy.exc import IntegrityError

try:
    db.add(project)
    await db.flush()
except IntegrityError:
    await db.rollback()
    raise ConflictError(f"Project with key '{data.key}' already exists")
```

---

## External service errors

```python
try:
    result = await DefectService.create_jira_issue(config, summary, description)
except httpx.HTTPStatusError as e:
    raise ExternalServiceError(f"Jira API error: {e.response.status_code}")
except httpx.TimeoutException:
    raise ExternalServiceError("Jira API timeout")
```
