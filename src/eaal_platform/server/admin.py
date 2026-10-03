"""The admin panel's JSON API (``/admin/api``), for whoever runs the server.

Admins have their own accounts (``Admin`` table) and sign in only here,
never to the app. The panel can see and manage everything: users (including
resetting a professor's password, which nobody else can), labs, the AI
backend, and a read-only browser over every database table.

The database browser is read-only on purpose. Rows are linked (a session id
opens that session), so you can follow a student's work from account to
session to events to code without any SQL.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import String, Table, cast, func, or_, select
from sqlalchemy.engine import Row

from eaal_platform.api.bridge import CavyApi
from eaal_platform.auth import hash_password, password_problem, verify_password
from eaal_platform.db import approval_import, approvals
from eaal_platform.db import resources as resource_store
from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    normalise_email,
    reset_professor_password,
    reset_student_password,
    set_lab_archived,
)
from eaal_platform.db.models import (
    Admin,
    AIInteraction,
    AuditLog,
    Base,
    CodeSnapshot,
    Event,
    EventType,
    ExecutionResult,
    Professor,
    Resource,
    ResourceKind,
    ResourceLab,
    ResourceStudent,
    SignalScore,
    Student,
    Task,
)
from eaal_platform.db.models import Session as SessionModel
from eaal_platform.server.app import (
    TOPIC_ACCOUNTS,
    TOPIC_LABS,
    TOPIC_RESOURCES,
    ServerState,
)

_HIDDEN_COLUMNS = frozenset({"password_hash", "data"})  # "data": uploaded file bytes
_CELL_PREVIEW_CHARS = 200
_MAX_PAGE_SIZE = 200
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_FAILURES = 5
_ADMIN_IDLE_SECONDS = 8 * 60 * 60
_ACTIVE_WINDOW_SECONDS = 15 * 60  # an unsubmitted session counts as "working now" for this long
_FEED_EVENT_TYPES = (
    EventType.TASK_START,
    EventType.CODE_RUN,
    EventType.AI_PROMPT,
    EventType.SUBMISSION,
)


@dataclass
class _AdminSession:
    admin_id: int
    name: str
    last_seen: float


class AdminAuth:
    """Admin sign-in tokens, with a small brake on password guessing."""

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._sessions: dict[str, _AdminSession] = {}
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def issue(self, admin_id: int, name: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = _AdminSession(admin_id, name, self._clock())
        return token

    def get(self, token: str) -> _AdminSession | None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if self._clock() - session.last_seen > _ADMIN_IDLE_SECONDS:
                del self._sessions[token]
                return None
            session.last_seen = self._clock()
            return session

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def locked_out(self, key: str) -> bool:
        with self._lock:
            cutoff = self._clock() - _LOGIN_WINDOW_SECONDS
            recent = [t for t in self._failures[key] if t > cutoff]
            self._failures[key] = recent
            return len(recent) >= _LOGIN_MAX_FAILURES

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(self._clock())

    def clear_failures(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


class SetupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ResetRequest(BaseModel):
    role: str
    id: int


class ArchiveRequest(BaseModel):
    archived: bool


class UserRef(BaseModel):
    role: str
    id: int


class DisableRequest(UserRef):
    disabled: bool


class UpdateUserRequest(UserRef):
    name: str | None = None
    email: str | None = None
    enrollment_no: str | None = None
    new_password: str | None = None
    must_change_password: bool = False


class AssignRequest(BaseModel):
    student_id: int
    professor_id: int | None = None  # None: take the student out of any class


class AllowedRequest(BaseModel):
    role: str
    name: str | None = None
    email: str
    enrollment_no: str | None = None


class AllowedUpdateRequest(BaseModel):
    name: str | None = None
    email: str
    enrollment_no: str | None = None


class ImportRequest(BaseModel):
    role: str
    filename: str
    data_base64: str


class CreateUserRequest(BaseModel):
    role: str
    name: str
    email: str
    password: str
    enrollment_no: str | None = None


class AiRequest(BaseModel):
    provider: str
    api_key: str = ""


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value") and not isinstance(value, str | int | float | bool):
        return value.value  # enums
    return value


def _cell(value: Any, *, full: bool = False) -> Any:
    value = _jsonable(value)
    if isinstance(value, dict | list):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and not full and len(value) > _CELL_PREVIEW_CHARS:
        return value[:_CELL_PREVIEW_CHARS] + "…"
    return value


def _visible_columns(table: Table) -> list[Any]:
    return [c for c in table.columns if c.name not in _HIDDEN_COLUMNS]


def _table_or_404(name: str) -> Table:
    table = Base.metadata.tables.get(name)
    if table is None:
        raise HTTPException(status_code=404, detail=f"No table {name!r}")
    return table


def create_admin_router(state: ServerState, auth: AdminAuth | None = None) -> APIRouter:
    auth = auth or AdminAuth()
    router = APIRouter(prefix="/admin/api")

    def current_admin(authorization: str | None = Header(default=None)) -> _AdminSession:
        if authorization and authorization.lower().startswith("bearer "):
            admin = auth.get(authorization[7:].strip())
            if admin is not None:
                return admin
        raise HTTPException(status_code=401, detail="Sign in to the admin panel.")

    def audit(admin: _AdminSession, action: str, detail: str | None = None) -> None:
        state.audit(f"admin:{admin.name}", action, detail)

    def find_account(db: Any, role: str, user_id: int) -> Student | Professor:
        account: Student | Professor | None
        if role == "student":
            account = db.get(Student, user_id)
        elif role == "professor":
            account = db.get(Professor, user_id)
        else:
            raise HTTPException(status_code=400, detail="Unknown role.")
        if account is None:
            raise HTTPException(status_code=404, detail="No such account.")
        return account

    def email_taken(db: Any, email: str, *, except_account: Student | Professor | None) -> bool:
        for model in (Student, Professor):
            other = db.query(model).filter(func.lower(model.email) == email.lower()).first()
            if other is not None and other is not except_account:
                return True
        return False

    def admin_count() -> int:
        with state.session_factory() as db:
            return db.query(Admin).count()

    # -- sign in ---------------------------------------------------------------

    @router.get("/status")
    def status() -> dict[str, Any]:
        return {"needs_setup": admin_count() == 0}

    @router.post("/setup")
    def setup(request: SetupRequest) -> dict[str, Any]:
        """Create the first admin. Refused once any admin exists."""
        with state.session_factory() as db:
            if db.query(Admin).count() > 0:
                raise HTTPException(status_code=403, detail="An admin already exists.")
            problem = password_problem(request.password)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
            if not request.name.strip() or not request.email.strip():
                raise HTTPException(status_code=400, detail="Enter a name and an email.")
            admin = Admin(
                display_name=request.name.strip(),
                email=request.email.strip().lower(),
                password_hash=hash_password(request.password),
            )
            db.add(admin)
            db.commit()
            return {"token": auth.issue(admin.id, admin.display_name), "name": admin.display_name}

    @router.post("/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        key = request.email.strip().lower()
        if auth.locked_out(key):
            raise HTTPException(
                status_code=429, detail="Too many attempts. Wait a few minutes and try again."
            )
        with state.session_factory() as db:
            admin = db.query(Admin).filter_by(email=key).first()
            ok = admin is not None and verify_password(request.password, admin.password_hash)
            if not ok or admin is None:
                auth.record_failure(key)
                raise HTTPException(status_code=401, detail="Incorrect email or password.")
            auth.clear_failures(key)
            return {"token": auth.issue(admin.id, admin.display_name), "name": admin.display_name}

    @router.post("/logout")
    def logout(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if authorization and authorization.lower().startswith("bearer "):
            auth.revoke(authorization[7:].strip())
        return {"ok": True}

    # -- overview ------------------------------------------------------------------

    @router.get("/overview")
    def overview(_: _AdminSession = Depends(current_admin)) -> dict[str, Any]:
        with state.session_factory() as db:
            counts = {
                "students": db.query(Student).filter(Student.email.is_not(None)).count(),
                "professors": db.query(Professor).count(),
                "labs": db.query(Task)
                .filter(Task.assessment_kind.is_(None), Task.archived_at.is_(None))
                .count(),
                "sessions": db.query(SessionModel).count(),
                "submitted": db.query(SessionModel)
                .filter(SessionModel.submitted_at.is_not(None))
                .count(),
                "events": db.query(Event).count(),
                "ai_interactions": db.query(AIInteraction).count(),
                "resources": db.query(Resource).count(),
            }
            recent = (
                db.query(SessionModel, Student, Task)
                .join(Student, SessionModel.student_id == Student.id)
                .join(Task, SessionModel.task_id == Task.id)
                .order_by(SessionModel.id.desc())
                .limit(10)
                .all()
            )
            recent_rows = [
                {
                    "session_id": s.id,
                    "student": st.display_name,
                    "task": t.title,
                    "started_at": _jsonable(s.started_at),
                    "submitted": s.submitted_at is not None,
                }
                for s, st, t in recent
            ]
        ai = CavyApi(state.session_factory, state.event_logger, ai_holder=state.ai_holder)
        return {
            "counts": counts,
            "signed_in": state.active_users(),
            "recent_sessions": recent_rows,
            "ai": ai.get_ai_settings(),
        }

    # -- users -----------------------------------------------------------------------

    @router.get("/users")
    def users(_: _AdminSession = Depends(current_admin)) -> dict[str, Any]:
        online = {(u["role"], u["user_id"]) for u in state.active_users()}
        with state.session_factory() as db:
            counted = db.query(SessionModel.student_id, func.count(SessionModel.id)).group_by(
                SessionModel.student_id
            )
            session_counts: dict[int, int] = {row[0]: row[1] for row in counted.all()}
            students = [
                {
                    "id": s.id,
                    "name": s.display_name,
                    "email": s.email,
                    "enrollment_no": s.enrollment_no,
                    "sessions": session_counts.get(s.id, 0),
                    "must_change_password": s.must_change_password,
                    "disabled": s.disabled,
                    "professor_id": s.professor_id,
                    "online": ("student", s.id) in online,
                    "created_at": _jsonable(s.created_at),
                }
                for s in db.query(Student).filter(Student.email.is_not(None)).order_by(Student.id)
            ]
            professors = [
                {
                    "id": p.id,
                    "name": p.display_name,
                    "email": p.email,
                    "must_change_password": p.must_change_password,
                    "disabled": p.disabled,
                    "students": db.query(Student).filter_by(professor_id=p.id).count(),
                    "resources": db.query(Resource).filter_by(professor_id=p.id).count(),
                    "online": ("professor", p.id) in online,
                    "created_at": _jsonable(p.created_at),
                }
                for p in db.query(Professor).order_by(Professor.id)
            ]
        return {"students": students, "professors": professors}

    @router.post("/users/reset-password")
    def reset_password(
        request: ResetRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        try:
            if request.role == "student":
                temporary = reset_student_password(state.session_factory, request.id)
            elif request.role == "professor":
                temporary = reset_professor_password(state.session_factory, request.id)
            else:
                raise HTTPException(status_code=400, detail="Unknown role.")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        signed_out = state.revoke_user(
            request.role, request.id, "Your password was reset by an administrator."
        )
        audit(admin, "reset_password", f"{request.role}#{request.id}")
        state.bump(TOPIC_ACCOUNTS)
        return {"temporary_password": temporary, "signed_out": signed_out}

    @router.post("/users/create")
    def create_user(
        request: CreateUserRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        problem = password_problem(request.password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        try:
            if request.role == "student":
                new_id = create_student_account(
                    state.session_factory,
                    display_name=request.name.strip(),
                    email=request.email.strip(),
                    password=request.password,
                    enrollment_no=request.enrollment_no,
                )
            elif request.role == "professor":
                new_id = create_professor_account(
                    state.session_factory,
                    display_name=request.name.strip(),
                    email=request.email.strip(),
                    password=request.password,
                )
            else:
                raise HTTPException(status_code=400, detail="Unknown role.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(admin, "create_account", f"{request.role}#{new_id}")
        state.bump(TOPIC_ACCOUNTS)
        return {"id": new_id}

    @router.post("/users/update")
    def update_user(
        request: UpdateUserRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        """Change someone's name, email, enrolment number or password."""
        with state.session_factory() as db:
            account = find_account(db, request.role, request.id)
            changed: list[str] = []
            if request.name is not None and request.name.strip():
                account.display_name = request.name.strip()
                changed.append("name")
            if request.email is not None and request.email.strip():
                email = normalise_email(request.email)
                if email != (account.email or "").lower():
                    if email_taken(db, email, except_account=account):
                        raise HTTPException(status_code=400, detail="That email is already used.")
                    approvals.follow_email_change(db, account.email or "", email)
                    account.email = email
                    changed.append("email")
            if request.enrollment_no is not None and isinstance(account, Student):
                account.enrollment_no = request.enrollment_no.strip() or None
                changed.append("enrolment")
            if request.new_password:
                problem = password_problem(request.new_password)
                if problem:
                    raise HTTPException(status_code=400, detail=problem)
                account.password_hash = hash_password(request.new_password)
                account.must_change_password = request.must_change_password
                changed.append("password")
            db.commit()
        # Credentials changed: any open session of this account is now stale.
        signed_out = 0
        if {"email", "password"} & set(changed):
            signed_out = state.revoke_user(
                request.role, request.id, "Your sign-in details were changed by an administrator."
            )
        audit(admin, "update_account", f"{request.role}#{request.id}: {', '.join(changed) or '-'}")
        state.bump(TOPIC_ACCOUNTS)
        return {"ok": True, "changed": changed, "signed_out": signed_out}

    @router.post("/users/disable")
    def disable_user(
        request: DisableRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        with state.session_factory() as db:
            account = find_account(db, request.role, request.id)
            account.disabled = request.disabled
            db.commit()
        signed_out = 0
        if request.disabled:
            signed_out = state.revoke_user(
                request.role, request.id, "Your account was disabled by an administrator."
            )
        audit(
            admin,
            "disable_account" if request.disabled else "enable_account",
            f"{request.role}#{request.id}",
        )
        state.bump(TOPIC_ACCOUNTS)
        return {"ok": True, "signed_out": signed_out}

    @router.post("/users/delete")
    def delete_user(
        request: UserRef, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        """Remove an account. Students with recorded work can only be disabled."""
        with state.session_factory() as db:
            account = find_account(db, request.role, request.id)
            if isinstance(account, Student):
                work = db.query(SessionModel).filter_by(student_id=account.id).count()
                if work:
                    raise HTTPException(
                        status_code=400,
                        detail=f"This student has {work} recorded session(s). "
                        "Disable the account instead, so their history is kept.",
                    )
            label = f"{request.role}#{request.id} {account.display_name}"
            if isinstance(account, Professor):
                # Their students become unassigned and their shared material goes with them.
                for student in db.query(Student).filter_by(professor_id=account.id):
                    student.professor_id = None
                resource_store.delete_professor_resources(db, account.id)
                db.flush()  # children first: the database enforces the foreign keys
            db.delete(account)
            db.commit()
        state.revoke_user(request.role, request.id, "Your account was removed by an administrator.")
        audit(admin, "delete_account", label)
        state.bump(TOPIC_ACCOUNTS)
        return {"ok": True}

    @router.post("/signed-in/{client_id}/sign-out")
    def sign_out_one(
        client_id: int, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        if not state.revoke_client(client_id, "You were signed out by an administrator."):
            raise HTTPException(status_code=404, detail="That person is no longer signed in.")
        audit(admin, "force_sign_out", f"connection #{client_id}")
        return {"ok": True}

    @router.post("/signed-in/sign-out-all")
    def sign_out_everyone(admin: _AdminSession = Depends(current_admin)) -> dict[str, Any]:
        count = 0
        for user in state.active_users():
            if state.revoke_client(user["client_id"], "You were signed out by an administrator."):
                count += 1
        audit(admin, "force_sign_out_all", f"{count} signed out")
        return {"ok": True, "signed_out": count}

    # -- live view ---------------------------------------------------------------------------

    @router.get("/live")
    def live(_: _AdminSession = Depends(current_admin)) -> dict[str, Any]:
        """What is happening right now: who's here, who's working on what."""
        now = datetime.now(UTC).replace(tzinfo=None)
        with state.session_factory() as db:
            open_sessions = (
                db.query(SessionModel, Student, Task)
                .join(Student, SessionModel.student_id == Student.id)
                .join(Task, SessionModel.task_id == Task.id)
                .filter(SessionModel.submitted_at.is_(None))
                .order_by(SessionModel.id.desc())
                .limit(100)
                .all()
            )
            working = []
            for session, student, task in open_sessions:
                last = (
                    db.query(Event.timestamp)
                    .filter(Event.session_id == session.id)
                    .order_by(Event.id.desc())
                    .first()
                )
                last_at = last[0] if last else session.started_at
                idle = int((now - last_at.replace(tzinfo=None)).total_seconds())
                if idle > _ACTIVE_WINDOW_SECONDS:
                    continue  # abandoned, not "working now"
                working.append(
                    {
                        "session_id": session.id,
                        "student": student.display_name,
                        "task": task.title,
                        "stage": session.stage.stage_type.value if session.stage else None,
                        "started_at": _jsonable(session.started_at),
                        "idle_seconds": max(0, idle),
                        "events": db.query(Event).filter_by(session_id=session.id).count(),
                        "ai_chats": db.query(AIInteraction)
                        .filter_by(session_id=session.id)
                        .count(),
                    }
                )
            latest_events = (
                db.query(Event, SessionModel, Student)
                .join(SessionModel, Event.session_id == SessionModel.id)
                .join(Student, SessionModel.student_id == Student.id)
                .filter(Event.event_type.in_(_FEED_EVENT_TYPES))
                .order_by(Event.id.desc())
                .limit(20)
                .all()
            )
            feed = [
                {
                    "student": st.display_name,
                    "type": ev.event_type.value,
                    "timestamp": _jsonable(ev.timestamp),
                    "session_id": se.id,
                }
                for ev, se, st in latest_events
            ]
            accounts = {
                "students": db.query(Student).filter(Student.email.is_not(None)).count(),
                "professors": db.query(Professor).count(),
                "disabled": (
                    db.query(Student).filter(Student.disabled.is_(True)).count()
                    + db.query(Professor).filter(Professor.disabled.is_(True)).count()
                ),
            }
        signed_in = state.active_users()
        accounts["signed_in"] = len(signed_in)
        return {
            "accounts": accounts,
            "signed_in": signed_in,
            "working_now": working,
            "feed": feed,
        }

    @router.get("/audit")
    def audit_log(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
        _: _AdminSession = Depends(current_admin),
    ) -> dict[str, Any]:
        with state.session_factory() as db:
            total = db.query(AuditLog).count()
            rows = (
                db.query(AuditLog)
                .order_by(AuditLog.id.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
                .all()
            )
            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "rows": [
                    {
                        "id": r.id,
                        "timestamp": _jsonable(r.timestamp),
                        "actor": r.actor,
                        "action": r.action,
                        "detail": r.detail,
                        "address": r.address,
                    }
                    for r in rows
                ],
            }

    @router.post("/users/assign")
    def assign_student(
        request: AssignRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        """Put a student in a professor's class, move them, or take them out of any class."""
        try:
            resource_store.assign_student(
                state.session_factory, request.student_id, request.professor_id
            )
        except resource_store.ResourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = f"professor#{request.professor_id}" if request.professor_id else "no class"
        audit(admin, "assign_student", f"student#{request.student_id} -> {target}")
        state.bump(TOPIC_ACCOUNTS, TOPIC_RESOURCES)
        return {"ok": True}

    @router.get("/resources")
    def all_resources(_: _AdminSession = Depends(current_admin)) -> list[dict[str, Any]]:
        with state.session_factory() as db:
            rows = []
            for r in db.query(Resource).order_by(Resource.id.desc()):
                owner = db.get(Professor, r.professor_id)
                chosen = db.query(ResourceStudent).filter_by(resource_id=r.id).count()
                shared = "whole class" if r.audience_all else f"{chosen} student(s)"
                rows.append(
                    {
                        "id": r.id,
                        "title": r.title,
                        "kind": r.kind.value,
                        "detail": r.filename if r.kind is ResourceKind.FILE else r.url,
                        "size_bytes": r.size_bytes,
                        "owner": owner.display_name if owner else None,
                        "shared_with": shared,
                        "labs": db.query(ResourceLab).filter_by(resource_id=r.id).count(),
                        "created_at": _jsonable(r.created_at),
                    }
                )
            return rows

    @router.post("/resources/{resource_id}/delete")
    def delete_resource(
        resource_id: int, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        with state.session_factory() as db:
            resource = db.get(Resource, resource_id)
            if resource is None:
                raise HTTPException(status_code=404, detail="No such resource.")
            title = resource.title
            resource_store.delete_resource_row(db, resource)
            db.commit()
        audit(admin, "delete_resource", f"{title} (#{resource_id})")
        state.bump(TOPIC_RESOURCES)
        return {"ok": True}

    # -- approved emails: who may create an account ----------------------------------------

    def _allowed_overview(role: str) -> dict[str, Any]:
        try:
            entries = approvals.list_entries(state.session_factory, role)
        except approvals.ApprovalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        registered = sum(1 for e in entries if e["registered"])
        return {
            "entries": entries,
            "counts": {
                "total": len(entries),
                "registered": registered,
                "waiting": len(entries) - registered,
            },
        }

    @router.get("/allowed/{role}")
    def allowed_list(role: str, _: _AdminSession = Depends(current_admin)) -> dict[str, Any]:
        return _allowed_overview(role)

    @router.post("/allowed")
    def allowed_add(
        request: AllowedRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        try:
            entry_id = approvals.add_entry(
                state.session_factory,
                request.role,
                name=request.name,
                email=request.email,
                enrollment_no=request.enrollment_no,
            )
        except approvals.ApprovalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(admin, "approve_email", f"{request.role} {normalise_email(request.email)}")
        state.bump(TOPIC_ACCOUNTS)
        return {"id": entry_id}

    @router.post("/allowed/{entry_id}/update")
    def allowed_update(
        entry_id: int, request: AllowedUpdateRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        try:
            approvals.update_entry(
                state.session_factory,
                entry_id,
                name=request.name,
                email=request.email,
                enrollment_no=request.enrollment_no,
            )
        except approvals.ApprovalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(admin, "edit_approved_email", f"entry#{entry_id}")
        state.bump(TOPIC_ACCOUNTS)
        return {"ok": True}

    @router.post("/allowed/{entry_id}/delete")
    def allowed_delete(
        entry_id: int, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        try:
            email = approvals.delete_entry(state.session_factory, entry_id)
        except approvals.ApprovalError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit(admin, "remove_approved_email", email)
        state.bump(TOPIC_ACCOUNTS)
        return {"ok": True}

    @router.post("/allowed-import")
    def allowed_import(
        request: ImportRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        """Add many approved emails from an uploaded CSV or Excel file."""
        try:
            approvals.check_role(request.role)
            data = base64.b64decode(request.data_base64, validate=True)
            rows = approval_import.read_rows(request.filename, data, request.role)
        except (approval_import.UploadError, approvals.ApprovalError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="That file couldn't be read.") from None
        report = approvals.import_entries(state.session_factory, request.role, rows)
        audit(
            admin,
            "import_approved_emails",
            f"{request.role}: {report.added} added, {len(report.problems)} problems "
            f"({request.filename})",
        )
        state.bump(TOPIC_ACCOUNTS)
        return {**report.as_dict(), "rows": len(rows)}

    @router.get("/allowed-template/{role}.csv")
    def allowed_template(role: str, _: _AdminSession = Depends(current_admin)) -> Response:
        if role not in approval_import.TEMPLATES:
            raise HTTPException(status_code=404, detail="Unknown role.")
        return Response(
            "\ufeff" + approval_import.TEMPLATES[role],
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{role}s-template.csv"'.replace(
                    "professors", "teachers"
                )
            },
        )

    # -- labs -----------------------------------------------------------------------

    @router.get("/labs")
    def labs(_: _AdminSession = Depends(current_admin)) -> list[dict[str, Any]]:
        with state.session_factory() as db:
            out = []
            for task in db.query(Task).filter(Task.assessment_kind.is_(None)).order_by(Task.id):
                if not task.stages:
                    continue  # the standalone Practice task isn't a lab
                stage_ids = [s.id for s in task.stages]
                out.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "professor": task.professor_name,
                        "course": task.course,
                        "difficulty": task.difficulty,
                        "stages": len(stage_ids),
                        "sessions": db.query(SessionModel)
                        .filter(SessionModel.stage_id.in_(stage_ids))
                        .count(),
                        "students": db.query(SessionModel.student_id)
                        .filter(SessionModel.stage_id.in_(stage_ids))
                        .distinct()
                        .count(),
                        "in_progress": db.query(SessionModel)
                        .filter(
                            SessionModel.stage_id.in_(stage_ids),
                            SessionModel.submitted_at.is_(None),
                        )
                        .count(),
                        "submitted": db.query(SessionModel)
                        .filter(
                            SessionModel.stage_id.in_(stage_ids),
                            SessionModel.submitted_at.is_not(None),
                        )
                        .count(),
                        "archived": task.archived_at is not None,
                        "created_at": _jsonable(task.created_at),
                    }
                )
            return out

    @router.post("/labs/{task_id}/archive")
    def archive_lab(
        task_id: int, request: ArchiveRequest, admin: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        try:
            set_lab_archived(state.session_factory, task_id, archived=request.archived)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit(admin, "archive_lab" if request.archived else "restore_lab", f"lab#{task_id}")
        state.bump(TOPIC_LABS)
        return {"ok": True}

    # -- database browser (read-only) ----------------------------------------------------

    @router.get("/tables")
    def tables(_: _AdminSession = Depends(current_admin)) -> list[dict[str, Any]]:
        out = []
        with state.engine.connect() as conn:
            for name, table in sorted(Base.metadata.tables.items()):
                total = conn.execute(select(func.count()).select_from(table)).scalar_one()
                out.append(
                    {
                        "name": name,
                        "rows": total,
                        "columns": [
                            {
                                "name": c.name,
                                "references": next(
                                    (fk.column.table.name for fk in c.foreign_keys), None
                                ),
                            }
                            for c in _visible_columns(table)
                        ],
                    }
                )
        return out

    @router.get("/tables/{name}")
    def table_rows(
        name: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
        q: str = "",
        sort: str = "",
        desc: bool = True,
        filter_column: str = "",
        filter_value: str = "",
        _: _AdminSession = Depends(current_admin),
    ) -> dict[str, Any]:
        table = _table_or_404(name)
        columns = _visible_columns(table)
        names = {c.name for c in columns}
        query = select(*columns)
        if filter_column:
            if filter_column not in names:
                raise HTTPException(status_code=400, detail="Unknown column.")
            query = query.where(cast(table.c[filter_column], String) == filter_value)
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(or_(*[cast(c, String).ilike(like) for c in columns]))
        sort_name = sort if sort in names else ("id" if "id" in names else columns[0].name)
        order = table.c[sort_name].desc() if desc else table.c[sort_name].asc()
        with state.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
            rows = conn.execute(
                query.order_by(order).limit(page_size).offset((page - 1) * page_size)
            )
            data = [[_cell(v) for v in row] for row in rows]
        return {
            "columns": [
                {
                    "name": c.name,
                    "references": next((fk.column.table.name for fk in c.foreign_keys), None),
                }
                for c in columns
            ],
            "rows": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "sort": sort_name,
            "desc": desc,
        }

    @router.get("/tables/{name}/{row_id}")
    def table_row(
        name: str, row_id: int, _: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        """One whole row, with long text (such as code) untrimmed."""
        table = _table_or_404(name)
        if "id" not in table.c:
            raise HTTPException(status_code=404, detail="This table has no id column.")
        columns = _visible_columns(table)
        with state.engine.connect() as conn:
            row: Row[Any] | None = conn.execute(
                select(*columns).where(table.c.id == row_id)
            ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="No such row.")
        return {c.name: _cell(v, full=True) for c, v in zip(columns, row, strict=True)}

    @router.get("/export/{name}.csv")
    def export_table(name: str, _: _AdminSession = Depends(current_admin)) -> Response:
        table = _table_or_404(name)
        columns = _visible_columns(table)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow([c.name for c in columns])
        with state.engine.connect() as conn:
            for row in conn.execute(select(*columns)):
                writer.writerow([_cell(v, full=True) for v in row])
        return Response(
            "﻿" + buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )

    # -- one session, everything ---------------------------------------------------------

    @router.get("/sessions/{session_id}")
    def session_detail(
        session_id: int, _: _AdminSession = Depends(current_admin)
    ) -> dict[str, Any]:
        with state.session_factory() as db:
            session = db.get(SessionModel, session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="No such session.")
            latest_scores: dict[str, Any] = {}
            for score in (
                db.query(SignalScore).filter_by(session_id=session_id).order_by(SignalScore.id)
            ):
                latest_scores[score.signal_key] = score.value
            return {
                "session": {
                    "id": session.id,
                    "student": session.student.display_name,
                    "task": session.task.title,
                    "stage": session.stage.stage_type.value if session.stage else None,
                    "started_at": _jsonable(session.started_at),
                    "submitted_at": _jsonable(session.submitted_at),
                },
                "signals": latest_scores,
                "events": [
                    {
                        "id": e.id,
                        "type": e.event_type.value,
                        "timestamp": _jsonable(e.timestamp),
                        "payload": e.payload_json,
                    }
                    for e in db.query(Event).filter_by(session_id=session_id).order_by(Event.id)
                ],
                "ai_interactions": [
                    {
                        "id": i.id,
                        "timestamp": _jsonable(i.timestamp),
                        "prompt": i.prompt,
                        "response": i.response,
                        "provider": i.provider,
                        "model": i.model,
                    }
                    for i in db.query(AIInteraction)
                    .filter_by(session_id=session_id)
                    .order_by(AIInteraction.id)
                ],
                "runs": [
                    {
                        "id": r.id,
                        "timestamp": _jsonable(r.timestamp),
                        "exit_status": r.exit_status,
                        "timed_out": r.timed_out,
                        "duration_ms": r.duration_ms,
                        "stdout": r.stdout,
                        "stderr": r.stderr,
                    }
                    for r in db.query(ExecutionResult)
                    .filter_by(session_id=session_id)
                    .order_by(ExecutionResult.id)
                ],
                "final_code": next(
                    (
                        s.content
                        for s in db.query(CodeSnapshot)
                        .filter_by(session_id=session_id)
                        .order_by(CodeSnapshot.id.desc())
                        .limit(1)
                    ),
                    None,
                ),
            }

    # -- AI backend & backup ----------------------------------------------------------------

    @router.get("/ai")
    def ai_settings(_: _AdminSession = Depends(current_admin)) -> dict[str, Any]:
        api = CavyApi(state.session_factory, state.event_logger, ai_holder=state.ai_holder)
        return api.get_ai_settings()

    @router.post("/ai")
    def set_ai(request: AiRequest, _: _AdminSession = Depends(current_admin)) -> dict[str, Any]:
        api = CavyApi(state.session_factory, state.event_logger, ai_holder=state.ai_holder)
        try:
            return api.set_ai_provider(request.provider, request.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/backup")
    def backup(
        background: BackgroundTasks, _: _AdminSession = Depends(current_admin)
    ) -> FileResponse:
        """A consistent copy of the whole database, taken while the server runs."""
        folder = Path(tempfile.mkdtemp(prefix="cavy-backup-"))
        target = folder / "backup.db"
        source = state.engine.raw_connection()
        try:
            destination = sqlite3.connect(target)
            try:
                raw = source.driver_connection
                assert isinstance(raw, sqlite3.Connection)
                raw.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        background.add_task(shutil.rmtree, folder, ignore_errors=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return FileResponse(
            target, media_type="application/octet-stream", filename=f"cavy-backup-{stamp}.db"
        )

    return router


_ADMIN_UI_DIR = Path(__file__).resolve().parent / "admin_ui"


def mount_admin_ui(app: FastAPI) -> None:
    """Serve the admin page at ``/admin`` (its script and styles at ``/admin/ui``)."""

    app.mount("/admin/ui", StaticFiles(directory=_ADMIN_UI_DIR), name="admin-ui")

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        return FileResponse(_ADMIN_UI_DIR / "index.html")

    @app.get("/", include_in_schema=False)
    def root() -> Response:
        return Response(status_code=307, headers={"Location": "/admin"})
