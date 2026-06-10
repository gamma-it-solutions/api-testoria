import csv
import io
import json

import openpyxl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.mixins import not_deleted
from app.models.test_case import TestCase
from app.models.test_suite import TestSuite

_EXPORT_COLUMNS = [
    "id",
    "suite_id",
    "title",
    "description",
    "preconditions",
    "steps_json",
    "priority",
    "type",
    "status",
    "tags",
]


async def _fetch_cases(db: AsyncSession, project_id: int) -> list[TestCase]:
    result = await db.execute(
        select(TestCase)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(
            TestSuite.project_id == project_id,
            not_deleted(TestSuite),
            not_deleted(TestCase),
        )
        .options(selectinload(TestCase.tags))
        .order_by(TestCase.id)
    )
    return list(result.scalars().all())


def _case_to_row(tc: TestCase) -> list[str]:
    return [
        str(tc.id),
        str(tc.suite_id),
        tc.title,
        tc.description or "",
        tc.preconditions or "",
        json.dumps(tc.steps),
        tc.priority,
        tc.type,
        tc.status,
        ",".join(t.name for t in tc.tags),
    ]


async def export_csv(db: AsyncSession, project_id: int) -> bytes:
    cases = await _fetch_cases(db, project_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_COLUMNS)
    for tc in cases:
        writer.writerow(_case_to_row(tc))
    return buf.getvalue().encode("utf-8")


async def export_excel(db: AsyncSession, project_id: int) -> bytes:
    cases = await _fetch_cases(db, project_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "TestCases"
    ws.append(_EXPORT_COLUMNS)
    for tc in cases:
        ws.append(_case_to_row(tc))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
