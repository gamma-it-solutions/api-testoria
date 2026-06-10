"""Integration tests for CSV/Excel import and export of test cases."""
import io

import openpyxl
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.test_suite import TestSuite


@pytest_asyncio.fixture
async def ie_project(db_session: AsyncSession) -> Project:
    project = Project(name="Import Export Project", description="IE tests")
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def ie_suite(db_session: AsyncSession, ie_project: Project) -> TestSuite:
    suite = TestSuite(project_id=ie_project.id, name="IE Suite")
    db_session.add(suite)
    await db_session.flush()
    await db_session.refresh(suite)
    return suite


_CSV_HEADER = (
    "suite_id,title,description,preconditions,steps_json,priority,type,status,tags"
)


def _make_csv(suite_id: int, rows: int = 3) -> bytes:
    lines = [_CSV_HEADER]
    for i in range(1, rows + 1):
        lines.append(f"{suite_id},Test Case {i},Desc {i},,[]" ",medium,manual,draft,")
    return "\n".join(lines).encode("utf-8")


_EXCEL_HEADER = [
    "suite_id", "title", "description", "preconditions",
    "steps_json", "priority", "type", "status", "tags",
]


def _make_excel(suite_id: int, rows: int = 2) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(_EXCEL_HEADER)
    for i in range(1, rows + 1):
        row = [suite_id, f"Excel TC {i}", f"Desc {i}", "", "[]", "high", "manual"]
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- CSV import ---


@pytest.mark.asyncio
async def test_csv_import_creates_test_cases(
    client: AsyncClient,
    admin_headers: dict[str, str],
    ie_project: Project,
    ie_suite: TestSuite,
) -> None:
    csv_bytes = _make_csv(ie_suite.id, rows=3)
    response = await client.post(
        f"/api/v1/projects/{ie_project.id}/test-cases/import",
        headers=admin_headers,
        files={"file": ("cases.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 3
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_csv_import_returns_error_for_invalid_suite(
    client: AsyncClient,
    admin_headers: dict[str, str],
    ie_project: Project,
) -> None:
    # suite_id 999999 does not exist
    csv_content = (
        b"suite_id,title,description,preconditions,steps_json,priority,type,status,tags\n"
        b"999999,Bad TC,,,[],,manual,draft,\n"
    )
    response = await client.post(
        f"/api/v1/projects/{ie_project.id}/test-cases/import",
        headers=admin_headers,
        files={"file": ("bad.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 0
    assert len(data["errors"]) == 1


# --- Excel import ---


@pytest.mark.asyncio
async def test_excel_import_creates_test_cases(
    client: AsyncClient,
    admin_headers: dict[str, str],
    ie_project: Project,
    ie_suite: TestSuite,
) -> None:
    xlsx_bytes = _make_excel(ie_suite.id, rows=2)
    response = await client.post(
        f"/api/v1/projects/{ie_project.id}/test-cases/import",
        headers=admin_headers,
        files={
            "file": (
                "cases.xlsx",
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 2
    assert data["errors"] == []


# --- CSV export ---


@pytest.mark.asyncio
async def test_csv_export_returns_csv(
    client: AsyncClient,
    admin_headers: dict[str, str],
    ie_project: Project,
    ie_suite: TestSuite,
) -> None:
    # Create a test case first
    await client.post(
        f"/api/v1/projects/{ie_project.id}/test-cases",
        headers=admin_headers,
        json={
            "suite_id": ie_suite.id,
            "title": "Export Me",
            "priority": "medium",
            "type": "manual",
            "status": "draft",
            "steps": [],
            "tags": [],
        },
    )

    response = await client.get(
        f"/api/v1/projects/{ie_project.id}/test-cases/export",
        headers=admin_headers,
        params={"format": "csv"},
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    text = response.text
    assert "suite_id" in text
    assert "title" in text
    assert "Export Me" in text


@pytest.mark.asyncio
async def test_excel_export_returns_xlsx(
    client: AsyncClient,
    admin_headers: dict[str, str],
    ie_project: Project,
    ie_suite: TestSuite,
) -> None:
    response = await client.get(
        f"/api/v1/projects/{ie_project.id}/test-cases/export",
        headers=admin_headers,
        params={"format": "excel"},
    )
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    # XLSX zip magic bytes
    assert response.content[:2] == b"PK"


# --- Auth enforcement ---


@pytest.mark.asyncio
async def test_import_requires_auth(client: AsyncClient, ie_project: Project) -> None:
    response = await client.post(
        f"/api/v1/projects/{ie_project.id}/test-cases/import",
        files={"file": ("x.csv", b"suite_id,title\n", "text/csv")},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_export_requires_auth(client: AsyncClient, ie_project: Project) -> None:
    response = await client.get(
        f"/api/v1/projects/{ie_project.id}/test-cases/export"
    )
    assert response.status_code == 401
