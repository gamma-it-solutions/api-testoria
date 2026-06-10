import logging
import xml.etree.ElementTree as ET

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.mixins import not_deleted
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.test_run import TestRun
from app.schemas.test_result import TestResultCreate
from app.services import test_result_service
from app.utils import stats

logger = logging.getLogger(__name__)


async def _get_run(db: AsyncSession, run_id: int) -> TestRun:
    result = await db.execute(
        select(TestRun).where(TestRun.id == run_id, not_deleted(TestRun))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"TestRun {run_id} not found")
    return run


async def import_junit_xml(
    db: AsyncSession,
    test_run_id: int,
    xml_content: bytes,
    user_id: int,
) -> dict[str, int]:
    await _get_run(db, test_run_id)

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise BadRequestError(f"Invalid XML: {exc}") from exc

    submitted = 0
    unmatched = 0

    for testcase in root.findall(".//testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        title = f"{classname}.{name}" if classname else name

        result = await db.execute(
            select(TestCase).where(TestCase.title == title, not_deleted(TestCase))
        )
        case = result.scalar_one_or_none()
        if not case:
            unmatched += 1
            continue

        failure = testcase.find("failure")
        error = testcase.find("error")
        skip_el = testcase.find("skipped")

        if failure is not None:
            status = "failed"
            comment = failure.get("message") or failure.text
        elif error is not None:
            status = "failed"
            comment = error.get("message") or error.text
        elif skip_el is not None:
            status = "no_run"
            comment = "Test skipped"
        else:
            status = "passed"
            comment = None

        raw_time = testcase.get("time", "0")
        execution_time = int(float(raw_time))

        await test_result_service.submit(
            db,
            test_run_id,
            TestResultCreate(
                test_case_id=case.id,
                status=status,
                comment=comment,
                execution_time=execution_time,
            ),
            user_id,
        )
        submitted += 1

    return {"submitted": submitted, "skipped": unmatched}


async def process_webhook(
    db: AsyncSession,
    payload: dict[str, object],
) -> dict[str, str]:
    logger.info("CI webhook received: %s", payload.get("event", "unknown"))
    return {"status": "accepted"}


async def generate_badge(db: AsyncSession, run_id: int) -> str:
    await _get_run(db, run_id)

    total_result = await db.execute(
        select(func.count()).where(
            TestResult.test_run_id == run_id, not_deleted(TestResult)
        )
    )
    total = total_result.scalar() or 0

    if total == 0:
        ratio = 0.0
    else:
        passed_result = await db.execute(
            select(func.count()).where(
                TestResult.test_run_id == run_id,
                TestResult.status == "passed",
                not_deleted(TestResult),
            )
        )
        passed = passed_result.scalar() or 0
        ratio = stats.pass_rate(passed, total) or 0.0

    percent = ratio * 100
    label = f"{percent:.0f}%"
    if percent >= 90:
        color = "#4c1"
    elif percent >= 70:
        color = "#dfb317"
    else:
        color = "#e05d44"

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="106" height="20">'
        '<linearGradient id="b" x2="0" y2="100%">'
        '<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/>'
        "</linearGradient>"
        '<clipPath id="a"><rect width="106" height="20" rx="3" fill="#fff"/></clipPath>'
        '<g clip-path="url(#a)">'
        '<rect width="61" height="20" fill="#555"/>'
        f'<rect x="61" width="45" height="20" fill="{color}"/>'
        '<rect width="106" height="20" fill="url(#b)"/>'
        "</g>"
        "<g"
        ' fill="#fff" text-anchor="middle"'
        ' font-family="DejaVu Sans,sans-serif"'
        ' font-size="11">'
        '<text x="30.5" y="15" fill="#010101" fill-opacity=".3">tests</text>'
        '<text x="30.5" y="14">tests</text>'
        f'<text x="82.5" y="15" fill="#010101" fill-opacity=".3">{label}</text>'
        f'<text x="82.5" y="14">{label}</text>'
        "</g>"
        "</svg>"
    )
    return svg
