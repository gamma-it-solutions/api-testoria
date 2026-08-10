"""Exit codes are a contract — a pipeline gates on them.

0  clean
1  transport, auth, or usage error
2  unmatched test cases, under --strict only

`@handle_errors` converts a `CLIError` into `typer.Exit(code=...)` at the command
boundary. That indirection is deliberate: Typer 0.27 does **not** honour custom
`click.ClickException` subclasses — its `_main` re-raises them, so the pretty
exception hook prints a traceback and the process exits 1 no matter what
`exit_code` says. `typer.Exit` is the only signal Typer routes to a real exit
code, and doing the conversion per command (rather than in the console-script
wrapper) keeps the behaviour identical under `CliRunner`, so the exit codes are
actually testable.
"""

import functools
from collections.abc import Callable
from typing import Any, TypeVar

import typer

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNMATCHED = 2

F = TypeVar("F", bound=Callable[..., Any])


class CLIError(Exception):
    """Base for every error reported to the user rather than as a traceback."""

    exit_code = EXIT_ERROR


class AuthError(CLIError):
    """No usable credentials, or the server rejected them."""


class APIError(CLIError):
    """The API returned a non-2xx response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UsageError(CLIError):
    """The user's flags do not make sense together."""


class UnmatchedError(CLIError):
    """Results uploaded, but some tests had no matching case (with --strict)."""

    exit_code = EXIT_UNMATCHED


def handle_errors(func: F) -> F:
    """Report a CLIError as a message plus an exit code, never a traceback."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from testoria_cli import output

        try:
            return func(*args, **kwargs)
        except CLIError as exc:
            output.err_console.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(code=exc.exit_code) from None

    return wrapper  # type: ignore[return-value]
