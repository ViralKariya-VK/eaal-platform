"""Sending email from the server (login details for students' first sign-in).

Uses ordinary SMTP, so any mail account works; for a classroom the simplest is
a dedicated Gmail account with an "app password". The settings live in the
database and are edited from the admin panel.
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.models import EmailSettings

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587
SMTP_TIMEOUT_SECONDS = 20
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


class MailError(Exception):
    """Sending failed; the message is fit to show to the administrator."""


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    password: str
    from_name: str


def load_settings(session_factory: sessionmaker[OrmSession]) -> MailSettings | None:
    """The saved settings, or None until an administrator has filled them in."""
    with session_factory() as db:
        row = db.query(EmailSettings).first()
        if row is None or not row.username or not row.password:
            return None
        return MailSettings(row.host, row.port, row.username, row.password, row.from_name)


def describe(session_factory: sessionmaker[OrmSession]) -> dict[str, Any]:
    """What the admin panel may see: everything except the password."""
    with session_factory() as db:
        row = db.query(EmailSettings).first()
        if row is None:
            return {
                "host": DEFAULT_HOST,
                "port": DEFAULT_PORT,
                "username": "",
                "from_name": "CAVY Team",
                "has_password": False,
                "configured": False,
            }
        return {
            "host": row.host,
            "port": row.port,
            "username": row.username,
            "from_name": row.from_name,
            "has_password": bool(row.password),
            "configured": bool(row.username and row.password),
        }


def save_settings(
    session_factory: sessionmaker[OrmSession],
    *,
    host: str,
    port: int,
    username: str,
    password: str | None,
    from_name: str,
) -> None:
    """Store the settings. A blank password keeps the one already saved."""
    host, username, from_name = host.strip(), username.strip(), from_name.strip()
    if not host or not username:
        raise MailError("Enter the mail server and the account's email address.")
    if not 1 <= int(port) <= 65535:
        raise MailError("The port must be a number such as 587.")
    with session_factory() as db:
        row = db.query(EmailSettings).first()
        if row is None:
            row = EmailSettings()
            db.add(row)
        row.host, row.port, row.username = host, int(port), username
        row.from_name = from_name or "CAVY Team"
        if password:
            # Google shows app passwords in groups of four; the spaces aren't part of it.
            row.password = password.replace(" ", "")
        if not row.password:
            raise MailError("Enter the app password for that account.")
        db.commit()


SmtpFactory = Callable[[str, int], Any]


def send_mail(
    settings: MailSettings,
    to: str,
    subject: str,
    body: str,
    smtp_factory: SmtpFactory | None = None,
) -> None:
    """Send one plain-text email, or raise ``MailError``."""
    message = EmailMessage()
    message["From"] = formataddr((settings.from_name, settings.username))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        if settings.port == 465:
            factory = smtp_factory or (
                lambda host, port: smtplib.SMTP_SSL(
                    host, port, timeout=SMTP_TIMEOUT_SECONDS, context=ssl.create_default_context()
                )
            )
            with factory(settings.host, settings.port) as smtp:
                smtp.login(settings.username, settings.password)
                smtp.send_message(message)
            return
        factory = smtp_factory or (
            lambda host, port: smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
        )
        with factory(settings.host, settings.port) as smtp:
            if settings.host in _LOCAL_HOSTS:
                # A test mail catcher on this computer: no encryption or login to do.
                smtp.send_message(message)
                return
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(settings.username, settings.password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "The mail server refused the address or app password. For Gmail, use an "
            "app password (Google Account > Security > App passwords), not the normal one."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(f"The mail server refused the recipient {to}.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Couldn't send the email ({exc.__class__.__name__}: {exc}).") from exc
