import pytest

from app.core import email
from app.core.email import EmailSender, build_message, render_email


def test_render_email_includes_link_in_both_bodies() -> None:
    ctx = {
        "username": "jdoe",
        "full_name": "Jane Doe",
        "link": "http://front/set-password?token=abc123",
    }
    html, text = render_email("welcome_invite", ctx)
    assert "abc123" in html
    assert "abc123" in text
    assert "Jane Doe" in html  # full_name preferred over username
    assert "<html" in html.lower()


def test_render_email_falls_back_to_username() -> None:
    ctx = {"username": "jdoe", "full_name": None, "link": "http://x/y?token=t"}
    html, text = render_email("password_reset", ctx)
    assert "jdoe" in html
    assert "jdoe" in text


def test_build_message_is_multipart() -> None:
    msg = build_message("to@x.com", "Subj", "<p>hi</p>", "hi")
    assert msg["To"] == "to@x.com"
    assert msg["Subject"] == "Subj"
    assert msg.is_multipart()
    types = {part.get_content_type() for part in msg.walk()}
    assert "text/plain" in types
    assert "text/html" in types


@pytest.mark.asyncio
async def test_sender_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # EMAIL_ENABLED is False by default — opening the sender must not attempt a
    # network connection, and send() returns without raising.
    monkeypatch.setattr(email.settings, "EMAIL_ENABLED", False)
    async with EmailSender() as sender:
        await sender.send("to@x.com", "Subj", "<p>hi</p>", "hi")  # no-op, no error
