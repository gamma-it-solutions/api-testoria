from pathlib import Path

import pytest

from testoria_cli.errors import UsageError
from testoria_cli.parsers.junit import count_testcases, detect_format, local_names


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_detect_format_from_extension(tmp_path: Path) -> None:
    assert detect_format(_write(tmp_path, "a.xml", b"<x/>")) == "junit"
    assert detect_format(_write(tmp_path, "a.json", b"[]")) == "json"


def test_detect_format_sniffs_without_an_extension(tmp_path: Path) -> None:
    assert detect_format(_write(tmp_path, "report", b"  [{}]")) == "json"
    assert detect_format(_write(tmp_path, "report", b"<testsuite/>")) == "junit"


def test_counts_nested_testsuites(tmp_path: Path) -> None:
    xml = (
        b"<testsuites><testsuite>"
        b'<testcase classname="a" name="one"/>'
        b'<testcase classname="a" name="two"/>'
        b"</testsuite></testsuites>"
    )
    assert count_testcases(_write(tmp_path, "j.xml", xml)) == 2


def test_counts_a_bare_testsuite(tmp_path: Path) -> None:
    xml = b'<testsuite><testcase classname="a" name="one"/></testsuite>'
    assert count_testcases(_write(tmp_path, "j.xml", xml)) == 1


def test_counts_json_entries(tmp_path: Path) -> None:
    payload = b'[{"name": "a"}, {"name": "b"}]'
    assert count_testcases(_write(tmp_path, "r.json", payload)) == 2


def test_empty_report_counts_zero(tmp_path: Path) -> None:
    assert count_testcases(_write(tmp_path, "j.xml", b"<testsuite/>")) == 0


def test_malformed_xml_raises_usage_error(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="not valid XML"):
        count_testcases(_write(tmp_path, "j.xml", b"<testsuite><oops>"))


def test_malformed_json_raises_usage_error(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="not valid JSON"):
        count_testcases(_write(tmp_path, "r.json", b"{oops"))


def test_json_object_instead_of_list_raises(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="JSON list"):
        count_testcases(_write(tmp_path, "r.json", b'{"name": "a"}'))


def test_local_names_builds_dotted_identifiers(tmp_path: Path) -> None:
    xml = (
        b"<testsuite>"
        b'<testcase classname="tests.auth.test_auth.TestAuth" name="test_ok"/>'
        b'<testcase classname="tests.auth.test_auth" name="test_p[LEAD]"/>'
        b"</testsuite>"
    )
    names = local_names(_write(tmp_path, "j.xml", xml))

    assert names == [
        "tests.auth.test_auth.TestAuth.test_ok",
        "tests.auth.test_auth.test_p[LEAD]",
    ]


def test_local_names_handles_a_missing_classname(tmp_path: Path) -> None:
    xml = b'<testsuite><testcase name="bare"/></testsuite>'
    assert local_names(_write(tmp_path, "j.xml", xml)) == ["bare"]
