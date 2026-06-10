from __future__ import annotations

import logging
from email.message import EmailMessage
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def render_email(template: str, context: dict[str, Any]) -> tuple[str, str]:
    """Render an email's HTML and plaintext bodies from its template stem.

    Looks up `<template>.html` and `<template>.txt` under app/templates/email/.
    """
    html = _env.get_template(f"{template}.html").render(**context)
    text = _env.get_template(f"{template}.txt").render(**context)
    return html, text


def build_message(
    to_email: str, subject: str, html_body: str, text_body: str
) -> EmailMessage:
    """Build a multipart (plaintext + HTML) message."""
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    return msg


class EmailSender:
    """Context-managed SMTP connection reused across many sends.

    The drain worker opens one `EmailSender`, sends a whole batch over the
    single connection, then closes it — avoiding the connect→TLS→AUTH→QUIT
    churn (and Gmail throttling) of one connection per message.

    When `EMAIL_ENABLED` is False the connection is never opened and `send`
    is a logged no-op, so dev/test exercise the outbox path without hitting
    Gmail.
    """

    def __init__(self) -> None:
        self._client: aiosmtplib.SMTP | None = None

    async def __aenter__(self) -> EmailSender:
        if settings.EMAIL_ENABLED:
            self._client = aiosmtplib.SMTP(
                hostname=settings.EMAIL_SMTP_HOST,
                port=settings.EMAIL_SMTP_PORT,
                start_tls=True,
                timeout=30,
            )
            await self._client.connect()
            await self._client.login(
                settings.EMAIL_SMTP_USER, settings.EMAIL_SMTP_PASSWORD
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            try:
                await self._client.quit()
            except Exception:  # noqa: BLE001 — closing best-effort
                logger.debug("SMTP quit failed", exc_info=True)
            finally:
                self._client = None

    async def send(
        self, to_email: str, subject: str, html_body: str, text_body: str
    ) -> None:
        """Send one rendered message over the open connection.

        Raises on SMTP failure so the worker records the outcome and retries.
        """
        if not settings.EMAIL_ENABLED or self._client is None:
            logger.info(
                "EMAIL disabled — no-op send to=%s subject=%r", to_email, subject
            )
            return
        msg = build_message(to_email, subject, html_body, text_body)
        await self._client.send_message(msg)
