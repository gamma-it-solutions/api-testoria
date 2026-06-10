"""Concurrency guarantee for the outbox claim: two workers never grab the same
row. Requires a real Postgres (FOR UPDATE SKIP LOCKED is a no-op on SQLite), so
the module is skipped unless TEST_DATABASE_URL points at Postgres."""
import asyncio
import os

import pytest

from app.services import email_outbox_service

pytestmark = pytest.mark.skipif(
    "postgresql" not in os.environ.get("TEST_DATABASE_URL", ""),
    reason="SKIP LOCKED concurrency requires Postgres",
)


@pytest.mark.asyncio
async def test_two_claimers_never_share_a_row() -> None:
    from tests.conftest import TestSessionLocal

    # Seed committed rows visible to both sessions.
    async with TestSessionLocal() as seed:
        for i in range(20):
            await email_outbox_service.enqueue(
                seed,
                to_email=f"c{i}@example.com",
                template="welcome_invite",
                context={"link": "http://x/set-password?token=t"},
                subject="Hi",
            )
        await seed.commit()

    async def claim() -> list[int]:
        async with TestSessionLocal() as db:
            rows = await email_outbox_service.claim_batch(db, limit=20)
            await db.commit()
            return [r.id for r in rows]

    a, b = await asyncio.gather(claim(), claim())

    # Disjoint claims, and every pending row claimed exactly once.
    assert set(a).isdisjoint(set(b))
    assert len(a) + len(b) == 20

    async with TestSessionLocal() as db:
        remaining = await email_outbox_service.claim_batch(db, limit=20)
        await db.commit()
    assert remaining == []
