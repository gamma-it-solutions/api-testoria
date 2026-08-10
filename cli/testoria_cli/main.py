import typer

from testoria_cli import __version__
from testoria_cli import config as config_module
from testoria_cli.client import TestoriaClient
from testoria_cli.commands import auth, cases, runs
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


@app.callback()
@handle_errors
def main(
    ctx: typer.Context,
    url: str | None = typer.Option(None, "--url", help="Testoria base URL"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="API key (prefer the TESTORIA_API_KEY env var)"
    ),
    version: bool = typer.Option(False, "--version", is_eager=True),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()

    ctx.ensure_object(dict)
    # `auth login/logout/status` run before credentials exist, so the client is
    # built lazily rather than in this callback.
    if ctx.invoked_subcommand == "auth":
        return
    credentials = config_module.resolve(url=url, api_key=api_key)
    ctx.obj["credentials"] = credentials
    ctx.obj["url"] = credentials.url
    ctx.obj["client"] = TestoriaClient(credentials)


app.command("upload")(upload_command)
app.command("whoami")(auth.whoami)


def run() -> None:
    """Console-script entry point.

    Error-to-exit-code conversion lives in `errors.CLIError` (a
    `click.ClickException`), so it behaves identically here, under `python -m`,
    and under a test runner.
    """
    app()


if __name__ == "__main__":
    run()
