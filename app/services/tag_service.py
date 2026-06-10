from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag


def _normalize(name: str) -> str:
    return name.strip().lower()


async def list_tags(db: AsyncSession, limit: int = 50) -> list[Tag]:
    result = await db.execute(select(Tag).order_by(Tag.name).limit(limit))
    return list(result.scalars().all())


async def search_tags(db: AsyncSession, q: str, limit: int = 50) -> list[Tag]:
    prefix = _normalize(q).replace("%", "").replace("_", "")
    result = await db.execute(
        select(Tag)
        .where(Tag.name.ilike(f"{prefix}%"))
        .order_by(Tag.name)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_tag_by_name(db: AsyncSession, name: str) -> Tag | None:
    normalized = _normalize(name)
    result = await db.execute(select(Tag).where(Tag.name == normalized))
    return result.scalar_one_or_none()


async def create_tag(db: AsyncSession, name: str) -> tuple[Tag, bool]:
    normalized = _normalize(name)
    existing = await get_tag_by_name(db, normalized)
    if existing is not None:
        return existing, False
    tag = Tag(name=normalized)
    db.add(tag)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await get_tag_by_name(db, normalized)
        if existing is None:
            raise
        return existing, False
    await db.refresh(tag)
    return tag, True


async def get_or_create_many(db: AsyncSession, names: list[str]) -> list[Tag]:
    if not names:
        return []
    seen: set[str] = set()
    tags: list[Tag] = []
    for raw in names:
        normalized = _normalize(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tag, _ = await create_tag(db, normalized)
        tags.append(tag)
    return tags
