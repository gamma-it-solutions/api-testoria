"""Top-level app wiring.

`--version` regressed once: it was read in the group callback body, but Click
rejects a missing subcommand during parsing, so it failed with "Missing command"
(exit 2) before the body ran. The CI smoke step caught it; these tests exist so
the test suite catches it next time.
"""

import pytest
from typer.testing import CliRunner

from testoria_cli import __version__
from testoria_cli.main import app

runner = CliRunner()


def test_version_exits_zero_with_no_subcommand() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert __version__ in result.output


def test_version_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """It must work on a machine that has never been configured."""
    monkeypatch.delenv("TESTORIA_URL", raising=False)
    monkeypatch.delenv("TESTORIA_API_KEY", raising=False)

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("upload", "whoami", "auth", "run", "case"):
        assert command in result.output


def test_bare_invocation_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "upload" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["upload", "--help"],
        ["auth", "--help"],
        ["run", "--help"],
        ["case", "--help"],
        ["whoami", "--help"],
        ["run", "create", "--help"],
        ["case", "list", "--help"],
    ],
)
def test_every_command_help_works(args: list[str]) -> None:
    """A command whose options fail to build only shows up when help renders."""
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
