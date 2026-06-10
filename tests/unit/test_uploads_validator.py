"""Unit tests for validate_image_upload + validate_upload_size."""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.core.uploads import (
    IMAGE_MIME_WHITELIST,
    sanitize_filename,
    validate_image_upload,
    validate_upload_size,
)


def _png(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    img = Image.new("RGB", (4, 4), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_file(name: str, content: bytes, content_type: str) -> UploadFile:
    from starlette.datastructures import Headers

    file = UploadFile(
        filename=name,
        file=io.BytesIO(content),
        headers=Headers({"content-type": content_type}),
    )
    return file


async def test_validate_image_accepts_real_png() -> None:
    file = _upload_file("a.png", _png(), "image/png")
    content = await validate_image_upload(file)
    assert content == _png()


async def test_validate_image_rejects_bad_mime() -> None:
    file = _upload_file("a.txt", b"text", "text/plain")
    with pytest.raises(HTTPException) as exc:
        await validate_image_upload(file)
    assert exc.value.status_code == 415


async def test_validate_image_rejects_non_image_bytes() -> None:
    file = _upload_file("bad.png", b"not a real image", "image/png")
    with pytest.raises(HTTPException) as exc:
        await validate_image_upload(file)
    assert exc.value.status_code == 400


async def test_validate_image_rejects_empty_file() -> None:
    file = _upload_file("e.png", b"", "image/png")
    with pytest.raises(HTTPException) as exc:
        await validate_image_upload(file)
    assert exc.value.status_code == 400


async def test_validate_upload_size_accepts_any_mime() -> None:
    file = _upload_file("log.txt", b"some log", "text/plain")
    content = await validate_upload_size(file)
    assert content == b"some log"


async def test_validate_upload_size_rejects_too_large() -> None:
    big = b"x" * 100
    file = _upload_file("big.txt", big, "text/plain")
    with pytest.raises(HTTPException) as exc:
        await validate_upload_size(file, max_bytes=50)
    assert exc.value.status_code == 413


def test_sanitize_filename_scrubs_traversal_and_specials() -> None:
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("ok name.png") == "ok_name.png"
    assert sanitize_filename("..\\windows\\foo.txt") == "foo.txt"
    assert sanitize_filename("") == "upload"


def test_mime_whitelist_is_closed_set() -> None:
    assert "image/png" in IMAGE_MIME_WHITELIST
    assert "application/pdf" not in IMAGE_MIME_WHITELIST
