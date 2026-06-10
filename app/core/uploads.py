"""Validation helpers for file uploads."""

from __future__ import annotations

import io
import re

from fastapi import HTTPException, UploadFile

from app.config import settings

IMAGE_MIME_WHITELIST: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
)

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str) -> str:
    name = name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _FILENAME_SAFE.sub("_", name)
    return cleaned[:200] or "upload"


async def validate_upload_size(
    file: UploadFile,
    max_bytes: int | None = None,
) -> bytes:
    """Read and size-check an upload. No MIME / content validation."""
    cap = max_bytes if max_bytes is not None else settings.MAX_ATTACHMENT_BYTES
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > cap:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {cap} bytes",
        )
    return content


async def validate_image_upload(
    file: UploadFile,
    max_bytes: int | None = None,
) -> bytes:
    """Validate and return the full bytes of an image upload.

    Raises HTTPException(415) on unsupported MIME, (413) on oversize,
    (400) on bytes that don't parse as an image.
    """
    content_type = (file.content_type or "").lower()
    if content_type not in IMAGE_MIME_WHITELIST:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {content_type or 'unknown'}",
        )

    content = await validate_upload_size(file, max_bytes=max_bytes)

    # Second-line defence: bytes must actually parse as an image.
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as img:
            img.verify()
    except Exception as err:  # noqa: BLE001 — PIL failure means "not a real image"
        raise HTTPException(
            status_code=400, detail="Bytes are not a valid image"
        ) from err

    return content
