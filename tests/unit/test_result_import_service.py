import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_run import TestRun
from app.models.test_suite import TestSuite
from app.models.user import User
from app.schemas.test_result import MAX_REPORTED_UNMATCHED
from app.services import result_import_service, test_result_service
from app.services.result_import_service import dotted, parse_junit


def test_dotted_converts_a_class_based_node_id() -> None:
    assert (
        dotted("tests/auth/test_auth.py::TestAuth::test_login")
        == "tests.auth.test_auth.TestAuth.test_login"
    )


def test_dotted_converts_a_module_level_node_id() -> None:
    assert (
        dotted("tests/auth/test_auth.py::test_login")
        == "tests.auth.test_auth.test_login"
    )


def test_dotted_preserves_a_parametrized_suffix() -> None:
    assert (
        dotted("tests/users/test_users.py::TestUsers::test_roles[TESTER]")
        == "tests.users.test_users.TestUsers.test_roles[TESTER]"
    )


def test_dotted_is_idempotent_on_already_dotted_input() -> None:
    already = "tests.auth.test_auth.TestAuth.test_login"
    assert dotted(already) == already


# Exactly what pytest 8.3.5 emits (junit_family=xunit2): no file/line/nodeid.
_PYTEST_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="1" tests="5">
<testcase classname="tests.auth.test_auth.TestAuth" name="test_ok" time="0.011"/>
<testcase classname="tests.auth.test_auth.TestAuth" name="test_bad" time="0.002">
<failure message="AssertionError: boom">trace here</failure></testcase>
<testcase classname="tests.auth.test_auth" name="test_param[TESTER]" time="0.0"/>
<testcase classname="tests.auth.test_auth" name="test_skip" time="0.0">
<skipped type="pytest.skip" message="nope"/></testcase>
<testcase classname="tests.auth.test_auth" name="test_err" time="0.0">
<error message="Fixture blew up">setup trace</error></testcase>
</testsuite></testsuites>"""


def test_parse_junit_maps_every_status() -> None:
    parsed = {c.name: c for c in parse_junit(_PYTEST_XML)}

    assert parsed["test_ok"].status == "passed"
    assert parsed["test_bad"].status == "failed"
    assert parsed["test_bad"].message == "AssertionError: boom"
    assert parsed["test_bad"].stack_trace == "trace here"
    assert parsed["test_skip"].status == "no_run"
    # <error> is a failure, not a separate status.
    assert parsed["test_err"].status == "failed"


def test_parse_junit_keeps_the_parametrized_name() -> None:
    names = {c.name for c in parse_junit(_PYTEST_XML)}
    assert "test_param[TESTER]" in names


def test_parse_junit_builds_the_dotted_identifier() -> None:
    parsed = {c.name: c for c in parse_junit(_PYTEST_XML)}
    assert parsed["test_ok"].identifier == "tests.auth.test_auth.TestAuth.test_ok"


def test_parse_junit_accepts_a_bare_testsuite_root() -> None:
    xml = b'<testsuite name="x"><testcase classname="a.b" name="t"/></testsuite>'
    assert len(parse_junit(xml)) == 1


def test_parse_junit_rounds_rather_than_truncates_time() -> None:
    # int(0.6) == 0 loses the test entirely; round(0.6) == 1.
    xml = b'<testsuite><testcase classname="a" name="t" time="0.6"/></testsuite>'
    assert parse_junit(xml)[0].execution_time == 1


def test_parse_junit_rejects_malformed_xml() -> None:
    with pytest.raises(BadRequestError):
        parse_junit(b"<testsuite><oops>")


def test_parse_json_reads_a_result_list() -> None:
    content = b'[{"name": "t", "status": "failed", "message": "m"}]'
    parsed = result_import_service.parse_json(content)
    assert parsed[0].status == "failed"
    assert parsed[0].message == "m"


def test_parse_json_rejects_a_non_list() -> None:
    with pytest.raises(BadRequestError):
        result_import_service.parse_json(b'{"name": "t"}')


def test_parse_json_rejects_an_entry_with_no_name() -> None:
    with pytest.raises(BadRequestError):
        result_import_service.parse_json(b'[{"status": "passed"}]')


def test_detect_format_prefers_the_extension() -> None:
    assert result_import_service.detect_format("a.xml", b"[]") == "junit"
    assert result_import_service.detect_format("a.json", b"<x/>") == "json"


def test_detect_format_sniffs_when_there_is_no_extension() -> None:
    assert result_import_service.detect_format(None, b"  [{}]") == "json"
    assert result_import_service.detect_format(None, b"<testsuite/>") == "junit"


# --------------------------------------------------------------------------
# Matching, against a real run/suite/case graph.
# --------------------------------------------------------------------------


async def _fixture_run(
    db: AsyncSession, cases: list[tuple[str | None, str]]
) -> tuple[TestRun, dict[str, int], User]:
    """Build a project/suite/run plus `cases` as (automation_id, title)."""
    user = User(
        username="importer",
        email="importer@example.com",
        hashed_password="x",
        role="tester",
        is_active=True,
    )
    project = Project(name="Import project")
    db.add_all([user, project])
    await db.flush()

    suite = TestSuite(project_id=project.id, name="Suite")
    db.add(suite)
    await db.flush()

    created: dict[str, int] = {}
    for automation_id, title in cases:
        case = TestCase(
            suite_id=suite.id, title=title, automation_id=automation_id, steps=[]
        )
        db.add(case)
        await db.flush()
        created[title] = case.id

    run = TestRun(project_id=project.id, name="Run", status="planned")
    db.add(run)
    await db.flush()
    return run, created, user


async def test_matches_pytest_node_ids_via_the_dotted_rule(
    db_session: AsyncSession,
) -> None:
    """The testoria-tests case: automation_id is a node ID, XML is dotted."""
    run, cases, user = await _fixture_run(
        db_session,
        [("tests/auth/test_auth.py::TestAuth::test_ok", "Login works")],
    )
    xml = (
        b'<testsuite><testcase classname="tests.auth.test_auth.TestAuth" '
        b'name="test_ok"/></testsuite>'
    )

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.matched == 1
    assert report.matched_by == {"automation_id_dotted": 1}


async def test_automation_id_beats_title(db_session: AsyncSession) -> None:
    run, cases, user = await _fixture_run(
        db_session,
        [
            ("pkg.mod.test_x", "Some readable title"),
            (None, "pkg.mod.test_x"),  # title looks like the identifier too
        ],
    )
    xml = b'<testsuite><testcase classname="pkg.mod" name="test_x"/></testsuite>'

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.matched_by == {"automation_id": 1}
    assert report.matched == 1


async def test_matches_a_bare_name_automation_id(db_session: AsyncSession) -> None:
    run, _, user = await _fixture_run(db_session, [("test_x", "Title")])
    xml = b'<testsuite><testcase classname="pkg.mod" name="test_x"/></testsuite>'

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.matched_by == {"automation_id_name": 1}


async def test_falls_back_to_title(db_session: AsyncSession) -> None:
    run, _, user = await _fixture_run(db_session, [(None, "pkg.mod.test_x")])
    xml = b'<testsuite><testcase classname="pkg.mod" name="test_x"/></testsuite>'

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.matched_by == {"title": 1}


async def test_duplicate_automation_id_is_ambiguous_not_first_wins(
    db_session: AsyncSession,
) -> None:
    run, _, user = await _fixture_run(
        db_session, [("pkg.mod.test_x", "First"), ("pkg.mod.test_x", "Second")]
    )
    xml = b'<testsuite><testcase classname="pkg.mod" name="test_x"/></testsuite>'

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.matched == 0
    assert report.unmatched == 1
    assert report.unmatched_cases[0].reason == "ambiguous"


async def test_two_xml_entries_claiming_one_case_is_ambiguous(
    db_session: AsyncSession,
) -> None:
    """The second would silently overwrite the first, so it is reported."""
    run, _, user = await _fixture_run(db_session, [("pkg.mod.test_x", "T")])
    xml = (
        b'<testsuite><testcase classname="pkg.mod" name="test_x"/>'
        b'<testcase classname="pkg.mod" name="test_x"/></testsuite>'
    )

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.matched == 1
    assert report.unmatched == 1
    assert report.unmatched_cases[0].reason == "ambiguous"


async def test_unmatched_is_reported_not_raised(db_session: AsyncSession) -> None:
    run, _, user = await _fixture_run(db_session, [("pkg.mod.test_x", "T")])
    xml = b'<testsuite><testcase classname="pkg.mod" name="test_gone"/></testsuite>'

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.unmatched == 1
    assert report.unmatched_cases[0].reason == "no_match"
    assert report.unmatched_cases[0].identifier == "pkg.mod.test_gone"


async def test_unmatched_list_is_capped_but_the_count_is_true(
    db_session: AsyncSession,
) -> None:
    run, _, user = await _fixture_run(db_session, [("pkg.mod.test_x", "T")])
    entries = b"".join(
        f'<testcase classname="pkg.mod" name="missing_{i}"/>'.encode()
        for i in range(MAX_REPORTED_UNMATCHED + 25)
    )
    xml = b"<testsuite>" + entries + b"</testsuite>"

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.unmatched == MAX_REPORTED_UNMATCHED + 25
    assert len(report.unmatched_cases) == MAX_REPORTED_UNMATCHED


async def test_parametrized_variants_report_independently(
    db_session: AsyncSession,
) -> None:
    """One case per variant — the decision tests-repo TD-010 was waiting on."""
    run, _, user = await _fixture_run(
        db_session,
        [
            ("tests/u.py::test_roles[TESTER]", "roles TESTER"),
            ("tests/u.py::test_roles[LEAD]", "roles LEAD"),
        ],
    )
    xml = (
        b'<testsuite><testcase classname="tests.u" name="test_roles[TESTER]"/>'
        b'<testcase classname="tests.u" name="test_roles[LEAD]">'
        b'<failure message="nope">t</failure></testcase></testsuite>'
    )

    report = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert report.matched == 2
    assert report.status_counts == {"passed": 1, "failed": 1}


async def test_import_is_idempotent(db_session: AsyncSession) -> None:
    """Re-running after a network blip must not duplicate or double-count."""
    run, _, user = await _fixture_run(db_session, [("pkg.mod.test_x", "T")])
    xml = b'<testsuite><testcase classname="pkg.mod" name="test_x"/></testsuite>'

    first = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )
    second = await result_import_service.import_results(
        db_session, run.id, xml, user.id, filename="j.xml"
    )

    assert first.submitted == second.submitted == 1
    results = await test_result_service.list_results(db_session, run.id)
    assert len(results) == 1
