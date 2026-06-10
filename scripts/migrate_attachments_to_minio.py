"""One-shot migration: move local-disk attachments to MinIO / S3.

Idempotent: re-runs skip rows already `storage_backend='s3'`.

Usage:
    # Dry run — logs what would move, touches nothing
    python scripts/migrate_attachments_to_minio.py

    # Actually upload and flip `storage_backend`
    python scripts/migrate_attachments_to_minio.py --commit

    # Upload + delete the source file after verification
    python scripts/migrate_attachments_to_minio.py --commit --delete-local
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core import storage
from app.core.uploads import sanitize_filename
from app.models.result_attachment import ResultAttachment

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_attachments")


def _build_object_key(result_id: int, filename: str) -> str:
    safe = sanitize_filename(filename)
    return f"results/{result_id}/{uuid.uuid4().hex}-{safe}"


async def run(commit: bool, delete_local: bool) -> int:
    engine = create_async_engine(settings.DATABASE_URL, future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    moved = 0
    skipped = 0
    failed = 0
    missing = 0

    try:
        await storage.ensure_bucket()
    except Exception as err:  # noqa: BLE001
        logger.error("Bucket ensure failed: %s — aborting", err)
        return 2

    async with Session() as db:
        rows = (
            await db.execute(
                select(ResultAttachment).where(
                    ResultAttachment.storage_backend == "local"
                )
            )
        ).scalars().all()

        if not rows:
            logger.info("No local-backed attachments to migrate. All clean.")
            return 0

        logger.info("Found %d local-backed attachments.", len(rows))

        for att in rows:
            disk_path = Path(att.object_key)
            if not disk_path.exists():
                logger.warning(
                    "attachment %d: file missing from disk (%s) — skipping",
                    att.id,
                    disk_path,
                )
                missing += 1
                continue

            content = disk_path.read_bytes()
            new_key = _build_object_key(att.test_result_id, att.filename)

            if not commit:
                logger.info(
                    "[dry-run] attachment %d (%s): would upload %d bytes to %s",
                    att.id,
                    att.filename,
                    len(content),
                    new_key,
                )
                skipped += 1
                continue

            try:
                await storage.put_object(
                    new_key, content, content_type=att.mime_type
                )
            except Exception as err:  # noqa: BLE001
                logger.error(
                    "attachment %d: MinIO upload failed (%s) — leaving as local",
                    att.id,
                    err,
                )
                failed += 1
                continue

            att.object_key = new_key
            att.storage_backend = "s3"
            await db.flush()
            moved += 1
            logger.info("attachment %d: migrated → %s", att.id, new_key)

            if delete_local:
                try:
                    disk_path.unlink()
                    logger.info("attachment %d: source file removed", att.id)
                except Exception as err:  # noqa: BLE001
                    logger.warning(
                        "attachment %d: failed to remove source %s — %s",
                        att.id,
                        disk_path,
                        err,
                    )

        if commit:
            await db.commit()

    logger.info(
        "Done — moved=%d skipped=%d missing=%d failed=%d",
        moved,
        skipped,
        missing,
        failed,
    )
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually upload and flip storage_backend (default: dry-run).",
    )
    parser.add_argument(
        "--delete-local",
        action="store_true",
        help="After successful upload, remove the source file from disk.",
    )
    args = parser.parse_args()
    return asyncio.run(run(commit=args.commit, delete_local=args.delete_local))


if __name__ == "__main__":
    sys.exit(main())
