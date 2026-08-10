from typing import Any

import typer

from testoria_cli import __version__
from testoria_cli import config as config_module
from testoria_cli.client import TestoriaClient
from testoria_cli.commands import auth, cases, keys, runs
from testoria_cli.commands.upload import upload as upload_command
from testoria_cli.errors import handle_errors

app = typer.Typer(
    name="testoria",
    help="Push automated test results into Testoria.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(auth.app, name="auth")
app.add_typer(runs.app, name="run")
app.add_typer(cases.app, name="case")
app.add_typer(keys.app, name="key")


class _LazyContext(dict[str, Any]):
    """Context that builds the API client on first use, not up front.

    Click runs the group callback *before* the subcommand parses its own
    arguments, so constructing the client here would make `testoria upload
    --help` fail with "no credentials" on a machine that has never been
    configured — help must work before setup, not after. Commands still just
    read `ctx.obj["client"]`.
    """

    def __missing__(self, key: str) -> Any:
        if key not in ("client", "jwt_client"):
            raise KeyError(key)
        # `jwt_client` refuses the API key: /api-keys is JWT-only server-side.
        client = TestoriaClient(self["credentials"], prefer_jwt=key == "jwt_client")
        self[key] = client
        return client


def _version_callback(value: bool) -> None:
    """Print the version and exit, during argument parsing.

    This must be a *parameter* callback, not a check in the group callback body:
    Click rejects a missing subcommand while parsing, so `testoria --version`
    would fail with "Missing command" (exit 2) before the body ever ran. An
    eager parameter callback fires early enough to pre-empt that.
    """
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
@handle_errors
def main(
    ctx: typer.Context,
    url: str | None = typer.Option(None, "--url", help="Testoria base URL"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="API key (prefer the TESTORIA_API_KEY env var)"
    ),
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the CLI version and exit",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    # Resolving credentials is cheap and never raises — only *using* them can,
    # which is why the client is built on demand (see _LazyContext). That also
    # lets `auth login/logout/status` run before any credentials exist.
    credentials = config_module.resolve(url=url, api_key=api_key)
    ctx.obj = _LazyContext(credentials=credentials, url=credentials.url)


app.command("upload")(upload_command)
app.command("whoami")(auth.whoami)


def run() -> None:
    """Console-script entry point.

    Deliberately thin: error-to-exit-code conversion is done by
    `errors.handle_errors` on each command, so the codes behave identically
    here, under `python -m`, and under a test runner.
    """
    app()


if __name__ == "__main__":
    run()
