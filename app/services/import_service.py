import csv
import io
import json
from typing import Any

import openpyxl

from app.schemas.test_case import ImportResult, TestCaseCreate, TestStep


def _row_to_create(
    row: dict[str, str],
    project_id: int,  # noqa: ARG001
) -> TestCaseCreate:
    steps_raw = row.get("steps_json", "[]") or "[]"
    try:
        steps_data: list[dict[str, str]] = json.loads(steps_raw)
    except (json.JSONDecodeError, ValueError):
        steps_data = []

    steps = [
        TestStep(step=s.get("step", ""), expected=s.get("expected", ""))
        for s in steps_data
    ]

    tags_raw = row.get("tags", "") or ""
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    suite_id_raw = row.get("suite_id", "")
    try:
        suite_id = int(suite_id_raw)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid suite_id: {suite_id_raw!r}")

    return TestCaseCreate(
        suite_id=suite_id,
        title=row.get("title", "").strip(),
        description=row.get("description") or None,
        preconditions=row.get("preconditions") or None,
        steps=steps,
        priority=row.get("priority", "medium") or "medium",
        type=row.get("type", "manual") or "manual",
        status=row.get("status", "draft") or "draft",
        tags=tags,
    )


def parse_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def parse_excel(content: bytes) -> list[dict[str, str]]:
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return []

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h) if h is not None else "" for h in rows[0]]
    result: list[dict[str, str]] = []
    for row in rows[1:]:
        result.append(
            {headers[i]: str(v) if v is not None else "" for i, v in enumerate(row)}
        )
    return result


async def import_test_cases(
    raw_rows: list[dict[str, str]],
    project_id: int,
    db: Any,
) -> ImportResult:
    from app.services import test_case_service  # local import to avoid circular

    created = 0
    errors: list[dict[str, Any]] = []

    for i, row in enumerate(raw_rows):
        try:
            data = _row_to_create(row, project_id)
            await test_case_service.create_test_case(db, project_id, data)
            created += 1
        except Exception as exc:
            errors.append({"row": i + 1, "detail": str(exc)})

    return ImportResult(created=created, errors=errors)
