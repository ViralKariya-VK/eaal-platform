"""Classes (which students belong to which professor) and shared resources.

All permission rules live here, in one place, so the desktop app, the server
and the admin panel enforce the same ones:

- A student is in at most one professor's class. A professor can add students
  who are not in anyone's class yet, and can remove their own; only an admin
  can move a student between professors.
- A resource belongs to the professor who made it. It is visible to that
  professor and to students in that professor's class: all of them, or only
  the ones chosen. A professor can only choose students from their own class.
- A resource can be attached to labs; there it is shown to the same people.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.models import (
    Professor,
    Resource,
    ResourceFile,
    ResourceKind,
    ResourceLab,
    ResourceStudent,
    Student,
    Task,
)

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_TITLE_CHARS = 300
MAX_NOTE_CHARS = 20_000
MAX_URL_CHARS = 2000

# Files a student could run by opening them: refused outright.
BLOCKED_EXTENSIONS = frozenset(
    {
        "exe", "msi", "bat", "cmd", "com", "scr", "pif", "ps1", "psm1", "vbs", "vbe", "wsf",
        "hta", "jar", "app", "dmg", "pkg", "command", "sh", "lnk", "reg", "dll", "cpl", "gadget",
        "js", "jse", "msp", "appx", "apk",
    }
)  # fmt: skip


class ResourceError(ValueError):
    """A problem with the request, worded for the person who made it."""


def clean_filename(name: str) -> str:
    """The bare file name, with any folder part and odd characters removed."""
    base = PureWindowsPath(PurePosixPath(name).name).name.strip().strip(".")
    safe = "".join(c for c in base if c.isprintable() and c not in '<>:"/\\|?*')
    return safe or "file"


_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_HOST_PORT_RE = re.compile(r"^[^/?#:]+:\d+([/?#]|$)")  # "example.com:8080/page", not a scheme


def _check_url(url: str) -> str:
    """A safe web address: http(s) only. A bare "example.com/page" becomes https."""
    url = url.strip()
    if not url or len(url) > MAX_URL_CHARS:
        raise ResourceError("Enter a web address.")
    if "://" not in url:
        if _SCHEME_RE.match(url) and not _HOST_PORT_RE.match(url):
            # javascript:, data:, mailto:, ... are never allowed.
            raise ResourceError("Links must start with http:// or https://.")
        url = f"https://{url}"
    parts = urlsplit(url)
    try:
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError if the port isn't a number
    except ValueError:
        raise ResourceError("That doesn't look like a web address.") from None
    if parts.scheme not in ("http", "https") or not host:
        raise ResourceError("Links must start with http:// or https://.")
    return parts.geturl()


# -- classes ------------------------------------------------------------------------------------


def _student_row(student: Student, professor_name: str | None = None) -> dict[str, Any]:
    return {
        "id": student.id,
        "name": student.display_name,
        "email": student.email,
        "enrollment_no": student.enrollment_no,
        "must_change_password": student.must_change_password,
        "professor_id": student.professor_id,
        "professor_name": professor_name,
    }


def class_students(
    session_factory: sessionmaker[OrmSession], professor_id: int
) -> list[dict[str, Any]]:
    with session_factory() as db:
        rows = (
            db.query(Student)
            .filter(Student.professor_id == professor_id, Student.email.is_not(None))
            .order_by(Student.display_name)
            .all()
        )
        return [_student_row(s) for s in rows]


def unassigned_students(session_factory: sessionmaker[OrmSession]) -> list[dict[str, Any]]:
    """Students nobody has claimed yet (the only ones a professor may add)."""
    with session_factory() as db:
        rows = (
            db.query(Student)
            .filter(Student.professor_id.is_(None), Student.email.is_not(None))
            .order_by(Student.display_name)
            .all()
        )
        return [_student_row(s) for s in rows]


def add_to_class(
    session_factory: sessionmaker[OrmSession], professor_id: int, student_ids: list[int]
) -> int:
    """Put unassigned students into this professor's class; returns how many were added."""
    added = 0
    with session_factory() as db:
        for student_id in dict.fromkeys(student_ids):
            student = db.get(Student, student_id)
            if student is None:
                raise ResourceError("That student doesn't exist.")
            if student.professor_id == professor_id:
                continue
            if student.professor_id is not None:
                raise ResourceError(
                    f"{student.display_name} is already in another professor's class. "
                    "Ask an administrator to move them."
                )
            student.professor_id = professor_id
            added += 1
        db.commit()
    return added


def remove_from_class(
    session_factory: sessionmaker[OrmSession], professor_id: int, student_id: int
) -> None:
    with session_factory() as db:
        student = db.get(Student, student_id)
        if student is None or student.professor_id != professor_id:
            raise ResourceError("That student isn't in your class.")
        student.professor_id = None
        _drop_student_shares(db, professor_id, student_id)
        db.commit()


def assign_student(
    session_factory: sessionmaker[OrmSession], student_id: int, professor_id: int | None
) -> None:
    """Admin: put a student in a professor's class (or take them out of any class)."""
    with session_factory() as db:
        student = db.get(Student, student_id)
        if student is None:
            raise ResourceError("No such student.")
        if professor_id is not None and db.get(Professor, professor_id) is None:
            raise ResourceError("No such professor.")
        previous = student.professor_id
        student.professor_id = professor_id
        if previous is not None and previous != professor_id:
            _drop_student_shares(db, previous, student_id)
        db.commit()


def _drop_student_shares(db: OrmSession, professor_id: int, student_id: int) -> None:
    """When a student leaves a class, individual shares from that professor end too."""
    own = select(Resource.id).where(Resource.professor_id == professor_id)
    for link in db.query(ResourceStudent).filter(
        ResourceStudent.student_id == student_id, ResourceStudent.resource_id.in_(own)
    ):
        db.delete(link)


# -- resources ----------------------------------------------------------------------------------


@dataclass
class ResourceInput:
    kind: str
    title: str
    description: str | None = None
    url: str | None = None
    body: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    data: bytes | None = None
    audience_all: bool = True
    student_ids: list[int] | None = None
    task_ids: list[int] | None = None


def _validated_kind(raw: str) -> ResourceKind:
    try:
        return ResourceKind(raw)
    except ValueError:
        raise ResourceError("Choose what kind of resource this is.") from None


def _apply(
    db: OrmSession,
    resource: Resource,
    professor_id: int,
    spec: ResourceInput,
    *,
    creating: bool,
) -> None:
    kind = _validated_kind(spec.kind)
    title = spec.title.strip()
    if not title:
        raise ResourceError("Give the resource a title.")
    if len(title) > MAX_TITLE_CHARS:
        raise ResourceError("That title is too long.")
    if not creating and kind != resource.kind:
        raise ResourceError("A resource can't change type. Add a new one instead.")

    resource.kind = kind
    resource.title = title
    resource.description = (spec.description or "").strip() or None

    if kind is ResourceKind.LINK:
        resource.url = _check_url(spec.url or "")
    elif kind is ResourceKind.NOTE:
        body = (spec.body or "").strip()
        if not body:
            raise ResourceError("Write the instructions.")
        if len(body) > MAX_NOTE_CHARS:
            raise ResourceError("That text is too long. Attach it as a file instead.")
        resource.body = body
    else:  # FILE
        if spec.data is None and creating:
            raise ResourceError("Choose a file to upload.")
        if spec.data is not None:
            name = clean_filename(spec.filename or "")
            if name.rsplit(".", 1)[-1].lower() in BLOCKED_EXTENSIONS and "." in name:
                raise ResourceError(
                    "That type of file can't be shared, because opening it could run a program."
                )
            if len(spec.data) == 0:
                raise ResourceError("That file is empty.")
            if len(spec.data) > MAX_FILE_BYTES:
                raise ResourceError(
                    f"That file is too large. The limit is {MAX_FILE_BYTES // (1024 * 1024)} MB."
                )
            resource.filename = name
            resource.mime_type = (spec.mime_type or "application/octet-stream")[:150]
            resource.size_bytes = len(spec.data)

    resource.audience_all = spec.audience_all
    if creating:
        db.add(resource)
        db.flush()

    # File bytes
    if kind is ResourceKind.FILE and spec.data is not None:
        stored = db.query(ResourceFile).filter_by(resource_id=resource.id).first()
        if stored is None:
            db.add(ResourceFile(resource_id=resource.id, data=spec.data))
        else:
            stored.data = spec.data

    # Audience: only students from this professor's own class.
    for share in db.query(ResourceStudent).filter_by(resource_id=resource.id):
        db.delete(share)
    if not spec.audience_all:
        chosen = list(dict.fromkeys(spec.student_ids or []))
        if not chosen:
            raise ResourceError("Choose at least one student, or share with your whole class.")
        for student_id in chosen:
            student = db.get(Student, student_id)
            if student is None or student.professor_id != professor_id:
                raise ResourceError("You can only share with students in your own class.")
            db.add(ResourceStudent(resource_id=resource.id, student_id=student_id))

    # Labs
    for link in db.query(ResourceLab).filter_by(resource_id=resource.id):
        db.delete(link)
    for task_id in dict.fromkeys(spec.task_ids or []):
        task = db.get(Task, task_id)
        if task is None or task.assessment_kind is not None or not task.stages:
            raise ResourceError("You can only attach a resource to a lab.")
        db.add(ResourceLab(resource_id=resource.id, task_id=task_id))


def create_resource(
    session_factory: sessionmaker[OrmSession], professor_id: int, spec: ResourceInput
) -> int:
    with session_factory() as db:
        resource = Resource(professor_id=professor_id, kind=_validated_kind(spec.kind), title="x")
        _apply(db, resource, professor_id, spec, creating=True)
        db.commit()
        return resource.id


def update_resource(
    session_factory: sessionmaker[OrmSession],
    professor_id: int,
    resource_id: int,
    spec: ResourceInput,
) -> None:
    with session_factory() as db:
        resource = _owned(db, professor_id, resource_id)
        _apply(db, resource, professor_id, spec, creating=False)
        db.commit()


def delete_resource(
    session_factory: sessionmaker[OrmSession], professor_id: int, resource_id: int
) -> str:
    """Delete a resource and everything attached to it; returns its title."""
    with session_factory() as db:
        resource = _owned(db, professor_id, resource_id)
        title = resource.title
        _delete_with_children(db, resource)
        db.commit()
        return title


def delete_resource_row(db: OrmSession, resource: Resource) -> None:
    """Delete one resource and everything attached to it (no ownership check: admin use)."""
    _delete_with_children(db, resource)


def _delete_with_children(db: OrmSession, resource: Resource) -> None:
    for model in (ResourceFile, ResourceLab, ResourceStudent):
        for row in db.query(model).filter_by(resource_id=resource.id):
            db.delete(row)
    db.flush()  # the rows pointing at the resource must go before it does
    db.delete(resource)


def delete_professor_resources(db: OrmSession, professor_id: int) -> int:
    """Remove all of a professor's resources (used when their account is deleted)."""
    resources = db.query(Resource).filter_by(professor_id=professor_id).all()
    for resource in resources:
        _delete_with_children(db, resource)
    return len(resources)


def _owned(db: OrmSession, professor_id: int, resource_id: int) -> Resource:
    resource = db.get(Resource, resource_id)
    if resource is None or resource.professor_id != professor_id:
        # Same message for "missing" and "someone else's": don't confirm it exists.
        raise ResourceError("That resource doesn't exist.")
    return resource


def _summary(db: OrmSession, resource: Resource, *, detail: bool) -> dict[str, Any]:
    labs = [
        {"id": task.id, "title": task.title}
        for task in db.query(Task)
        .join(ResourceLab, ResourceLab.task_id == Task.id)
        .filter(ResourceLab.resource_id == resource.id)
        .order_by(Task.title)
    ]
    owner = db.get(Professor, resource.professor_id)
    row: dict[str, Any] = {
        "id": resource.id,
        "kind": resource.kind.value,
        "title": resource.title,
        "description": resource.description,
        "url": resource.url,
        "body": resource.body,
        "filename": resource.filename,
        "size_bytes": resource.size_bytes,
        "owner": owner.display_name if owner else None,
        "labs": labs,
        "created_at": resource.created_at.isoformat() if resource.created_at else None,
        "updated_at": resource.updated_at.isoformat() if resource.updated_at else None,
    }
    if detail:  # the owner also sees who it is shared with
        row["audience_all"] = resource.audience_all
        row["student_ids"] = [
            link.student_id for link in db.query(ResourceStudent).filter_by(resource_id=resource.id)
        ]
        row["task_ids"] = [lab["id"] for lab in labs]
    return row


def professor_resources(
    session_factory: sessionmaker[OrmSession], professor_id: int, task_id: int | None = None
) -> list[dict[str, Any]]:
    with session_factory() as db:
        query = db.query(Resource).filter(Resource.professor_id == professor_id)
        if task_id is not None:
            query = query.join(ResourceLab, ResourceLab.resource_id == Resource.id).filter(
                ResourceLab.task_id == task_id
            )
        return [_summary(db, r, detail=True) for r in query.order_by(Resource.id.desc())]


def _visible_to_student(db: OrmSession, resource: Resource, student: Student) -> bool:
    if student.professor_id is None or student.professor_id != resource.professor_id:
        return False
    if resource.audience_all:
        return True
    return (
        db.query(ResourceStudent).filter_by(resource_id=resource.id, student_id=student.id).first()
        is not None
    )


def student_resources(
    session_factory: sessionmaker[OrmSession], student_id: int, task_id: int | None = None
) -> list[dict[str, Any]]:
    with session_factory() as db:
        student = db.get(Student, student_id)
        if student is None or student.professor_id is None:
            return []
        query = db.query(Resource).filter(Resource.professor_id == student.professor_id)
        if task_id is not None:
            query = query.join(ResourceLab, ResourceLab.resource_id == Resource.id).filter(
                ResourceLab.task_id == task_id
            )
        return [
            _summary(db, r, detail=False)
            for r in query.order_by(Resource.id.desc())
            if _visible_to_student(db, r, student)
        ]


def student_teacher(session_factory: sessionmaker[OrmSession], student_id: int) -> str | None:
    with session_factory() as db:
        student = db.get(Student, student_id)
        if student is None or student.professor_id is None:
            return None
        professor = db.get(Professor, student.professor_id)
        return professor.display_name if professor else None


def resource_file(
    session_factory: sessionmaker[OrmSession], *, role: str, user_id: int, resource_id: int
) -> tuple[str, str, bytes]:
    """(filename, mime type, bytes) of a file the viewer is allowed to open."""
    with session_factory() as db:
        resource = db.get(Resource, resource_id)
        allowed = False
        if resource is not None:
            if role == "professor":
                allowed = resource.professor_id == user_id
            elif role == "student":
                student = db.get(Student, user_id)
                allowed = student is not None and _visible_to_student(db, resource, student)
        if resource is None or not allowed or resource.kind is not ResourceKind.FILE:
            raise ResourceError("That file isn't available.")
        stored = db.query(ResourceFile).filter_by(resource_id=resource.id).first()
        if stored is None:
            raise ResourceError("That file is missing.")
        return (
            resource.filename or "file",
            resource.mime_type or "application/octet-stream",
            stored.data,
        )
