"""Integration tests for bulk attachment upload + URL exposure + delete paths.

Uses the in-memory MinIO stub from `tests/conftest.py` so no real MinIO
server is needed.
"""
from __future__ import annotations

import io

import pytest
import pytest_asyncio
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User


def _tiny_png(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    img = Image.new("RGB", (4, 4), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="ba_tester",
        email="ba_tester@example.com",
        hashed_password=get_password_hash("password"),
        role="tester",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def lead_user(db_session: AsyncSession) -> User:
    user = User(
        username="ba_lead",
        email="ba_lead@example.com",
        hashed_password=get_password_hash("password"),
        role="lead",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest.fixture
def tester_headers(tester_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(tester_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def lead_headers(lead_user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(lead_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def project_id(client: AsyncClient, lead_headers: dict[str, str]) -> int:
    resp = await client.post(
        "/api/v1/projects", json={"name": "Bulk Project"}, headers=lead_headers
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def suite_id(
    client: AsyncClient, project_id: int, lead_headers: dict[str, str]
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-suites",
        json={"name": "Bulk Suite"},
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def case_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    lead_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-cases",
        json={
            "suite_id": suite_id,
            "title": "Bulk Case",
            "priority": "high",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def run_id(
    client: AsyncClient,
    project_id: int,
    suite_id: int,
    tester_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/test-runs",
        json={"name": "Bulk Run", "suite_id": suite_id},
        headers=tester_headers,
    )
    return int(resp.json()["id"])


@pytest_asyncio.fixture
async def result_id(
    client: AsyncClient,
    run_id: int,
    case_id: int,
    tester_headers: dict[str, str],
) -> int:
    resp = await client.post(
        f"/api/v1/test-runs/{run_id}/results",
        json={"test_case_id": case_id, "status": "failed"},
        headers=tester_headers,
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def test_bulk_upload_happy_path(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    files = [
        ("files", ("a.png", _tiny_png((255, 0, 0)), "image/png")),
        ("files", ("b.png", _tiny_png((0, 255, 0)), "image/png")),
        ("files", ("c.png", _tiny_png((0, 0, 255)), "image/png")),
    ]
    resp = await client.post(
        f"/api/v1/test-results/{result_id}/attachments/bulk",
        files=files,
        headers=tester_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert len(data["uploaded"]) == 3
    assert data["failed"] == []
    for att in data["uploaded"]:
        assert att["url"].startswith("http://fake-minio/")
        assert att["storage_backend"] == "s3"


async def test_bulk_upload_rejects_non_image(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    files = [
        ("files", ("ok.png", _tiny_png(), "image/png")),
        ("files", ("bad.txt", b"not an image", "text/plain")),
    ]
    resp = await client.post(
        f"/api/v1/test-results/{result_id}/attachments/bulk",
        files=files,
        headers=tester_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["uploaded"]) == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["filename"] == "bad.txt"


async def test_bulk_upload_cap_rejected(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    files = [
        ("files", (f"{i}.png", _tiny_png(), "image/png")) for i in range(11)
    ]
    resp = await client.post(
        f"/api/v1/test-results/{result_id}/attachments/bulk",
        files=files,
        headers=tester_headers,
    )
    assert resp.status_code == 400


async def test_bulk_upload_empty_bytes_are_quarantined(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    resp = await client.post(
        f"/api/v1/test-results/{result_id}/attachments/bulk",
        files=[("files", ("e.png", b"", "image/png"))],
        headers=tester_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["uploaded"] == []
    assert len(body["failed"]) == 1


async def test_result_response_exposes_attachment_urls(
    client: AsyncClient,
    run_id: int,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    await client.post(
        f"/api/v1/test-results/{result_id}/attachments/bulk",
        files=[("files", ("a.png", _tiny_png(), "image/png"))],
        headers=tester_headers,
    )
    list_resp = await client.get(
        f"/api/v1/test-runs/{run_id}/results",
        headers=tester_headers,
    )
    assert list_resp.status_code == 200
    [row] = list_resp.json()
    assert len(row["attachments"]) == 1
    att = row["attachments"][0]
    assert att["url"].startswith("http://fake-minio/")
    assert att["storage_backend"] == "s3"


async def test_delete_attachment_removes_object(
    client: AsyncClient,
    result_id: int,
    tester_headers: dict[str, str],
) -> None:
    upload = await client.post(
        f"/api/v1/test-results/{result_id}/attachments/bulk",
        files=[("files", ("a.png", _tiny_png(), "image/png"))],
        headers=tester_headers,
    )
    attach_id = upload.json()["uploaded"][0]["id"]

    delete = await client.delete(
        f"/api/v1/test-results/{result_id}/attachments/{attach_id}",
        headers=tester_headers,
    )
    assert delete.status_code == 204

    again = await client.delete(
        f"/api/v1/test-results/{result_id}/attachments/{attach_id}",
        headers=tester_headers,
    )
    assert again.status_code == 404
