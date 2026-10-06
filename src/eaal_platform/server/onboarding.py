"""A student's first sign-in by email.

Instead of inventing a password, the student types their (approved) email; the
server creates the account with a temporary password and emails it. They sign
in with it and must choose their own password straight away. Receiving the
email is also what proves the address is theirs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from eaal_platform import mailer
from eaal_platform.auth import generate_temporary_password, hash_password, password_problem
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


# -- forgotten passwords: a code by email -----------------------------------------------------

RESET_CODE_MINUTES = 15
RESET_CODE_ATTEMPTS = 5
_RESET_SENT = (
    "If that email has an account, we've sent a 6-digit code to it. It works for 15 minutes."
)


class ResetCodes:
    """Codes waiting to be used. Kept in memory (hashed), so a server restart voids them."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(salt: str, code: str) -> str:
        return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()

    def issue(self, email: str) -> str:
        code = f"{secrets.randbelow(10**6):06d}"
        salt = secrets.token_hex(8)
        with self._lock:
            self._pending[email] = {
                "salt": salt,
                "digest": self._digest(salt, code),
                "expires": self._clock() + RESET_CODE_MINUTES * 60,
                "attempts": 0,
            }
        return code

    def check(self, email: str, code: str) -> bool:
        """True (once) for the right code. Wrong guesses are counted; too many kill the code."""
        with self._lock:
            entry = self._pending.get(email)
            if entry is None or self._clock() > entry["expires"]:
                self._pending.pop(email, None)
                return False
            entry["attempts"] += 1
            if entry["attempts"] > RESET_CODE_ATTEMPTS:
                del self._pending[email]
                return False
            good = hmac.compare_digest(entry["digest"], self._digest(entry["salt"], code.strip()))
            if good:
                del self._pending[email]
            return good


def _find_account(factory: Any, email: str) -> tuple[str, int, str, bool] | None:
    """(role, id, name, disabled) of whoever has this email, if anyone."""
    with factory() as db:
        for role, model in (("student", Student), ("professor", Professor)):
            row = db.query(model).filter(model.email == email).first()
            if row is not None:
                return role, row.id, row.display_name, bool(row.disabled)
    return None


def request_password_reset(
    state: Any,
    email: str,
    computer: str,
    *,
    sender: Callable[..., None] = mailer.send_mail,
) -> dict[str, Any]:
    """Email a one-time code to the address of an existing account (students and teachers)."""
    email = normalise_email(email)
    if "@" not in email or len(email) > 320:
        raise LoginRequestError("Enter the email address of your account.")
    settings = mailer.load_settings(state.session_factory)
    if settings is None:
        raise LoginRequestError("Email isn't set up on this server yet. Ask your administrator.")
    state.login_throttle.check(email, computer)
    account = _find_account(state.session_factory, email)
    if account is not None and not account[3]:
        code = state.reset_codes.issue(email)
        body = (
            f"Hello {account[2]},\n\n"
            "Someone asked to reset the password of your CAVY account.\n\n"
            f"    Your code:  {code}\n\n"
            f"Enter it in CAVY within {RESET_CODE_MINUTES} minutes and choose a new password. "
            "If it wasn't you, ignore this email: your password has not changed.\n\n"
            "CAVY Team\n"
        )
        try:
            sender(settings, email, "Your CAVY password reset code", body)
        except mailer.MailError as exc:
            raise LoginRequestError(
                "The email couldn't be sent. Tell your administrator (the Email page in the "
                "admin panel shows what is wrong)."
            ) from exc
        state.audit(f"{account[0]}:{email}", "password_reset_code_sent")
    # The same answer whether or not the address has an account.
    return {"ok": True, "message": _RESET_SENT}


def confirm_password_reset(state: Any, email: str, code: str, new_password: str) -> dict[str, Any]:
    """Set a new password, but only for someone holding the code that was emailed."""
    email = normalise_email(email)
    problem = password_problem(new_password)
    if problem:
        raise LoginRequestError(problem)
    wrong = LoginRequestError("That code isn't right, or it has expired. Ask for a new one.")
    account = _find_account(state.session_factory, email)
    if account is None or account[3] or not state.reset_codes.check(email, code):
        raise wrong
    role, user_id = account[0], account[1]
    with state.session_factory() as db:
        row = db.get(Student if role == "student" else Professor, user_id)
        assert row is not None
        row.password_hash = hash_password(new_password)
        row.must_change_password = False
        db.commit()
    state.revoke_user(role, user_id, "Your password was reset.")
    state.audit(f"{role}:{email}", "password_reset_by_code")
    return {"ok": True, "message": "Your password has been changed. You can log in now."}
