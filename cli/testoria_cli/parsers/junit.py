"""Local inspection of a report file.

Parsing and matching happen server-side — the CLI does not duplicate that logic,
because two implementations of the same matching rules is how a client drifts
from its API. These helpers only exist to fail fast on an obviously wrong file
and to name automation IDs for `--attach`.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from testoria_cli.errors import UsageError


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".xml":
        return "junit"
    if suffix == ".json":
        return "json"
    head = path.read_bytes().lstrip()[:1]
    return "json" if head in (b"[", b"{") else "junit"


def count_testcases(path: Path) -> int:
    """Number of test entries in the file, for a fail-fast sanity check."""
    fmt = detect_format(path)
    content = path.read_bytes()
    if fmt == "json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise UsageError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise UsageError(f"{path} must contain a JSON list of results")
        return len(payload)
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise UsageError(f"{path} is not valid XML: {exc}") from exc
    return len(list(root.iter("testcase")))


def local_names(path: Path) -> list[str]:
    """`classname.name` for every entry — used to match `--attach` filenames."""
    if detect_format(path) != "junit":
        return []
    root = ET.fromstring(path.read_bytes())
    names = []
    for element in root.iter("testcase"):
        classname = element.get("classname")
        name = element.get("name") or ""
        if not name:
            continue
        names.append(f"{classname}.{name}" if classname else name)
    return names
