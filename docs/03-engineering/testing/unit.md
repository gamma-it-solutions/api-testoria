# Unit Tests

Writing unit tests for Testoria backend services.

---

## What to unit test

- Service methods: happy path, error paths, edge cases
- `app/core/security.py`: token creation, decoding, password hashing
- `app/utils/pagination.py`: pagination math
- Parser logic: JUnit XML parsing in `app/services/import_service.py`

---

## Test structure

```python
# tests/unit/test_project_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.project_service import ProjectService
from app.schemas.project import ProjectCreate

@pytest.mark.asyncio
async def test_create_project():
    # Arrange
    db = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    data = ProjectCreate(name="My Project", key="MYPROJ", description="Test")

    # Act
    project = await ProjectService.create(db, data, user_id=1)

    # Assert
    db.add.assert_called_once()
    assert project.name == "My Project"
    assert project.key == "MYPROJ"
```

Use `unittest.mock.AsyncMock` for async collaborators.

---

## Testing security functions

```python
# tests/unit/test_security.py
import pytest
from app.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)

def test_password_hash_and_verify():
    plain = "s3cr3t"
    hashed = get_password_hash(plain)
    assert hashed != plain
    assert verify_password(plain, hashed)
    assert not verify_password("wrong", hashed)

def test_access_token_roundtrip():
    data = {"sub": 42, "username": "alice", "role": "tester"}
    token = create_access_token(data)
    payload = decode_token(token)
    assert payload["sub"] == 42
    assert payload["type"] == "access"

def test_decode_invalid_token_returns_none():
    payload = decode_token("not.a.real.token")
    assert payload is None
```

---

## Testing parsers

```python
# tests/unit/test_import_service.py
import pytest
from app.services.import_service import ImportService

JUNIT_XML = b"""
<testsuites>
  <testsuite name="unit">
    <testcase classname="tests.auth" name="test_login" time="0.5"/>
    <testcase classname="tests.auth" name="test_bad_password" time="0.2">
      <failure message="AssertionError">Expected 401, got 200</failure>
    </testcase>
    <testcase classname="tests.auth" name="test_inactive_user" time="0.1">
      <skipped/>
    </testcase>
  </testsuite>
</testsuites>
"""

def test_parse_junit_xml():
    results = ImportService.parse_junit_xml(JUNIT_XML)
    assert len(results) == 3
    assert results[0]["status"] == "Passed"
    assert results[1]["status"] == "Failed"
    assert results[1]["comment"] == "AssertionError"
    assert results[2]["status"] == "Skipped"
```

---

## Marking async tests

All async test functions must be decorated with `@pytest.mark.asyncio` (or configure `asyncio_mode = "auto"` in `pytest.ini`):

```ini
# pytest.ini
[pytest]
asyncio_mode = auto
```

With `asyncio_mode = auto`, all `async def test_*` functions are automatically treated as async tests — no decorator needed.
