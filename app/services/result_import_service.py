import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.mixins import not_deleted
from app.models.test_case import TestCase
from app.models.test_run import TestRun
from app.schemas.test_result import (
    MAX_REPORTED_UNMATCHED,
    ResultImportReport,
    TestResultCreate,
    UnmatchedCase,
)
from app.services import test_result_service

logger = logging.getLogger(__name__)

# Ordered: the first rule that resolves wins. `automation_id` beats `title`
# because it is the stable identifier; title is kept for parity with the older
# /ci/results/bulk contract.
_MATCH_RULES = (
    "automation_id",
    "automation_id_dotted",
    "automation_id_name",
    "title",
    "title_dotted",
)


@dataclass(frozen=True)
class ParsedCase:
    """One `<testcase>` element, framework-agnostic."""

    classname: str | None
    name: str
    status: str
    comment: str | None
    message: str | None
    stack_trace: str | None
    execution_time: int | None

    @property
    def identifier(self) -> str:
        return f"{self.classname}.{self.name}" if self.classname else self.name


def dotted(node_id: str) -> str:
    """Convert a pytest node ID to the dotted form JUnit XML reports.

    `tests/a/test_a.py::TestA::test_x` -> `tests.a.test_a.TestA.test_x`

    Only this direction is well-defined — going back is ambiguous because
    nothing marks where the module path ends and the class begins. So the
    normalisation is applied to the *stored* identifier at match time and never
    to the incoming XML.
    """
    return node_id.replace(".py::", ".").replace("::", ".").replace("/", ".")


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def parse_junit(xml_content: bytes) -> list[ParsedCase]:
    """Parse JUnit XML into ParsedCase records.

    Handles both `<testsuites><testsuite>` (pytest's default) and a bare
    `<testsuite>` root.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise BadRequestError(f"Invalid XML: {exc}") from exc

    parsed: list[ParsedCase] = []
    for element in root.iter("testcase"):
        classname = _clean(element.get("classname"))
        name = _clean(element.get("name")) or ""
        if not name:
            continue

        failure = element.find("failure")
        error = element.find("error")
        skipped = element.find("skipped")

        if failure is not None or error is not None:
            node = failure if failure is not None else error
            assert node is not None  # narrowed by the branch
            status = "failed"
            message = _clean(node.get("message"))
            stack_trace = _clean(node.text)
        elif skipped is not None:
            status = "no_run"
            message = _clean(skipped.get("message")) or "Test skipped"
            stack_trace = None
        else:
            status = "passed"
            message = None
            stack_trace = None

        parsed.append(
            ParsedCase(
                classname=classname,
                name=name,
                status=status,
                comment=message,
                message=message,
                stack_trace=stack_trace,
                execution_time=_parse_time(element.get("time")),
            )
        )
    return parsed


def _parse_time(raw: str | None) -> int | None:
    """JUnit reports seconds as a float; the column is integer seconds.

    `round`, not `int`: truncation sent every sub-second test to 0. Sub-500ms
    tests still round to 0 — moving the column to milliseconds is tracked as
    tech debt.
    """
    if raw is None:
        return None
    try:
        return round(float(raw))
    except ValueError:
        return None


def parse_json(content: bytes) -> list[ParsedCase]:
    """Parse a JSON result list.

    Expects `[{"classname"?, "name", "status", "message"?, "stack_trace"?,
    "execution_time"?}, ...]` — the shape a non-JUnit framework can emit
    without pretending to be JUnit.
    """
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BadRequestError(f"Invalid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise BadRequestError("JSON results must be a list of objects")

    parsed: list[ParsedCase] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BadRequestError(f"Entry {index} is not an object")
        name = _clean(str(entry.get("name", "")))
        if not name:
            raise BadRequestError(f"Entry {index} is missing 'name'")
        status = str(entry.get("status", "no_run"))
        message = _clean(entry.get("message"))
        classname = entry.get("classname")
        parsed.append(
            ParsedCase(
                classname=_clean(str(classname)) if classname else None,
                name=name,
                status=status,
                comment=_clean(entry.get("comment")) or message,
                message=message,
                stack_trace=_clean(entry.get("stack_trace")),
                execution_time=_parse_time(
                    None
                    if entry.get("execution_time") is None
                    else str(entry["execution_time"])
                ),
            )
        )
    return parsed


class _CaseIndex:
    """Lookup keys -> case id, per rule, with ambiguity preserved.

    A key that two cases claim is recorded as ambiguous rather than resolved
    first-wins — a duplicated `automation_id` is a data bug the user has to see.
    """

    def __init__(self) -> None:
        self._by_rule: dict[str, dict[str, set[int]]] = {
            rule: {} for rule in _MATCH_RULES
        }

    def add(self, rule: str, key: str | None, case_id: int) -> None:
        if not key:
            return
        self._by_rule[rule].setdefault(key, set()).add(case_id)

    def lookup(self, key: str, name: str) -> tuple[int | None, str | None, bool]:
        """Return `(case_id, rule, ambiguous)` for the first rule that hits."""
        for rule in _MATCH_RULES:
            probe = name if rule == "automation_id_name" else key
            hits = self._by_rule[rule].get(probe)
            if not hits:
                continue
            if len(hits) > 1:
                return None, rule, True
            return next(iter(hits)), rule, False
        return None, None, False


async def _build_index(
    db: AsyncSession, run: TestRun
) -> tuple[_CaseIndex, set[int]]:
    """One query for every case in the run's scope, indexed four ways."""
    result = await db.execute(
        select(TestCase.id, TestCase.automation_id, TestCase.title).where(
            TestCase.id.in_(test_result_service.run_scope_case_ids(run)),
            not_deleted(TestCase),
        )
    )
    index = _CaseIndex()
    in_scope: set[int] = set()
    for case_id, automation_id, title in result.all():
        in_scope.add(case_id)
        index.add("automation_id", automation_id, case_id)
        index.add("automation_id_name", automation_id, case_id)
        index.add("title", title, case_id)
        if automation_id:
            index.add("automation_id_dotted", dotted(automation_id), case_id)
        if title:
            index.add("title_dotted", dotted(title), case_id)
    return index, in_scope


async def _get_run(db: AsyncSession, run_id: int) -> TestRun:
    result = await db.execute(
        select(TestRun).where(TestRun.id == run_id, not_deleted(TestRun))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"TestRun {run_id} not found")
    return run


def detect_format(filename: str | None, content: bytes) -> str:
    """Pick a parser from the extension, falling back to sniffing the content."""
    if filename:
        lowered = filename.lower()
        if lowered.endswith(".xml"):
            return "junit"
        if lowered.endswith(".json"):
            return "json"
    head = content.lstrip()[:1]
    return "json" if head in (b"[", b"{") else "junit"


async def import_results(
    db: AsyncSession,
    run_id: int,
    content: bytes,
    user_id: int,
    filename: str | None = None,
    fmt: str = "auto",
) -> ResultImportReport:
    """Parse a report, resolve each entry to a test case, and submit the matches.

    Always returns a report — an unmatched case is information, not an error.
    `--strict` is a client policy (the CLI exits 2); baking it in would force
    every consumer into the same failure taste.
    """
    run = await _get_run(db, run_id)

    resolved_format = detect_format(filename, content) if fmt == "auto" else fmt
    if resolved_format == "junit":
        parsed = parse_junit(content)
    elif resolved_format == "json":
        parsed = parse_json(content)
    else:
        raise BadRequestError(f"Unsupported format '{fmt}'")

    index, in_scope = await _build_index(db, run)

    items: list[TestResultCreate] = []
    unmatched: list[UnmatchedCase] = []
    matched_by: dict[str, int] = {}
    seen_case_ids: set[int] = set()

    for case in parsed:
        case_id, rule, ambiguous = index.lookup(case.identifier, case.name)
        if ambiguous:
            unmatched.append(_unmatched(case, "ambiguous"))
            continue
        if case_id is None:
            unmatched.append(_unmatched(case, "no_match"))
            continue
        if case_id not in in_scope:
            unmatched.append(_unmatched(case, "out_of_scope"))
            continue
        if case_id in seen_case_ids:
            # Two XML entries claiming one case — the later would silently
            # overwrite the earlier, so report it instead.
            unmatched.append(_unmatched(case, "ambiguous"))
            continue

        seen_case_ids.add(case_id)
        assert rule is not None
        matched_by[rule] = matched_by.get(rule, 0) + 1
        items.append(
            TestResultCreate(
                test_case_id=case_id,
                status=case.status,
                comment=case.comment,
                message=case.message,
                stack_trace=case.stack_trace,
                execution_time=case.execution_time,
            )
        )

    submitted, status_counts = await test_result_service.submit_many(
        db, run_id, items, user_id
    )

    return ResultImportReport(
        run_id=run_id,
        total=len(parsed),
        matched=len(items),
        submitted=submitted,
        unmatched=len(unmatched),
        unmatched_cases=unmatched[:MAX_REPORTED_UNMATCHED],
        matched_by=matched_by,
        status_counts=status_counts,
    )


def _unmatched(case: ParsedCase, reason: str) -> UnmatchedCase:
    return UnmatchedCase(
        identifier=case.identifier,
        classname=case.classname,
        name=case.name,
        status=case.status,
        reason=reason,
    )
