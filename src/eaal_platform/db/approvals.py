"""Approved email addresses: who is allowed to create an account on a server.

An administrator keeps two lists, one for teachers and one for students. On
a server nobody can sign up with an email that isn't on the list for their
role, so accounts stay limited to one institution's people. Entries are
added one at a time or in bulk from a CSV / Excel file (see ``importer``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.bootstrap import normalise_email
from eaal_platform.db.models import AllowedEmail, Professor, Student

ROLES = ("student", "professor")
_EMAIL_RE = re.compile(r"^[^@\s,;<>()\[\]]+@[^@\s,;<>()\[\]]+\.[^@\s,;<>()\[\]]{2,}$")
MAX_NAME_CHARS = 200
MAX_ENROLLMENT_CHARS = 50


class ApprovalError(ValueError):
    """A problem with the request, worded for the administrator."""


def check_role(role: str) -> str:
    if role not in ROLES:
        raise ApprovalError("Choose teacher or student.")
    return role


def check_email(raw: str) -> str:
    email = normalise_email(raw or "")
    if not email or len(email) > 320 or not _EMAIL_RE.match(email):
        raise ApprovalError(f"“{(raw or '').strip()}” isn't a valid email address.")
    return email


def _clean(value: str | None, limit: int, what: str) -> str | None:
    text = (value or "").strip()
    if len(text) > limit:
        raise ApprovalError(f"The {what} is too long.")
    return text or None


def _account_role(db: OrmSession, email: str) -> str | None:
    """Which kind of account already uses this email, if any."""
    if db.query(Student).filter(func.lower(Student.email) == email).first() is not None:
        return "student"
    if db.query(Professor).filter(func.lower(Professor.email) == email).first() is not None:
        return "professor"
    return None


def _label(role: str) -> str:
    return "teacher" if role == "professor" else "student"


def _row(db: OrmSession, entry: AllowedEmail) -> dict[str, Any]:
    has_account = _account_role(db, entry.email) == entry.role
    return {
        "id": entry.id,
        "role": entry.role,
        "name": entry.name,
        "email": entry.email,
        "enrollment_no": entry.enrollment_no,
        "registered": has_account,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def list_entries(session_factory: sessionmaker[OrmSession], role: str) -> list[dict[str, Any]]:
    check_role(role)
    with session_factory() as db:
        entries = db.query(AllowedEmail).filter_by(role=role).order_by(AllowedEmail.email).all()
        return [_row(db, entry) for entry in entries]


def find_approval(
    session_factory: sessionmaker[OrmSession], role: str, email: str
) -> dict[str, Any] | None:
    """The approval for this role and email, or None if sign-up isn't allowed."""
    with session_factory() as db:
        entry = db.query(AllowedEmail).filter_by(role=role, email=normalise_email(email)).first()
        return _row(db, entry) if entry is not None else None


def _add(
    db: OrmSession, role: str, name: str | None, email: str, enrollment_no: str | None
) -> AllowedEmail:
    existing = db.query(AllowedEmail).filter_by(email=email).first()
    if existing is not None:
        if existing.role == role:
            raise ApprovalError(f"{email} is already on the list.")
        raise ApprovalError(f"{email} is already on the {_label(existing.role)} list.")
    other = _account_role(db, email)
    if other is not None and other != role:
        raise ApprovalError(f"{email} already has a {_label(other)} account.")
    entry = AllowedEmail(
        role=role,
        email=email,
        name=_clean(name, MAX_NAME_CHARS, "name"),
        enrollment_no=_clean(enrollment_no, MAX_ENROLLMENT_CHARS, "enrolment number")
        if role == "student"
        else None,
    )
    db.add(entry)
    db.flush()
    return entry


def add_entry(
    session_factory: sessionmaker[OrmSession],
    role: str,
    *,
    name: str | None,
    email: str,
    enrollment_no: str | None = None,
) -> int:
    check_role(role)
    clean_email = check_email(email)
    with session_factory() as db:
        entry = _add(db, role, name, clean_email, enrollment_no)
        db.commit()
        return entry.id


def update_entry(
    session_factory: sessionmaker[OrmSession],
    entry_id: int,
    *,
    name: str | None,
    email: str,
    enrollment_no: str | None = None,
) -> None:
    """Change an entry that hasn't been used yet. Registered people are edited as accounts."""
    clean_email = check_email(email)
    with session_factory() as db:
        entry = db.get(AllowedEmail, entry_id)
        if entry is None:
            raise ApprovalError("That entry doesn't exist.")
        if _account_role(db, entry.email) == entry.role:
            raise ApprovalError("This person has registered. Edit their account on the Users page.")
        clash = db.query(AllowedEmail).filter_by(email=clean_email).first()
        if clash is not None and clash.id != entry.id:
            raise ApprovalError(f"{clean_email} is already on the list.")
        other = _account_role(db, clean_email)
        if other is not None:
            raise ApprovalError(f"{clean_email} already has a {_label(other)} account.")
        entry.email = clean_email
        entry.name = _clean(name, MAX_NAME_CHARS, "name")
        if entry.role == "student":
            entry.enrollment_no = _clean(enrollment_no, MAX_ENROLLMENT_CHARS, "enrolment number")
        db.commit()


def delete_entry(session_factory: sessionmaker[OrmSession], entry_id: int) -> str:
    """Remove an approval; returns the email. Any account that exists is left alone."""
    with session_factory() as db:
        entry = db.get(AllowedEmail, entry_id)
        if entry is None:
            raise ApprovalError("That entry doesn't exist.")
        email = entry.email
        db.delete(entry)
        db.commit()
        return email


def follow_email_change(db: OrmSession, old_email: str, new_email: str) -> None:
    """When an account's email is edited, move its approval along with it."""
    entry = db.query(AllowedEmail).filter_by(email=normalise_email(old_email)).first()
    if (
        entry is not None
        and db.query(AllowedEmail).filter_by(email=normalise_email(new_email)).first() is None
    ):
        entry.email = normalise_email(new_email)


@dataclass
class ImportReport:
    """What happened to each row of an uploaded file."""

    added: int = 0
    already_listed: int = 0
    problems: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "already_listed": self.already_listed,
            "problems": self.problems,
        }


def import_entries(
    session_factory: sessionmaker[OrmSession], role: str, rows: list[dict[str, Any]]
) -> ImportReport:
    """Add every valid row; list the rest with the reason (one bad row never blocks the others)."""
    check_role(role)
    report = ImportReport()
    with session_factory() as db:
        seen: set[str] = set()
        for row in rows:
            number = row.get("row")
            raw_email = str(row.get("email") or "")
            try:
                email = check_email(raw_email)
                if email in seen:
                    report.already_listed += 1
                    continue
                seen.add(email)
                existing = db.query(AllowedEmail).filter_by(email=email).first()
                if existing is not None and existing.role == role:
                    report.already_listed += 1
                    continue
                with db.begin_nested():
                    _add(db, role, row.get("name"), email, row.get("enrollment_no"))
                report.added += 1
            except ApprovalError as exc:
                report.problems.append(
                    {"row": number, "email": raw_email.strip(), "error": str(exc)}
                )
        db.commit()
    return report
