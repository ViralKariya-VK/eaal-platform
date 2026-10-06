"""A student's first sign-in by email.

Instead of inventing a password, the student types their (approved) email; the
server creates the account with a temporary password and emails it. They sign
in with it and must choose their own password straight away. Receiving the
email is also what proves the address is theirs.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from eaal_platform import mailer
from eaal_platform.auth import generate_temporary_password
from eaal_platform.db import approvals
from eaal_platform.db.bootstrap import (
    create_student_account,
    normalise_email,
    reset_student_password,
)
from eaal_platform.db.models import Professor, Student

MIN_SECONDS_BETWEEN_EMAILS = 60
MAX_PER_ADDRESS_PER_HOUR = 5
MAX_PER_COMPUTER_PER_HOUR = 20


class LoginRequestError(ValueError):
    """The request can't be carried out; the message is for the person who made it."""


class Throttle:
    """So nobody can use the form to flood an inbox."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._by_email: dict[str, list[float]] = defaultdict(list)
        self._by_computer: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, email: str, computer: str) -> None:
        now = self._clock()
        with self._lock:
            mine = [t for t in self._by_email[email] if t > now - 3600]
            theirs = [t for t in self._by_computer[computer] if t > now - 3600]
            self._by_email[email], self._by_computer[computer] = mine, theirs
            if mine and now - mine[-1] < MIN_SECONDS_BETWEEN_EMAILS:
                raise LoginRequestError(
                    "An email was just sent. Check your inbox (and spam) and try again in a minute."
                )
            if len(mine) >= MAX_PER_ADDRESS_PER_HOUR or len(theirs) >= MAX_PER_COMPUTER_PER_HOUR:
                raise LoginRequestError("Too many requests. Please try again later.")
            mine.append(now)
            theirs.append(now)


def _body(name: str, email: str, password: str, server_hint: str) -> str:
    return (
        f"Hello {name},\n\n"
        "Your CAVY account is ready. Here are your first login details:\n\n"
        f"    Username:            {email}\n"
        f"    Temporary password:  {password}\n\n"
        "Open CAVY, choose Student, and log in with them. You will be asked to choose "
        "your own password straight away; the temporary one stops working after that.\n"
        f"{server_hint}\n"
        "If you did not ask for this, you can ignore this email.\n\n"
        "CAVY Team\n"
    )


def request_first_login(
    state: Any,
    email: str,
    computer: str,
    *,
    sender: Callable[..., None] = mailer.send_mail,
    throttle: Throttle | None = None,
) -> dict[str, Any]:
    """Create (or reset, if never used) the student's account and email the login details."""
    factory = state.session_factory
    email = normalise_email(email)
    if "@" not in email or len(email) > 320:
        raise LoginRequestError("Enter your university email address.")
    settings = mailer.load_settings(factory)
    if settings is None:
        raise LoginRequestError("Email isn't set up on this server yet. Ask your administrator.")
    approval = approvals.find_approval(factory, "student", email)
    if approval is None:
        raise LoginRequestError(
            "That email isn't on the approved list. Check the spelling, or ask your administrator."
        )
    (throttle or state.login_throttle).check(email, computer)

    with factory() as db:
        student = db.query(Student).filter(Student.email == email).first()
        if student is None and db.query(Professor).filter(Professor.email == email).first():
            raise LoginRequestError("That email belongs to a teacher account.")
        existing = (student.id, student.must_change_password, student.disabled) if student else None
    created = False
    if existing is None:
        password = generate_temporary_password(10)
        student_id = create_student_account(
            factory,
            display_name=approval["name"] or email.split("@")[0],
            email=email,
            password=password,
            enrollment_no=approval["enrollment_no"],
        )
        with factory() as db:
            row = db.get(Student, student_id)
            assert row is not None
            row.must_change_password = True
            db.commit()
        created = True
    else:
        student_id, must_change, disabled = existing
        if disabled:
            raise LoginRequestError("This account is disabled. Ask your administrator.")
        if not must_change:
            raise LoginRequestError(
                "This account is already set up. Log in with your password; if you forgot "
                "it, ask your teacher or administrator to reset it."
            )
        password = reset_student_password(factory, student_id)  # never used: send a fresh one

    name = approval["name"] or email.split("@")[0]
    try:
        sender(
            settings,
            email,
            "Your CAVY login",
            _body(name, email, password, getattr(state, "server_hint", "")),
        )
    except mailer.MailError as exc:
        if created:
            with factory() as db:
                row = db.get(Student, student_id)
                if row is not None:
                    db.delete(row)
                    db.commit()
        raise LoginRequestError(
            "The email couldn't be sent. Tell your administrator (they can check the "
            "Email page in the admin panel)."
        ) from exc
    state.audit(f"student:{email}", "first_login_email", "created" if created else "resent")
    return {
        "ok": True,
        "created": created,
        "message": f"We've emailed your login details to {email}. Check your inbox (and spam).",
    }
