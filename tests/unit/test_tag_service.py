import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import tag_service


@pytest.mark.asyncio
async def test_create_tag_new(db_session: AsyncSession) -> None:
    tag, created = await tag_service.create_tag(db_session, "smoke")
    assert created is True
    assert tag.name == "smoke"
    assert tag.id is not None


@pytest.mark.asyncio
async def test_create_tag_normalizes_case_and_whitespace(
    db_session: AsyncSession,
) -> None:
    tag, _ = await tag_service.create_tag(db_session, "  Regression  ")
    assert tag.name == "regression"


@pytest.mark.asyncio
async def test_create_tag_idempotent(db_session: AsyncSession) -> None:
    first, created1 = await tag_service.create_tag(db_session, "ux")
    second, created2 = await tag_service.create_tag(db_session, "UX")
    assert created1 is True
    assert created2 is False
    assert first.id == second.id


@pytest.mark.asyncio
async def test_list_tags_orders_by_name(db_session: AsyncSession) -> None:
    await tag_service.create_tag(db_session, "zeta")
    await tag_service.create_tag(db_session, "alpha")
    await tag_service.create_tag(db_session, "mu")
    tags = await tag_service.list_tags(db_session, limit=10)
    names = [t.name for t in tags]
    assert names == sorted(names)
    assert {"alpha", "mu", "zeta"}.issubset(set(names))


@pytest.mark.asyncio
async def test_search_tags_prefix_case_insensitive(
    db_session: AsyncSession,
) -> None:
    await tag_service.create_tag(db_session, "login")
    await tag_service.create_tag(db_session, "logout")
    await tag_service.create_tag(db_session, "smoke")
    results = await tag_service.search_tags(db_session, "LOG")
    names = {t.name for t in results}
    assert "login" in names
    assert "logout" in names
    assert "smoke" not in names


@pytest.mark.asyncio
async def test_get_or_create_many_deduplicates(db_session: AsyncSession) -> None:
    tags = await tag_service.get_or_create_many(
        db_session, ["api", "API", "db", "  api  ", ""]
    )
    names = [t.name for t in tags]
    assert names == ["api", "db"]


@pytest.mark.asyncio
async def test_get_or_create_many_empty(db_session: AsyncSession) -> None:
    assert await tag_service.get_or_create_many(db_session, []) == []
