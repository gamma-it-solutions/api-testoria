"""
Seed script — creates the initial admin user if no admin exists.

Usage:
    python scripts/seed.py

Run once after `alembic upgrade head`. Safe to re-run: skips creation if an
admin user already exists.

Required env var:
    ADMIN_PASSWORD — set in .env or export before running

Optional env vars (defaults shown):
    ADMIN_USERNAME=admin
    ADMIN_EMAIL=admin@testoria.local
"""

import asyncio
import sys
from pathlib import Path

# Ensure the project root is on sys.path when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.core.roles import UserRole
from app.core.security import get_password_hash
from app.database import Base
from app.models.user import User


async def seed() -> None:
    if not settings.ADMIN_PASSWORD:
        print("ERROR: ADMIN_PASSWORD is not set. Add it to .env or export it.")
        sys.exit(1)

    # Import here to avoid circular issues; engine is created on import
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Admin already exists: '{existing.username}' ({existing.email}) — skipping.")
            await engine.dispose()
            return

        admin = User(
            username=settings.ADMIN_USERNAME,
            email=settings.ADMIN_EMAIL,
            hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        print(f"Admin created: username='{settings.ADMIN_USERNAME}' email='{settings.ADMIN_EMAIL}'")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
