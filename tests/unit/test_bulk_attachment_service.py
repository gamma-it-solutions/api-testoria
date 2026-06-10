"""Unit tests for the bulk-attachment service helper.

Tests drive the service directly so failure modes (MinIO outages, partial
upload) can be simulated via monkey-patching `app.core.storage.put_object`.
Uses the integration-style HTTP fixtures for the fixture setup chain so we
don't need to know every model's constructor kwargs.
"""
from __future__ import annotations

import io
from typing import TypeAlias

import pytest
import pytest_asyncio
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.user import User
from app.services import test_result_service

UploadFiles: TypeAlias = list[tuple[str, bytes, str | None]]


def _png() -> bytes:
    img = Image.new("RGB", (2, 2), color=(1, 2, 3))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@pytest_asyncio.fixture
async def tester_user(db_session: AsyncSession) -> User:
    user = User(
        username="svc_tester",
        email="svc_tester@example.com",
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
        username="svc_lead",
        email="svc_lead@example.com",
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
async def result_id(
    client: AsyncClient,
    tester_headers: dict[str, str],
    lead_headers: dict[str, str],
) -> int:
    project = (await client.post(
        "/api/v1/projects", json={"name": "SvcProj"}, headers=lead_headers
    )).json()
    suite = (await client.post(
        f"/api/v1/projects/{project['id']}/test-suites",
        json={"name": "S"},
        headers=lead_headers,
    )).json()
    case = (await client.post(
        f"/api/v1/projects/{project['id']}/test-cases",
        json={
            "suite_id": suite["id"],
            "title": "C",
            "priority": "high",
            "type": "manual",
            "status": "active",
        },
        headers=lead_headers,
    )).json()
    run = (await client.post(
        f"/api/v1/projects/{project['id']}/test-runs",
        json={"name": "R", "suite_id": suite["id"]},
        headers=tester_headers,
    )).json()
    result = (await client.post(
        f"/api/v1/test-runs/{run['id']}/results",
        json={"test_case_id": case["id"], "status": "failed"},
        headers=tester_headers,
    )).json()
    return int(result["id"])


async def test_bulk_upload_all_succeed(
    db_session: AsyncSession,
    result_id: int,
    tester_user: User,
) -> None:
    files: UploadFiles = [
        ("a.png", _png(), "image/png"),
        ("b.png", _png(), "image/png"),
    ]
    uploaded, failed = await test_result_service.upload_attachments_bulk(
        db=db_session,
        result_id=result_id,
        files=files,
        user_id=tester_user.id,
    )
    assert len(uploaded) == 2
    assert failed == []
    for att in uploaded:
        assert att.storage_backend == "s3"
        assert att.object_key.startswith(f"results/{result_id}/")


async def test_bulk_upload_partial_failure(
    db_session: AsyncSession,
    result_id: int,
    tester_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import storage

    calls = {"n": 0}
    original = storage.put_object

    async def flaky(
        key: str,
        body: bytes,
        content_type: str | None = None,
        bucket: str | None = None,
    ) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated MinIO outage")
        return await original(key, body, content_type=content_type, bucket=bucket)

    monkeypatch.setattr(storage, "put_object", flaky)

    files: UploadFiles = [
        ("ok1.png", _png(), "image/png"),
        ("ok2.png", _png(), "image/png"),
        ("ok3.png", _png(), "image/png"),
    ]
    uploaded, failed = await test_result_service.upload_attachments_bulk(
        db=db_session,
        result_id=result_id,
        files=files,
        user_id=tester_user.id,
    )
    assert len(uploaded) == 2
    assert len(failed) == 1
    assert failed[0][0] == "ok2.png"


async def test_delete_attachment_calls_storage(
    db_session: AsyncSession,
    result_id: int,
    tester_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import storage

    deleted: list[str] = []

    async def record_delete(key: str, bucket: str | None = None) -> None:
        deleted.append(key)

    monkeypatch.setattr(storage, "delete_object", record_delete)

    [att], _ = await test_result_service.upload_attachments_bulk(
        db=db_session,
        result_id=result_id,
        files=[("a.png", _png(), "image/png")],
        user_id=tester_user.id,
    )
    await test_result_service.delete_attachment(db_session, result_id, att.id)
    assert deleted == [att.object_key]
