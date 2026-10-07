"""The Python <-> JS bridge: every operation the web frontend can perform.

This replaces the old Qt screens (``WorkspaceScreen``, ``LabsScreen``, ...)
entirely, but not the engine underneath them — every method here is a thin
wrapper around the same ``db``/``sandbox``/``events``/``ai`` packages those
screens used. Nothing about *what* the app records or how it runs code
changed; only how the UI talks to it did.

Every method takes and returns plain JSON-serializable data (dicts, lists,
strings, numbers, booleans) because pywebview marshals arguments and return
values across the JS/Python boundary as JSON — there is no way to pass a
richer Python object through, and there's no reason to want one here.

Workspace/session data stays stateless across calls: nothing about "the
current lab session" is held in memory on this object (no
``self._last_execution_outcome`` the way ``WorkspaceScreen`` had). Every
such method takes a ``session_id`` and re-reads whatever it needs from the
database, so a page reload in the webview never loses state that already
reached the database — only unsaved, never-run keystrokes are ever purely
in the frontend's memory.

*Who is currently logged in* is the one piece of real session state this
object does hold (``_current_student_id``/``_current_professor_id``,
set by ``login`` and cleared by ``logout``) — there is exactly one
``CavyApi`` instance per app window, so it plays the role a server would
normally give a per-request session/cookie.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai import cloud_providers
from eaal_platform.ai.ollama_provider import OllamaProvider
from eaal_platform.ai.provider import (
    AIProvider,
    GenerationContext,
    ProviderHolder,
    Purpose,
)
from eaal_platform.auth import hash_password, password_problem, verify_password
from eaal_platform.db import academics, app_settings, approvals
from eaal_platform.db import resources as resource_store
from eaal_platform.db.bootstrap import (
    authenticate_professor,
    authenticate_student,
    create_professor_account,
    create_student_account,
    resume_or_start_stage_session,
    start_practice_session,
)
from eaal_platform.db.bootstrap import (
    change_password as change_password_row,
)
from eaal_platform.db.bootstrap import (
    create_followup_assessment as create_followup_assessment_row,
)
from eaal_platform.db.bootstrap import (
    create_lab as create_lab_row,
)
from eaal_platform.db.bootstrap import (
    reset_student_password as reset_student_password_row,
)
from eaal_platform.db.bootstrap import (
    set_lab_archived as set_lab_archived_row,
)
from eaal_platform.db.bootstrap import (
    update_followup_assessment as update_followup_row,
)
from eaal_platform.db.bootstrap import (
    update_lab as update_lab_row,
)
from eaal_platform.db.models import (
    AIAssistanceMode,
    AIInteraction,
    AssessmentKind,
    CodeSnapshot,
    ConceptCheckResponse,
    Event,
    EventType,
    ExecutionResult,
    Professor,
    SignalScore,
    Stage,
    StageType,
    Student,
    Task,
)
from eaal_platform.db.models import Session as SessionModel
from eaal_platform.db.progress import is_stage_unlocked, stage_status
from eaal_platform.events.logger import EventLogger, PendingEvent
from eaal_platform.logging_setup import get_logger
from eaal_platform.progress_report import SessionRow, build_progress
from eaal_platform.sandbox.executor import run_code as sandbox_run_code
from eaal_platform.signals.compute import compute_all_signals, persist_signal_scores

_ENTRY_FILENAME = "main.py"
_DISABLED_MESSAGE = "This account has been disabled. Ask your administrator."
_NOT_APPROVED = {
    "student": (
        "This email address hasn't been approved. Use your university email, "
        "or ask your administrator to add you."
    ),
    "professor": (
        "Teacher accounts are set up by your administrator. "
        "Ask them to add your email address first."
    ),
}

_STAGE_TYPES = (StageType.LEARNING, StageType.EXPLORATION, StageType.ASSESSMENT)


def _is_lab(task: Task) -> bool:
    """A Lab (as opposed to Practice, or a Transfer/Retention follow-up)."""
    return bool(task.stages) and task.assessment_kind is None


def _is_archived(task: Task) -> bool:
    parent = task.linked_task
    return task.archived_at is not None or (parent is not None and parent.archived_at is not None)


def save_text_file(
    dialog: Callable[[str], str | None] | None, exported: dict[str, Any]
) -> dict[str, Any]:
    """Ask where to save ``exported`` ({filename, csv}) and write it there."""
    if dialog is None:
        return {"ok": False, "error": "Saving files isn't available in this window."}
    chosen = dialog(exported["filename"])
    if not chosen:
        return {"ok": False, "cancelled": True}
    # utf-8-sig so Excel opens non-ASCII names correctly.
    with Path(chosen).open("w", newline="", encoding="utf-8-sig") as handle:
        handle.write(exported["csv"])
    return {"ok": True, "path": chosen}


def _csv_safe(value: object) -> str:
    """Stop a spreadsheet treating a cell as a formula (e.g. a name like ``=HYPERLINK(...)``)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


# S3.1 (Conceptual Understanding): a fixed prompt shown at Assessment-stage
# submit time. Stored alongside each response (see ``ConceptCheckResponse``)
# rather than assumed by the scorer, so a future per-task question doesn't
# lose the ability to interpret past answers.
_CONCEPT_CHECK_QUESTION = (
    "In your own words, explain the concept your solution relies on and why your approach works."
)


def _bundle_files(files: dict[str, str]) -> str:
    """Render a session's open files as one text blob for storage/AI context.

    Keeps every signal that reasons about "the student's code" as a single
    string (similarity against AI-suggested code, truncation for prompts,
    ...) working unchanged even though a workspace can hold several files —
    see ``db/models.py``'s ``CodeSnapshot.content`` docstring.
    """
    return "\n\n".join(f"### {name}\n{content}" for name, content in files.items())


_SUBMIT_NOTES = {"focus": "Submitted automatically: left the lab window twice"}
log = get_logger(__name__)
_FOCUS_LIMIT = 2  # leaving the lab window this many times ends the lab


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _paste_comes_from_ai(pasted: str, replies: list[str]) -> bool:
    """Whether pasted text is (mostly) something the AI assistant wrote."""
    whole = _squash(pasted)
    if len(whole) < 8:
        return False
    squashed = [_squash(reply) for reply in replies]
    if any(whole in reply for reply in squashed):
        return True
    lines = [_squash(line) for line in pasted.splitlines()]
    lines = [line for line in lines if len(line) >= 6]
    if len(lines) < 2:
        return False
    return any(sum(1 for line in lines if line in reply) / len(lines) >= 0.6 for reply in squashed)


def _student_card(student: Student) -> dict[str, Any]:
    return {"id": student.id, "name": student.display_name, **academics.describe(student)}


def _seconds_since(moment: datetime | None) -> int:
    if moment is None:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - moment).total_seconds()))


_BUNDLE_HEADER = re.compile(r"(?:^|\n\n)### ([\w.-]+\.py)\n")


def _unbundle_files(blob: str) -> dict[str, str]:
    """Inverse of ``_bundle_files``: the blob back into ``{filename: content}``."""
    parts = _BUNDLE_HEADER.split(blob)
    # split() yields ["", name1, body1, name2, body2, ...]
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


# Mirrors Table 5 in docs/The EAAL Framework.pdf. Kept here (not derived
# from signal_scores rows) because the pillar structure and its display
# order are fixed by the framework, independent of which signals happen to
# have been computed yet for a given session.
_PILLARS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "P1 · AI Utilization",
        "How effectively is the student using AI?",
        ("S1.1", "S1.2", "S1.3", "S1.4", "S1.5", "S1.6"),
    ),
    (
        "P2 · Cognitive Engagement",
        "How actively is the student participating in the cognitive process?",
        ("S2.1", "S2.2", "S2.3", "S2.4"),
    ),
    (
        "P3 · Learning & Knowledge Development",
        "What knowledge and capability is developing within the student?",
        ("S3.1", "S3.2", "S3.3", "S3.4"),
    ),
)


class CavyApi:
    """Exposed to the frontend as ``window.pywebview.api``."""

    def __init__(
        self,
        session_factory: sessionmaker[OrmSession],
        event_logger: EventLogger,
        ai_provider: AIProvider | None = None,
        save_file_dialog: Callable[[str], str | None] | None = None,
        *,
        ai_holder: ProviderHolder | None = None,
        professors_set_ai: bool = False,
        restrict_signup: bool = False,
        ai_http: httpx.Client | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_logger = event_logger
        # Shared with other CavyApi instances when running as a server, so the
        # whole class uses one AI backend; standalone, it's just this app's.
        self._ai_holder = ai_holder or ProviderHolder(ai_provider)
        # On the server, only professors may change the class-wide AI backend.
        self._professors_set_ai = professors_set_ai
        # On a server, only emails an administrator has approved may sign up.
        self._restrict_signup = restrict_signup
        # Lets tests stand in for the AI companies' servers; None means the real internet.
        self._ai_http = ai_http
        # Opens the OS "Save as" dialog and returns the chosen path (None if
        # cancelled). Injected by app.py because only the window can show it;
        # the webview can't download files itself.
        self._save_file_dialog = save_file_dialog
        self._current_student_id: int | None = None
        self._current_student_name: str | None = None
        self._current_professor_id: int | None = None
        self._current_professor_name: str | None = None

    @property
    def _ai_provider(self) -> AIProvider:
        return self._ai_holder.provider

    @_ai_provider.setter
    def _ai_provider(self, provider: AIProvider) -> None:
        self._ai_holder.provider = provider

    # -- auth --------------------------------------------------------------

    def login(self, role: str, email: str, password: str) -> dict[str, Any]:
        """Authenticate against the Login screen's Student/Teacher tab.

        Clears whichever role isn't logging in, so switching from one
        account to another in the same window (via ``logout`` then
        ``login`` again) can never leave a stale identity of the other
        role still "logged in" underneath it.
        """
        if role == "student":
            result = authenticate_student(self._session_factory, email=email, password=password)
            if result is None:
                return {"ok": False, "error": "Incorrect email or password."}
            if self._is_disabled("student", result[0]):
                return {"ok": False, "error": _DISABLED_MESSAGE}
            self._current_student_id, self._current_student_name = result
            self._current_professor_id, self._current_professor_name = None, None
            return {
                "ok": True,
                "role": "student",
                "name": self._current_student_name,
                "must_change_password": self._student_must_change_password(),
                "needs_details": self._student_needs_details(),
            }
        if role == "professor":
            result = authenticate_professor(self._session_factory, email=email, password=password)
            if result is None:
                return {"ok": False, "error": "Incorrect email or password."}
            if self._is_disabled("professor", result[0]):
                return {"ok": False, "error": _DISABLED_MESSAGE}
            self._current_professor_id, self._current_professor_name = result
            self._current_student_id, self._current_student_name = None, None
            return {
                "ok": True,
                "role": "professor",
                "name": self._current_professor_name,
                "must_change_password": self._professor_must_change_password(),
                "needs_details": self._professor_needs_courses(),
            }
        raise ValueError(f"Unknown role {role!r}")

    def create_account(
        self,
        role: str,
        display_name: str,
        email: str,
        password: str,
        enrollment_no: str | None = None,
        cohort: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        problem = password_problem(password)
        if problem:
            return {"ok": False, "error": problem}
        if role not in ("student", "professor"):
            raise ValueError(f"Unknown role {role!r}")
        if self._restrict_signup:
            approval = approvals.find_approval(self._session_factory, role, email)
            if approval is None:
                return {"ok": False, "error": _NOT_APPROVED[role]}
            enrollment_no = enrollment_no or approval["enrollment_no"]
            display_name = display_name.strip() or approval["name"] or ""
        try:
            if role == "student":
                place = academics.signup_cohort(self._session_factory, cohort)
                if place and not enrollment_no:
                    enrollment_no = place["roll_number"]  # shown wherever an enrolment no. is
                create_student_account(
                    self._session_factory,
                    display_name=display_name,
                    email=email,
                    password=password,
                    enrollment_no=enrollment_no,
                    cohort=place,
                )
            else:
                create_professor_account(
                    self._session_factory, display_name=display_name, email=email, password=password
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def _identity(self) -> tuple[str, int] | None:
        """(role, id) of whoever is signed in, for the server's bookkeeping."""
        if self._current_student_id is not None:
            return "student", self._current_student_id
        if self._current_professor_id is not None:
            return "professor", self._current_professor_id
        return None

    def _is_disabled(self, role: str, user_id: int) -> bool:
        with self._session_factory() as db_session:
            account: Student | Professor | None
            if role == "student":
                account = db_session.get(Student, user_id)
            else:
                account = db_session.get(Professor, user_id)
            return bool(account and account.disabled)

    def _professor_needs_courses(self) -> bool:
        """True for a professor on a server who hasn't yet said which courses they teach."""
        if not self._restrict_signup:
            return False
        professor_id = self._require_professor_id()
        has_any = bool(academics.professor_course_ids(self._session_factory, professor_id))
        return not has_any and bool(academics.list_courses(self._session_factory))

    def _student_needs_details(self) -> bool:
        """True for a student who hasn't said which course they are in (while courses exist)."""
        with self._session_factory() as db_session:
            student = db_session.get(Student, self._require_student_id())
            if student is None or student.course_id is not None:
                return False
        return bool(academics.list_courses(self._session_factory))

    def get_signup_info(self) -> dict[str, Any]:
        """Whether students get their first login by email (only on a configured server)."""
        return {"email_signup": False}

    def complete_first_login(
        self, new_password: str, cohort: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """A student's first sign-in: choose a password and say where they study.

        Only works while the temporary password from the email is still in use,
        so it can't be used to change a password without knowing the old one.
        """
        student_id = self._require_student_id()
        problem = password_problem(new_password)
        if problem:
            return {"ok": False, "error": problem}
        with self._session_factory() as db_session:
            student = db_session.get(Student, student_id)
            if student is None or not student.must_change_password:
                return {"ok": False, "error": "Use Profile to change your password."}
            if student.password_hash and verify_password(new_password, student.password_hash):
                return {"ok": False, "error": "Choose a password different from the temporary one."}
        try:
            place = academics.signup_cohort(self._session_factory, cohort)
        except academics.AcademicError as exc:
            return {"ok": False, "error": str(exc)}
        with self._session_factory() as db_session:
            student = db_session.get(Student, student_id)
            if student is None:
                return {"ok": False, "error": "That account no longer exists."}
            student.password_hash = hash_password(new_password)
            student.must_change_password = False
            if place:
                student.course_id = place["course_id"]
                student.year = place["year"]
                student.division = place["division"]
                student.batch = place["batch"]
                student.roll_number = place["roll_number"]
                student.enrollment_no = student.enrollment_no or place["roll_number"]
            db_session.commit()
        if place:
            academics.assign_by_groups(self._session_factory, student_id)
        return {"ok": True}

    def _student_must_change_password(self) -> bool:
        with self._session_factory() as db_session:
            student = db_session.get(Student, self._require_student_id())
            return bool(student and student.must_change_password)

    def _professor_must_change_password(self) -> bool:
        with self._session_factory() as db_session:
            professor = db_session.get(Professor, self._require_professor_id())
            return bool(professor and professor.must_change_password)

    def get_profile(self) -> dict[str, Any]:
        """The signed-in user's own account details, for the Profile screen."""
        with self._session_factory() as db_session:
            if self._current_student_id is not None:
                student = db_session.get(Student, self._current_student_id)
                if student is not None:
                    return {
                        "role": "student",
                        "name": student.display_name,
                        "email": student.email,
                        "enrollment_no": student.enrollment_no,
                        "must_change_password": student.must_change_password,
                        **academics.describe(student),
                    }
            if self._current_professor_id is not None:
                professor = db_session.get(Professor, self._current_professor_id)
                if professor is not None:
                    return {
                        "role": "professor",
                        "name": professor.display_name,
                        "email": professor.email,
                        "enrollment_no": None,
                        "must_change_password": professor.must_change_password,
                    }
        raise ValueError("Not logged in")

    def change_password(self, current_password: str, new_password: str) -> dict[str, Any]:
        if self._current_student_id is not None:
            role, user_id = "student", self._current_student_id
        elif self._current_professor_id is not None:
            role, user_id = "professor", self._current_professor_id
        else:
            raise ValueError("Not logged in")
        try:
            change_password_row(
                self._session_factory,
                role=role,
                user_id=user_id,
                current_password=current_password,
                new_password=new_password,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def get_students(self) -> list[dict[str, Any]]:
        """The students in this professor's class."""
        return resource_store.class_students(self._session_factory, self._require_professor_id())

    def get_unassigned_students(self) -> list[dict[str, Any]]:
        """Students not in this professor's class yet (being in other classes is fine).

        On a server a professor can only add students of the courses they teach.
        """
        professor_id = self._require_professor_id()
        rows: list[dict[str, Any]] = resource_store.addable_students(
            self._session_factory, professor_id
        )
        if self._restrict_signup:
            mine = academics.professor_course_ids(self._session_factory, professor_id)
            rows = [row for row in rows if row["course_id"] in mine]
        return rows

    # -- courses, years, divisions and batches --------------------------------------

    def get_academic_options(self) -> list[dict[str, Any]]:
        """Courses with their years, divisions and batches (for the sign-up form)."""
        return academics.list_courses(self._session_factory)

    def get_my_courses(self) -> list[dict[str, Any]]:
        """The courses this professor may teach and aim labs at."""
        return academics.courses_for_professor(
            self._session_factory,
            self._require_professor_id(),
            every_course=not self._restrict_signup,
        )

    def get_course_catalog(self) -> list[dict[str, Any]]:
        """Every course, marked with the ones this professor teaches (for the picker)."""
        return academics.catalog_for_professor(self._session_factory, self._require_professor_id())

    def set_my_courses(self, course_ids: list[int]) -> dict[str, Any]:
        """The professor says which courses they teach; those courses' students join their class."""
        professor_id = self._require_professor_id()
        try:
            result = academics.set_professor_courses(
                self._session_factory, professor_id, [int(i) for i in course_ids]
            )
        except academics.AcademicError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}

    def get_class_groups(self) -> list[dict[str, Any]]:
        return academics.professor_groups(self._session_factory, self._require_professor_id())

    def preview_class_group(self, group: dict[str, Any]) -> dict[str, Any]:
        professor_id = self._require_professor_id()
        try:
            counts = academics.preview_group(
                self._session_factory, professor_id, group, every_course=not self._restrict_signup
            )
        except academics.AcademicError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **counts}

    def add_class_group(self, group: dict[str, Any]) -> dict[str, Any]:
        """Take every student of a course / year / division / batch into this class."""
        professor_id = self._require_professor_id()
        try:
            result = academics.add_group(
                self._session_factory, professor_id, group, every_course=not self._restrict_signup
            )
        except academics.AcademicError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}

    def remove_class_group(self, group_id: int) -> dict[str, Any]:
        professor_id = self._require_professor_id()
        try:
            academics.remove_group(self._session_factory, professor_id, int(group_id))
        except academics.AcademicError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def add_students_to_class(self, student_ids: list[int]) -> dict[str, Any]:
        professor_id = self._require_professor_id()
        try:
            added = resource_store.add_to_class(
                self._session_factory, professor_id, [int(i) for i in student_ids]
            )
        except resource_store.ResourceError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "added": added}

    def remove_student_from_class(self, student_id: int) -> dict[str, Any]:
        professor_id = self._require_professor_id()
        try:
            resource_store.remove_from_class(self._session_factory, professor_id, int(student_id))
        except resource_store.ResourceError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def get_my_teacher(self) -> str | None:
        """The professor whose class the signed-in student is in (None if nobody's yet)."""
        return resource_store.student_teacher(self._session_factory, self._require_student_id())

    def reset_student_password(self, student_id: int) -> dict[str, Any]:
        """Set a temporary password for a student in this professor's class.

        Returned once, in the clear, so the professor can pass it on; the
        student is made to replace it at next sign-in.
        """
        professor_id = self._require_professor_id()
        with self._session_factory() as db_session:
            student = db_session.get(Student, student_id)
            if student is None or not resource_store.is_member(
                db_session, student_id, professor_id
            ):
                return {"ok": False, "error": "That student isn't in your class."}
        try:
            temporary = reset_student_password_row(self._session_factory, student_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "temporary_password": temporary}

    # -- resources ---------------------------------------------------------------------

    @staticmethod
    def _resource_input(spec: dict[str, Any]) -> resource_store.ResourceInput:
        raw = spec.get("data_base64")
        data: bytes | None = None
        if raw:
            try:
                data = base64.b64decode(str(raw), validate=True)
            except (binascii.Error, ValueError):
                raise resource_store.ResourceError("That file couldn't be read.") from None
        return resource_store.ResourceInput(
            kind=str(spec.get("kind", "")),
            title=str(spec.get("title", "")),
            description=spec.get("description"),
            url=spec.get("url"),
            body=spec.get("body"),
            filename=spec.get("filename"),
            mime_type=spec.get("mime_type"),
            data=data,
            audience_all=bool(spec.get("audience_all", True)),
            student_ids=[int(i) for i in spec.get("student_ids") or []],
            task_ids=[int(i) for i in spec.get("task_ids") or []],
        )

    def get_my_resources(self) -> list[dict[str, Any]]:
        """Everything this professor has shared (professors only)."""
        return resource_store.professor_resources(
            self._session_factory, self._require_professor_id()
        )

    def create_resource(self, spec: dict[str, Any]) -> dict[str, Any]:
        professor_id = self._require_professor_id()
        try:
            new_id = resource_store.create_resource(
                self._session_factory, professor_id, self._resource_input(spec)
            )
        except resource_store.ResourceError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": new_id}

    def update_resource(self, resource_id: int, spec: dict[str, Any]) -> dict[str, Any]:
        professor_id = self._require_professor_id()
        try:
            resource_store.update_resource(
                self._session_factory, professor_id, int(resource_id), self._resource_input(spec)
            )
        except resource_store.ResourceError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def delete_resource(self, resource_id: int) -> dict[str, Any]:
        professor_id = self._require_professor_id()
        try:
            resource_store.delete_resource(self._session_factory, professor_id, int(resource_id))
        except resource_store.ResourceError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def get_student_resources(self) -> list[dict[str, Any]]:
        """Resources shared with the signed-in student (by their professor)."""
        return resource_store.student_resources(self._session_factory, self._require_student_id())

    def get_lab_resources(self, task_id: int) -> list[dict[str, Any]]:
        """Resources attached to one lab that the signed-in person may see."""
        self._require_logged_in()
        if self._current_professor_id is not None:
            return resource_store.professor_resources(
                self._session_factory, self._current_professor_id, int(task_id)
            )
        return resource_store.student_resources(
            self._session_factory, self._require_student_id(), int(task_id)
        )

    def get_resource_file(self, resource_id: int) -> dict[str, Any]:
        """An uploaded file's bytes (base64), if the signed-in person may open it."""
        self._require_logged_in()
        role, user_id = ("professor", self._current_professor_id)
        if user_id is None:
            role, user_id = "student", self._require_student_id()
        try:
            filename, mime_type, data = resource_store.resource_file(
                self._session_factory, role=role, user_id=user_id, resource_id=int(resource_id)
            )
        except resource_store.ResourceError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "filename": filename,
            "mime_type": mime_type,
            "data_base64": base64.b64encode(data).decode("ascii"),
        }

    def logout(self) -> None:
        self._current_student_id, self._current_student_name = None, None
        self._current_professor_id, self._current_professor_name = None, None

    def _require_student_id(self) -> int:
        if self._current_student_id is None:
            raise ValueError("Not logged in as a student")
        return self._current_student_id

    def _require_professor_id(self) -> int:
        if self._current_professor_id is None:
            raise ValueError("Not logged in as a professor")
        return self._current_professor_id

    def _require_session_owner(self, session_id: int) -> None:
        """Students may only add to their own sessions."""
        student_id = self._require_student_id()
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            if session is None or session.student_id != student_id:
                raise ValueError("That session isn't yours.")

    def _require_logged_in(self) -> None:
        if self._current_student_id is None and self._current_professor_id is None:
            raise ValueError("Not logged in")

    # -- bootstrap / navigation ------------------------------------------

    def get_student_name(self) -> str:
        return self._current_student_name or "Student"

    def get_labs(self) -> list[dict[str, Any]]:
        with self._session_factory() as db_session:
            student_id = self._current_student_id
            me = db_session.get(Student, student_id) if student_id is not None else None
            tasks = [
                task
                for task in db_session.query(Task).all()
                if _is_lab(task)
                and not _is_archived(task)
                and (me is None or academics.lab_visible_to(me, task))
            ]
            return [
                {
                    "id": task.id,
                    "title": task.title,
                    "learning_objective": task.learning_objective,
                    "professor_name": task.professor_name,
                    "stage_count": len(task.stages),
                    "status": self._lab_status(db_session, student_id, task),
                }
                for task in tasks
            ]

    def _lab_status(self, db_session: OrmSession, student_id: int | None, task: Task) -> str:
        """``"submitted"``, ``"in_progress"`` or ``"not_started"`` for this student."""
        if student_id is None:
            return "not_started"
        if self._final_submission(db_session, student_id, task) is not None:
            return "submitted"
        started = (
            db_session.query(SessionModel)
            .filter(SessionModel.student_id == student_id, SessionModel.task_id == task.id)
            .first()
        )
        return "in_progress" if started is not None else "not_started"

    @staticmethod
    def _require_lab_access(db_session: OrmSession, student_id: int, task: Task) -> None:
        student = db_session.get(Student, student_id)
        if student is not None and not academics.lab_visible_to(student, task):
            raise ValueError("This lab isn't assigned to you.")

    def get_stages(self, task_id: int) -> dict[str, Any]:
        student_id = self._require_student_id()
        with self._session_factory() as db_session:
            task = db_session.get(Task, task_id)
            if task is None:
                raise ValueError(f"No task with id {task_id}")
            self._require_lab_access(db_session, student_id, task)
            stages = list(task.stages)
            final = self._final_submission(db_session, student_id, task)
            return {
                "task_title": task.title,
                "lab_submitted": final is not None,
                "submitted_at": final.submitted_at.isoformat()
                if final and final.submitted_at
                else None,
                "submitted_session_id": final.id if final else None,
                "submit_note": _SUBMIT_NOTES.get(final.submit_reason or "") if final else None,
                "stages": [
                    {
                        "id": stage.id,
                        "stage_type": stage.stage_type.value,
                        "ai_assistance_mode": stage.ai_assistance_mode.value,
                        "duration_minutes": stage.duration_minutes,
                        "unlocked": is_stage_unlocked(self._session_factory, student_id, stage),
                        "status": stage_status(self._session_factory, student_id, stage.id),
                    }
                    for stage in stages
                ],
            }

    @staticmethod
    def _final_submission(
        db_session: OrmSession, student_id: int, task: Task
    ) -> SessionModel | None:
        """The student's submitted attempt at the lab's last stage, if they've submitted the lab.

        A lab is submitted once (from its last stage), so that is the mark of it
        being done; after that it can't be opened for editing or submitted again.
        """
        if not task.stages:
            return None
        return (
            db_session.query(SessionModel)
            .filter(
                SessionModel.student_id == student_id,
                SessionModel.stage_id == task.stages[-1].id,
                SessionModel.submitted_at.is_not(None),
            )
            .order_by(SessionModel.id.desc())
            .first()
        )

    def get_followup_assessments(self, task_id: int) -> list[dict[str, Any]]:
        """Transfer Tasks / Retention Checks a professor spun off from this Lab.

        Each entry includes its own single stage's id — the frontend starts
        it exactly like any other stage (``start_stage``); nothing about
        Transfer/Retention is special on the taking side, only on the
        scoring side (see ``signals/compute.py``). Read by both roles: a
        student sees these on the Lab's Stages screen to attempt them, and
        a professor sees them on the Lab Report screen to know what they've
        already spun off.
        """
        self._require_logged_in()
        with self._session_factory() as db_session:
            followups = db_session.query(Task).filter_by(linked_task_id=task_id).all()
            return [
                {
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "assessment_kind": task.assessment_kind.value if task.assessment_kind else None,
                    "stage_id": task.stages[0].id if task.stages else None,
                    "ai_assistance_mode": (
                        task.stages[0].ai_assistance_mode.value if task.stages else None
                    ),
                    "duration_minutes": task.stages[0].duration_minutes if task.stages else None,
                    "mode_locked": bool(
                        task.stages
                        and db_session.query(SessionModel)
                        .filter_by(stage_id=task.stages[0].id)
                        .first()
                    ),
                    "submitted_count": len(
                        {
                            sid
                            for (sid,) in db_session.query(SessionModel.student_id).filter(
                                SessionModel.task_id == task.id,
                                SessionModel.submitted_at.is_not(None),
                            )
                        }
                    ),
                }
                for task in followups
                if task.stages
            ]

    # -- professor: lab authoring & reports ---------------------------------

    def create_followup_assessment(self, followup: dict[str, Any]) -> dict[str, Any]:
        """Spin off a Transfer Task or Retention Check from an existing Lab.

        ``followup`` mirrors ``create_lab``'s single-dict convention:
        ``source_task_id``, ``kind`` ("TRANSFER"/"RETENTION"), ``title``,
        ``description``, ``ai_assistance_mode``, ``duration_minutes``.
        """
        self._require_professor_id()
        task_id = create_followup_assessment_row(
            self._session_factory,
            source_task_id=followup["source_task_id"],
            kind=AssessmentKind(followup["kind"]),
            title=followup["title"],
            description=followup.get("description"),
            ai_assistance_mode=AIAssistanceMode(followup.get("ai_assistance_mode", "RESTRICTED")),
            duration_minutes=followup.get("duration_minutes"),
        )
        return {"task_id": task_id}

    def update_followup_assessment(self, task_id: int, followup: dict[str, Any]) -> dict[str, Any]:
        """Edit a follow-up's title, problem, AI mode and time."""
        self._require_professor_id()
        title = str(followup.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "A title is required."}
        try:
            update_followup_row(
                self._session_factory,
                int(task_id),
                title=title,
                description=followup.get("description"),
                ai_assistance_mode=AIAssistanceMode(
                    followup.get("ai_assistance_mode", "RESTRICTED")
                ),
                duration_minutes=followup.get("duration_minutes"),
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def create_lab(self, lab: dict[str, Any]) -> dict[str, Any]:
        """Create a Lab (a Task with its three Stages) from a Create Session form.

        ``lab`` is the whole form payload in one dict — passed as a single
        JSON object rather than one bridge parameter per field, since the
        form has a dozen fields and pywebview marshals every call
        positionally (see ``log_code_edit``'s docstring): a long positional
        list would be exactly the kind of easy-to-miscount call this avoids.
        Expected keys: ``title``, ``course``, ``division``, ``batch``,
        ``topic``, ``description``, ``difficulty``, and ``stages`` — a list
        of three ``{duration_minutes, ai_assistance_mode}`` dicts in
        Learning/Exploration/Assessment order.
        """
        professor_id = self._require_professor_id()
        try:
            target = self._lab_target(professor_id, lab)
        except academics.AcademicError as exc:
            return {"ok": False, "error": str(exc)}
        stage_inputs = lab.get("stages") or []
        stage_types = _STAGE_TYPES
        if len(stage_inputs) != len(stage_types):
            raise ValueError(f"Expected {len(stage_types)} stage configs, got {len(stage_inputs)}")

        stage_plan = tuple(
            (
                stage_type,
                AIAssistanceMode(stage_input["ai_assistance_mode"]),
                stage_input.get("duration_minutes"),
            )
            for stage_type, stage_input in zip(stage_types, stage_inputs, strict=True)
        )
        task_id = create_lab_row(
            self._session_factory,
            title=lab["title"],
            description=lab.get("description"),
            learning_objective=lab.get("topic"),
            difficulty=lab.get("difficulty"),
            professor_name=lab.get("professor_name") or self._current_professor_name,
            course=target["course"],
            division=target["division"],
            batch=target["batch"],
            stage_plan=stage_plan,
            course_id=target["course_id"],
            year=target["year"],
            # On a classroom server a lab with no course goes to its professor's
            # class; on a single computer everyone sees every lab.
            professor_id=professor_id if self._restrict_signup else None,
        )
        return {"ok": True, "task_id": task_id}

    def _lab_target(self, professor_id: int, lab: dict[str, Any]) -> dict[str, Any]:
        """Who a lab is for: a course (and optionally year, division, batch) the professor teaches.

        Without a course the lab simply goes to the professor's own class.
        """
        mine = self.get_my_courses()
        if lab.get("course_id") in (None, ""):
            if mine:
                raise academics.AcademicError("Choose which course this lab is for.")
            return {
                "course": lab.get("course"),
                "division": lab.get("division"),
                "batch": lab.get("batch"),
                "course_id": None,
                "year": None,
            }
        place = academics.lab_target(self._session_factory, lab)
        if place["course_id"] not in {c["id"] for c in mine}:
            raise academics.AcademicError("You haven't been assigned that course.")
        return {
            "course": place["course_name"],
            "division": place["division"],
            "batch": place["batch"],
            "course_id": place["course_id"],
            "year": place["year"],
        }

    def get_lab(self, task_id: int) -> dict[str, Any]:
        """Everything the Edit form needs, in ``create_lab``'s payload shape."""
        self._require_professor_id()
        with self._session_factory() as db_session:
            task = db_session.get(Task, task_id)
            if task is None or not _is_lab(task):
                raise ValueError(f"No lab with id {task_id}")
            started_stage_ids = {
                stage_id
                for (stage_id,) in db_session.query(SessionModel.stage_id)
                .filter(SessionModel.stage_id.in_([stage.id for stage in task.stages]))
                .distinct()
            }
            return {
                "id": task.id,
                "title": task.title,
                "course": task.course,
                "course_id": task.course_id,
                "year": task.year,
                "division": task.division,
                "batch": task.batch,
                "topic": task.learning_objective,
                "description": task.description,
                "difficulty": task.difficulty,
                "archived": _is_archived(task),
                "stages": [
                    {
                        "stage_type": stage.stage_type.value,
                        "duration_minutes": stage.duration_minutes,
                        "ai_assistance_mode": stage.ai_assistance_mode.value,
                        "mode_locked": stage.id in started_stage_ids,
                    }
                    for stage in task.stages
                ],
            }

    def update_lab(self, task_id: int, lab: dict[str, Any]) -> dict[str, Any]:
        """Save an edited lab. ``lab`` has the same keys as ``create_lab``'s payload."""
        professor_id = self._require_professor_id()
        title = (lab.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "Session Title is required."}
        try:
            target = self._lab_target(professor_id, lab)
        except academics.AcademicError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            update_lab_row(
                self._session_factory,
                task_id,
                title=title,
                description=lab.get("description"),
                learning_objective=lab.get("topic"),
                difficulty=lab.get("difficulty"),
                course=target["course"],
                division=target["division"],
                batch=target["batch"],
                stage_plan=tuple(
                    (AIAssistanceMode(stage["ai_assistance_mode"]), stage.get("duration_minutes"))
                    for stage in lab.get("stages") or []
                ),
                retarget=True,
                course_id=target["course_id"],
                year=target["year"],
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def archive_lab(self, task_id: int) -> dict[str, Any]:
        self._require_professor_id()
        set_lab_archived_row(self._session_factory, task_id, archived=True)
        return {"ok": True}

    def unarchive_lab(self, task_id: int) -> dict[str, Any]:
        self._require_professor_id()
        set_lab_archived_row(self._session_factory, task_id, archived=False)
        return {"ok": True}

    def get_lab_report_csv(self, task_id: int) -> dict[str, Any]:
        """A lab's report as CSV text plus a suggested file name (professors only)."""
        self._require_professor_id()
        report = self.get_lab_report(task_id)
        safe_title = "".join(c if c.isalnum() or c in "-." else "_" for c in report["task_title"])
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(["Student Name", "Enrolment No.", "Score", "Submitted On", "Status"])
        for row in report["rows"]:
            writer.writerow(
                [
                    _csv_safe(row["student_name"]),
                    _csv_safe(row["enrollment_no"]),
                    _csv_safe(row["score"]),
                    _csv_safe(row["submitted_at"]),
                    _csv_safe(row["status"]),
                ]
            )
        return {"filename": f"{safe_title}_report.csv", "csv": buffer.getvalue()}

    def export_lab_report(self, task_id: int) -> dict[str, Any]:
        """Write a lab's report as a CSV file the professor picks a location for."""
        return save_text_file(self._save_file_dialog, self.get_lab_report_csv(task_id))

    def get_professor_labs(self, include_archived: bool = False) -> list[dict[str, Any]]:
        self._require_professor_id()
        with self._session_factory() as db_session:
            tasks = [
                task
                for task in db_session.query(Task).all()
                if _is_lab(task) and (include_archived or not _is_archived(task))
            ]
            return [
                {
                    "id": task.id,
                    "title": task.title,
                    "course": task.course,
                    "division": task.division,
                    "batch": task.batch,
                    "archived": _is_archived(task),
                }
                for task in tasks
            ]

    def _dashboard_rows_for_task(
        self, db_session: OrmSession, task: Task
    ) -> tuple[set[int], list[float], int, list[dict[str, Any]]]:
        """Per-task slice of the Home dashboard aggregation: (student_ids, graded_scores,
        pending_count, activity_rows)."""
        student_ids: set[int] = set()
        graded_scores: list[float] = []
        pending_count = 0
        activity_rows: list[dict[str, Any]] = []

        for session in self._latest_assessment_session_per_student(db_session, task).values():
            student_ids.add(session.student_id)
            if session.submitted_at is None:
                pending_count += 1
                continue
            score = self._overall_score(session.id)
            if score is not None:
                graded_scores.append(score)
            student = db_session.get(Student, session.student_id)
            activity_rows.append(
                {
                    "student_name": student.display_name if student else "Unknown",
                    "lab_title": task.title,
                    "score": score,
                    "submitted_at": session.submitted_at.isoformat(),
                }
            )
        return student_ids, graded_scores, pending_count, activity_rows

    def get_professor_dashboard_summary(self) -> dict[str, Any]:
        """Cross-lab overview for the Home screen (distinct from My Labs' per-lab table).

        Pulls the same per-lab report data ``get_lab_report`` does, just
        rolled up across every lab this professor has authored, plus a
        short recent-activity feed (latest assessment submissions, most
        recent first) so Home reads as "what's happening" rather than
        duplicating My Labs' management table.
        """
        self._require_professor_id()
        with self._session_factory() as db_session:
            tasks = [
                task
                for task in db_session.query(Task).all()
                if _is_lab(task) and not _is_archived(task)
            ]

            student_ids: set[int] = set()
            all_graded_scores: list[float] = []
            pending_count = 0
            recent_activity: list[dict[str, Any]] = []
            for task in tasks:
                task_students, task_scores, task_pending, task_activity = (
                    self._dashboard_rows_for_task(db_session, task)
                )
                student_ids |= task_students
                all_graded_scores.extend(task_scores)
                pending_count += task_pending
                recent_activity.extend(task_activity)

            recent_activity.sort(key=lambda row: row["submitted_at"], reverse=True)
            return {
                "total_labs": len(tasks),
                "total_students": len(student_ids),
                "average_score": round(sum(all_graded_scores) / len(all_graded_scores), 1)
                if all_graded_scores
                else None,
                "pending_submissions": pending_count,
                "recent_activity": recent_activity[:5],
            }

    def _latest_assessment_session_per_student(
        self, db_session: OrmSession, task: Task
    ) -> dict[int, SessionModel]:
        assessment_stage = next(
            (s for s in task.stages if s.stage_type == StageType.ASSESSMENT), None
        )
        if assessment_stage is None:
            return {}
        sessions = (
            db_session.query(SessionModel)
            .filter_by(stage_id=assessment_stage.id)
            .order_by(SessionModel.id)
            .all()
        )
        latest_by_student: dict[int, SessionModel] = {}
        for session in sessions:
            latest_by_student[session.student_id] = session
        return latest_by_student

    def _report_row(self, db_session: OrmSession, session: SessionModel) -> dict[str, Any]:
        student = db_session.get(Student, session.student_id)
        submitted = session.submitted_at is not None
        return {
            "student_id": session.student_id,
            "session_id": session.id,
            "student_name": student.display_name if student else "Unknown",
            "enrollment_no": student.enrollment_no if student else None,
            "score": self._overall_score(session.id) if submitted else None,
            "submitted_at": session.submitted_at.isoformat() if session.submitted_at else None,
            "status": "Submitted" if submitted else "Not Submitted",
            "note": _SUBMIT_NOTES.get(session.submit_reason or ""),
        }

    def get_lab_report(self, task_id: int) -> dict[str, Any]:
        self._require_professor_id()
        with self._session_factory() as db_session:
            task = db_session.get(Task, task_id)
            if task is None:
                raise ValueError(f"No task with id {task_id}")

            latest_by_student = self._latest_assessment_session_per_student(db_session, task)
            rows = [self._report_row(db_session, session) for session in latest_by_student.values()]

            submitted_rows = [row for row in rows if row["status"] == "Submitted"]
            # A submitted session can still have no computable score yet (no
            # edits/runs/AI use beyond the submit itself) — every signal
            # stays `None` rather than 0, so exclude those from the average
            # instead of letting them drag it toward zero.
            graded_scores = [row["score"] for row in submitted_rows if row["score"] is not None]
            average_score = (
                round(sum(graded_scores) / len(graded_scores), 1) if graded_scores else None
            )
            return {
                "task_title": task.title,
                "course": task.course,
                "division": task.division,
                "batch": task.batch,
                "total_students": len(rows),
                "submitted_count": len(submitted_rows),
                "not_submitted_count": len(rows) - len(submitted_rows),
                "average_score": average_score,
                "rows": rows,
            }

    def _overall_score(self, session_id: int) -> float | None:
        # A naive equal-weighted average over whatever signals are
        # currently computed — the framework is explicit that real
        # aggregation weights are an open empirical question (see
        # `docs/The EAAL Framework.pdf` §6.7), so this single number is a
        # provisional convenience for the report table, not a validated
        # score. The full per-signal breakdown remains the source of truth
        # (see `get_ciq_score`).
        self._event_logger.flush()
        results = compute_all_signals(self._session_factory, session_id, self._ai_provider)
        values = [result.value for result in results.values() if result.value is not None]
        if not values:
            return None
        return round(sum(values) / len(values) * 100, 1)

    # -- session lifecycle -------------------------------------------------

    def start_practice(self) -> dict[str, Any]:
        session_id = start_practice_session(self._session_factory, self._require_student_id())
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            if session is None:
                raise ValueError(f"Session {session_id} was just created but cannot be found")
            task = session.task
            self._log_event(session_id, EventType.TASK_START)
            return {
                "session_id": session_id,
                "stage_id": None,
                "task_title": task.title,
                "task_description": task.description,
                "starter_files": {_ENTRY_FILENAME: task.starter_code or ""},
                "ai_assistance_mode": AIAssistanceMode.FULL.value,
                "is_stage": False,
                "difficulty": task.difficulty,
                "duration_minutes": None,
                "stage_type": None,
                "task_id": task.id,
            }

    def start_stage(self, stage_id: int) -> dict[str, Any]:
        """Open a stage of a lab, picking up the student's unfinished attempt if there is one.

        A lab's stages are done in order and submitted together at the end
        (see ``submit_session``), so moving between them keeps the code.
        """
        student_id = self._require_student_id()
        with self._session_factory() as db_session:
            target = db_session.get(Stage, stage_id)
            if target is None:
                raise ValueError(f"No stage with id {stage_id}")
            if _is_archived(target.task):
                raise ValueError("This lab has been archived by your professor.")
            self._require_lab_access(db_session, student_id, target.task)
            if self._final_submission(db_session, student_id, target.task) is not None:
                raise ValueError("You have already submitted this lab.")
        session_id, resumed = resume_or_start_stage_session(
            self._session_factory, student_id, stage_id
        )
        with self._session_factory() as db_session:
            stage = db_session.get(Stage, stage_id)
            if stage is None:
                raise ValueError(f"No stage with id {stage_id}")
            task = stage.task
            started_at = db_session.get(SessionModel, session_id).started_at  # type: ignore[union-attr]
            latest = (
                db_session.query(CodeSnapshot)
                .filter_by(session_id=session_id)
                .order_by(CodeSnapshot.id.desc())
                .first()
            )
            files = {_ENTRY_FILENAME: task.starter_code or ""}
            if resumed and latest is not None:
                files = {**files, **_unbundle_files(latest.content)}
            if not resumed:
                self._log_event(session_id, EventType.TASK_START)
            return {
                "session_id": session_id,
                "stage_id": stage.id,
                "task_title": f"{task.title} — {stage.stage_type.value.title()}",
                "task_description": task.description,
                "starter_files": files,
                "resumed": resumed,
                "elapsed_seconds": _seconds_since(started_at) if resumed else 0,
                "ai_assistance_mode": stage.ai_assistance_mode.value,
                "is_stage": True,
                "difficulty": task.difficulty,
                "duration_minutes": stage.duration_minutes,
                "stage_type": stage.stage_type.value,
                "task_id": task.id,
            }

    # -- editing / running --------------------------------------------------

    def log_code_edit(
        self,
        session_id: int,
        files: dict[str, str],
        active_filename: str | None = None,
        reset: bool = False,
        manual: bool = False,
    ) -> dict[str, Any]:
        self._require_session_owner(session_id)
        # Deliberately not keyword-only: pywebview's JS bridge marshals every
        # call as a flat positional-argument list (it slices `arguments` on
        # the JS side and applies it as `func(*args)` on the Python side —
        # there is no keyword-argument channel across that boundary), so a
        # keyword-only parameter here is simply never reachable from the
        # frontend. Python callers (tests) can still pass these by name.
        snapshot_id = self._save_snapshot(session_id, files, active_filename=active_filename)
        payload: dict[str, object] | None = None
        if reset:
            payload = {"reset": True}
        elif manual:
            payload = {"manual": True}
        self._log_event(
            session_id, EventType.CODE_EDIT, code_version_id=snapshot_id, payload=payload
        )
        return {"snapshot_id": snapshot_id}

    def prepare_run(
        self, session_id: int, files: dict[str, str], entry_filename: str = _ENTRY_FILENAME
    ) -> dict[str, Any]:
        """Record that code is about to run; the caller then runs it and calls ``record_run``."""
        self._require_session_owner(session_id)
        if entry_filename not in files:
            entry_filename = _ENTRY_FILENAME
        snapshot_id = self._save_snapshot(session_id, files, active_filename=entry_filename)
        self._log_event(session_id, EventType.CODE_RUN, code_version_id=snapshot_id)
        return {"snapshot_id": snapshot_id, "entry_filename": entry_filename}

    def record_run(
        self,
        session_id: int,
        snapshot_id: int,
        entry_filename: str,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        """Store the result of a run (``outcome`` has stdout, stderr, exit_status, ...)."""
        self._require_session_owner(session_id)
        stdout = str(outcome.get("stdout", ""))
        stderr = str(outcome.get("stderr", ""))
        duration_ms = int(outcome.get("duration_ms", 0))
        timed_out = bool(outcome.get("timed_out", False))
        exit_status = outcome.get("exit_status")
        with self._session_factory() as db_session:
            db_session.add(
                ExecutionResult(
                    session_id=session_id,
                    code_version_id=snapshot_id,
                    stdout=stdout,
                    stderr=stderr,
                    exit_status=exit_status,
                    duration_ms=duration_ms,
                    timed_out=timed_out,
                )
            )
            db_session.commit()

        self._log_event(
            session_id,
            EventType.EXECUTION_RESULT,
            code_version_id=snapshot_id,
            payload={
                "exit_status": exit_status,
                "duration_ms": duration_ms,
                "timed_out": timed_out,
                # Whether the run actually produced output — S3.2 (Knowledge
                # Application) uses this so a no-op program (e.g. bare
                # `pass`) that merely exits cleanly can't score the same as
                # one that did something.
                "has_output": bool(stdout.strip()),
            },
        )
        return {
            "entry_filename": entry_filename,
            "stdout": stdout,
            "stderr": stderr,
            "exit_status": exit_status,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
        }

    def run_code(
        self, session_id: int, files: dict[str, str], entry_filename: str = _ENTRY_FILENAME
    ) -> dict[str, Any]:
        """Run code on this machine (standalone mode) and record the result."""
        prepared = self.prepare_run(session_id, files, entry_filename)
        outcome = sandbox_run_code(files, entry_filename=prepared["entry_filename"])
        return self.record_run(
            session_id,
            prepared["snapshot_id"],
            prepared["entry_filename"],
            {
                "stdout": outcome.stdout,
                "stderr": outcome.stderr,
                "exit_status": outcome.exit_status,
                "duration_ms": outcome.duration_ms,
                "timed_out": outcome.timed_out,
            },
        )

    def submit_session(
        self, session_id: int, files: dict[str, str], reason: str | None = None
    ) -> dict[str, Any]:
        """Submit a session. For a lab this submits the whole lab.

        ``reason="focus"`` marks a submit the app made for the student because
        they left the lab window twice; it is only believed when the server has
        counted that many departures.

        Students move through a lab's stages (learning, exploration,
        assessment) and submit once, from the last one. That submit also
        marks the student's unfinished attempts at the earlier stages as
        submitted, so each stage still ends up with its own submitted
        session for scoring and the professor's reports.
        """
        self._require_session_owner(session_id)
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            if session is not None and session.stage is not None:
                done = self._final_submission(db_session, session.student_id, session.task)
                if done is not None:
                    raise ValueError("You have already submitted this lab.")
        snapshot_id = self._save_snapshot(session_id, files)
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            earlier: list[tuple[int, int | None]] = []
            if session is not None:
                now = datetime.now(UTC)
                session.submitted_at = now
                if reason == "focus" and self._focus_losses(db_session, session) >= _FOCUS_LIMIT:
                    session.submit_reason = "focus"
                    log.warning("lab submitted automatically", extra={"session": session_id})
                if session.stage is not None:
                    earlier = self._submit_other_stages(db_session, session, now)
            db_session.commit()
        for earlier_id, earlier_snapshot in earlier:
            self._log_event(earlier_id, EventType.SUBMISSION, code_version_id=earlier_snapshot)
        self._log_event(session_id, EventType.SUBMISSION, code_version_id=snapshot_id)
        return {"ok": True}

    @staticmethod
    def _submit_other_stages(
        db_session: OrmSession, session: SessionModel, now: datetime
    ) -> list[tuple[int, int | None]]:
        """Submit the lab's other stages along with this one.

        A normal submit comes from the last stage and sweeps up the earlier ones. A
        forced one (the student left the window twice) ends the whole lab, so it covers
        every stage, and a stage that was never opened is submitted empty.
        Returns ``(session id, last snapshot id)`` for each one touched.
        """
        forced = session.submit_reason == "focus"
        swept: list[tuple[int, int | None]] = []
        for stage in session.task.stages:
            if stage.id == session.stage_id:
                continue
            if not forced and stage.order_index >= session.stage.order_index:  # type: ignore[union-attr]
                continue
            attempts = db_session.query(SessionModel).filter(
                SessionModel.student_id == session.student_id, SessionModel.stage_id == stage.id
            )
            open_attempt = (
                attempts.filter(SessionModel.submitted_at.is_(None))
                .order_by(SessionModel.id.desc())
                .first()
            )
            if open_attempt is None:
                if not forced or attempts.first() is not None:
                    continue
                open_attempt = SessionModel(
                    student_id=session.student_id,
                    task_id=session.task_id,
                    stage_id=stage.id,
                    started_at=now,
                )
                db_session.add(open_attempt)
                db_session.flush()
            open_attempt.submitted_at = now
            open_attempt.submit_reason = session.submit_reason
            last = (
                db_session.query(CodeSnapshot)
                .filter_by(session_id=open_attempt.id)
                .order_by(CodeSnapshot.id.desc())
                .first()
            )
            swept.append((open_attempt.id, last.id if last else None))
        return swept

    # -- keeping the student in the lab ---------------------------------------

    @staticmethod
    def _focus_losses(db_session: OrmSession, session: SessionModel) -> int:
        """Times this student has left the lab window, over every stage of the lab."""
        total = (
            db_session.query(func.coalesce(func.sum(SessionModel.focus_losses), 0))
            .filter_by(student_id=session.student_id, task_id=session.task_id)
            .scalar()
        )
        return int(total or 0)

    def get_lab_policy(self) -> dict[str, Any]:
        """Whether the administrator has lab mode (full screen, key lock, focus rule) on."""
        return {"lab_mode": app_settings.get_flag(self._session_factory, app_settings.LAB_MODE)}

    def record_focus_lost(self, session_id: int) -> dict[str, Any]:
        """The lab window lost focus. The first time is a warning, the second ends the lab.

        Counted on the server over the whole lab (not per screen), so reopening
        a stage does not give the student their warning back.
        """
        self._require_session_owner(session_id)
        if not app_settings.get_flag(self._session_factory, app_settings.LAB_MODE):
            return {"count": 0, "limit": _FOCUS_LIMIT, "action": "none"}
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            if session is None or session.stage is None:
                return {"count": 0, "limit": _FOCUS_LIMIT, "action": "none"}
            session.focus_losses = (session.focus_losses or 0) + 1
            db_session.commit()
            count = self._focus_losses(db_session, session)
        log.info(
            "lab window left",
            extra={"session": session_id, "count": count, "limit": _FOCUS_LIMIT},
        )
        self._log_event(session_id, EventType.FOCUS_LOST, payload={"count": count})
        return {
            "count": count,
            "limit": _FOCUS_LIMIT,
            "action": "submit" if count >= _FOCUS_LIMIT else "warn",
        }

    def record_paste(self, session_id: int, text: str) -> dict[str, Any]:
        """Text was pasted into the editor: note whether it came from the AI's replies."""
        self._require_session_owner(session_id)
        text = str(text or "")[:20000]
        if not text.strip():
            return {"source": "none"}
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            if session is None:
                return {"source": "none"}
            replies = [
                reply
                for (reply,) in db_session.query(AIInteraction.response)
                .join(SessionModel, SessionModel.id == AIInteraction.session_id)
                .filter(SessionModel.student_id == session.student_id)
                .order_by(AIInteraction.id.desc())
                .limit(200)
                if reply
            ]
        source = "ai" if _paste_comes_from_ai(text, replies) else "other"
        self._log_event(
            session_id,
            EventType.PASTE,
            payload={"source": source, "chars": len(text), "lines": len(text.splitlines())},
        )
        return {"source": source}

    def get_concept_check_question(self) -> str:
        return _CONCEPT_CHECK_QUESTION

    def submit_concept_check(self, session_id: int, response_text: str) -> dict[str, Any]:
        """Store the student's explanation of their solution — S3.1's evidence.

        One row per session: if this session already has a response (e.g.
        the student edited their explanation before the final Submit),
        update it in place rather than accumulating duplicates.
        """
        self._require_session_owner(session_id)
        self._require_student_id()
        with self._session_factory() as db_session:
            existing = (
                db_session.query(ConceptCheckResponse).filter_by(session_id=session_id).first()
            )
            if existing is not None:
                existing.response_text = response_text
            else:
                db_session.add(
                    ConceptCheckResponse(
                        session_id=session_id,
                        question=_CONCEPT_CHECK_QUESTION,
                        response_text=response_text,
                    )
                )
            db_session.commit()
        return {"ok": True}

    # -- AI chat --------------------------------------------------------

    def ping_ai(self) -> bool:
        return self._ai_provider.diagnose() is None

    def get_ai_settings(self) -> dict[str, Any]:
        problem = self._ai_provider.diagnose()
        return {
            "provider": self._ai_provider.provider_name,
            "model": self._ai_provider.model_name,
            "available": problem is None,
            "problem": problem,
        }

    def set_ai_provider(self, provider: str, api_key: str = "", model: str = "") -> dict[str, Any]:
        """Switch the assistant's backend for the rest of this app session.

        A key is accepted only if the provider answers with it, and is held
        in memory only, never written to the database or disk.
        """
        if self._professors_set_ai:
            self._require_professor_id()
        candidate: AIProvider
        if provider == "ollama":
            candidate = OllamaProvider()
        elif provider in cloud_providers.PROVIDERS and provider != "ollama":
            check = cloud_providers.check_key(provider, api_key, self._ai_http)
            if not check.ok:
                return {"ok": False, "error": check.error}
            chosen = model.strip() or check.default
            if chosen not in check.models:
                return {"ok": False, "error": "That model isn't available to this key."}
            candidate = cloud_providers.make_cloud_provider(
                provider, api_key, chosen, self._ai_http
            )
        else:
            raise ValueError(f"Unknown AI provider {provider!r}")
        self._ai_provider = candidate
        return {"ok": True, **self.get_ai_settings()}

    def prepare_ai_message(
        self, session_id: int, message: str, files: dict[str, str]
    ) -> dict[str, Any]:
        """First half of asking the assistant: check it's allowed, record the prompt, build context.

        The caller then gets the answer from whichever AI it uses (the class
        assistant on the server, or the student's own on their computer) and
        reports it with ``record_ai_message``.
        """
        self._require_session_owner(session_id)
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            if session is None:
                raise ValueError(f"No session with id {session_id}")
            task_description = session.task.description
            mode = session.stage.ai_assistance_mode if session.stage is not None else None

        if mode in (AIAssistanceMode.RESTRICTED, AIAssistanceMode.NONE):
            # Server-side enforcement, not just a disabled button in the
            # frontend — the frontend already prevents this, but the rule
            # about what's allowed in a given stage belongs here too.
            return {"allowed": False, "error": "AI assistance is restricted during this stage."}

        snapshot_id = self._save_snapshot(session_id, files)
        had_recent_error = self._last_execution_had_error(session_id)
        self._log_event(
            session_id,
            EventType.AI_PROMPT,
            code_version_id=snapshot_id,
            payload={"had_recent_error": had_recent_error},
        )
        context = self._build_ai_context(session_id, task_description, files)
        return {
            "allowed": True,
            "snapshot_id": snapshot_id,
            "context": {
                "task_description": context.task_description,
                "current_code": context.current_code,
                "recent_stdout": context.recent_stdout,
                "recent_stderr": context.recent_stderr,
            },
        }

    def record_ai_message(
        self,
        session_id: int,
        snapshot_id: int,
        message: str,
        result: dict[str, Any],
        provider: str | None,
        model: str | None,
    ) -> dict[str, Any]:
        """Second half: store the assistant's answer (or why there wasn't one)."""
        self._require_session_owner(session_id)
        available = bool(result.get("available"))
        text = str(result.get("text") or "")
        error = None if available else str(result.get("error") or "") or None
        self._log_event(
            session_id,
            EventType.AI_RESPONSE,
            code_version_id=snapshot_id,
            payload={"available": available, "error": error},
        )
        response_or_error = text if available else (error or "")
        with self._session_factory() as db_session:
            db_session.add(
                AIInteraction(
                    session_id=session_id,
                    prompt=message,
                    response=response_or_error if available else None,
                    provider=(provider or "")[:50] or None,
                    model=(model or "")[:100] or None,
                    code_version_before_id=snapshot_id,
                    code_version_after_id=snapshot_id,
                )
            )
            db_session.commit()
        return {"available": available, "text": response_or_error}

    def send_ai_message(
        self, session_id: int, message: str, files: dict[str, str]
    ) -> dict[str, Any]:
        """Ask the class (or this app's) assistant."""
        prepared = self.prepare_ai_message(session_id, message, files)
        if not prepared["allowed"]:
            return {"available": False, "error": prepared["error"]}
        result = self._ai_provider.generate(
            message, GenerationContext(**prepared["context"]), Purpose.CHAT
        )
        return self.record_ai_message(
            session_id,
            prepared["snapshot_id"],
            message,
            {"available": result.available, "text": result.text, "error": result.error},
            self._ai_provider.provider_name,
            self._ai_provider.model_name,
        )

    def _build_ai_context(
        self, session_id: int, task_description: str | None, files: dict[str, str]
    ) -> GenerationContext:
        with self._session_factory() as db_session:
            last_result = (
                db_session.query(ExecutionResult)
                .filter_by(session_id=session_id)
                .order_by(ExecutionResult.timestamp.desc())
                .first()
            )
            return GenerationContext(
                task_description=task_description,
                current_code=_bundle_files(files),
                recent_stdout=last_result.stdout if last_result else None,
                recent_stderr=last_result.stderr if last_result else None,
            )

    def _last_execution_had_error(self, session_id: int) -> bool:
        with self._session_factory() as db_session:
            last_result = (
                db_session.query(ExecutionResult)
                .filter_by(session_id=session_id)
                .order_by(ExecutionResult.timestamp.desc())
                .first()
            )
            return bool(last_result and last_result.stderr)

    # -- results --------------------------------------------------------

    def get_submission_summary(self, session_id: int) -> dict[str, Any]:
        self._require_session_owner(session_id)
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            submitted_at = (
                session.submitted_at.isoformat() if session and session.submitted_at else None
            )

            last_result = (
                db_session.query(ExecutionResult)
                .filter_by(session_id=session_id)
                .order_by(ExecutionResult.timestamp.desc())
                .first()
            )
            exit_status = None
            if last_result is not None:
                exit_status = (
                    "Timed out" if last_result.timed_out else f"Exit code {last_result.exit_status}"
                )
            if session is None or session.stage is None:
                label = "Practice"
            else:
                label = {
                    StageType.LEARNING: "Learning stage",
                    StageType.EXPLORATION: "Exploration stage",
                    StageType.ASSESSMENT: "Assessment",
                }[session.stage.stage_type]
            return {"submitted_at": submitted_at, "exit_status": exit_status, "label": label}

    def get_ciq_score(self, session_id: int) -> dict[str, Any]:
        self._require_session_owner(session_id)
        # Events are written by a background thread; make sure the latest ones
        # are saved before they are counted.
        self._event_logger.flush()
        with self._session_factory() as db_session:
            event_count = db_session.query(Event).filter_by(session_id=session_id).count()
            snapshot_count = db_session.query(CodeSnapshot).filter_by(session_id=session_id).count()
            interaction_count = (
                db_session.query(AIInteraction).filter_by(session_id=session_id).count()
            )

        results = compute_all_signals(self._session_factory, session_id, self._ai_provider)
        persist_signal_scores(self._session_factory, session_id, results)

        return {
            "evidence": {
                "event_count": event_count,
                "snapshot_count": snapshot_count,
                "interaction_count": interaction_count,
            },
            "pillars": [
                {
                    "heading": heading,
                    "question": question,
                    "signals": [
                        {
                            "key": key,
                            "value": results[key].value,
                            "reason": results[key].reason,
                        }
                        for key in keys
                    ],
                }
                for heading, question, keys in _PILLARS
            ],
        }

    def get_my_progress(self) -> dict[str, Any]:
        """The signed-in student's learning over all their submitted sessions.

        Sessions that were never scored (e.g. submitted before the student
        opened the CIQ screen) are scored now and saved, so the first call
        can be slow and later calls are quick.
        """
        return self._progress_for(self._require_student_id())

    def _progress_for(self, student_id: int) -> dict[str, Any]:
        self._event_logger.flush()
        with self._session_factory() as db_session:
            session_ids = [
                row.id
                for row in db_session.query(SessionModel)
                .filter(
                    SessionModel.student_id == student_id,
                    SessionModel.submitted_at.is_not(None),
                )
                .all()
            ]
            scored_ids = {
                sid
                for (sid,) in db_session.query(SignalScore.session_id)
                .filter(SignalScore.session_id.in_(session_ids))
                .distinct()
                .all()
            }
        for session_id in session_ids:
            if session_id not in scored_ids:
                results = compute_all_signals(self._session_factory, session_id, self._ai_provider)
                persist_signal_scores(self._session_factory, session_id, results)

        rows: list[SessionRow] = []
        with self._session_factory() as db_session:
            for session in db_session.query(SessionModel).filter(SessionModel.id.in_(session_ids)):
                latest: dict[str, float | None] = {}
                for score in (
                    db_session.query(SignalScore)
                    .filter_by(session_id=session.id)
                    .order_by(SignalScore.id)
                ):
                    latest[score.signal_key] = score.value  # later rows win
                task = session.task
                if task.assessment_kind is not None:
                    kind = "followup"
                elif _is_lab(task):
                    kind = "lab"
                else:
                    kind = "practice"
                submitted = session.submitted_at
                if submitted is None:  # (the query above only returns submitted sessions)
                    continue
                # SQLite hands back naive datetimes; compare like with like.
                elapsed = submitted.replace(tzinfo=None) - session.started_at.replace(tzinfo=None)
                rows.append(
                    {
                        "session_id": session.id,
                        "title": task.title,
                        "kind": kind,
                        "stage": session.stage.stage_type.value if session.stage else None,
                        "submitted_at": submitted.isoformat(),
                        "minutes": max(0, round(elapsed.total_seconds() / 60)),
                        "ai_interactions": len(session.ai_interactions),
                        "signals": latest,
                    }
                )
        return build_progress(rows)

    # -- professor: looking at students' work and progress ---------------------

    def _may_view_student(
        self, db_session: OrmSession, professor_id: int, student: Student, task: Task | None = None
    ) -> bool:
        """A professor sees their own class, and (on a server) students doing their labs."""
        if not self._restrict_signup or resource_store.is_member(
            db_session, student.id, professor_id
        ):
            return True
        if task is None:
            return False
        if task.professor_id == professor_id:
            return True
        return task.course_id is not None and task.course_id in academics.professor_course_ids(
            self._session_factory, professor_id
        )

    def get_class_overview(self) -> list[dict[str, Any]]:
        """The class with each student's place and overall CIQ (for the My Class tree)."""
        professor_id = self._require_professor_id()
        rows = resource_store.class_students(self._session_factory, professor_id)
        for row in rows:
            totals = self._progress_for(row["id"])["totals"]
            row["overall_ciq"] = totals["average_score"]
            row["sessions_submitted"] = totals["sessions"]
        return rows

    def get_student_progress(self, student_id: int) -> dict[str, Any]:
        """What the student sees as My Progress, for a professor."""
        professor_id = self._require_professor_id()
        with self._session_factory() as db_session:
            student = db_session.get(Student, int(student_id))
            if student is None or not self._may_view_student(db_session, professor_id, student):
                raise ValueError("That student isn't in your class.")
            name = student.display_name
            place = academics.describe(student)
        return {"student": {"id": int(student_id), "name": name, **place}} | self._progress_for(
            int(student_id)
        )

    def _stage_submission(self, db_session: OrmSession, session: SessionModel) -> dict[str, Any]:
        """One stage's final code and last run output, as the professor sees it."""
        last_snapshot = (
            db_session.query(CodeSnapshot)
            .filter_by(session_id=session.id)
            .order_by(CodeSnapshot.id.desc())
            .first()
        )
        files: dict[str, str] = {}
        if last_snapshot is not None:
            files = _unbundle_files(last_snapshot.content) or {
                _ENTRY_FILENAME: last_snapshot.content
            }
        last_run = (
            db_session.query(ExecutionResult)
            .filter_by(session_id=session.id)
            .order_by(ExecutionResult.id.desc())
            .first()
        )
        explanation = (
            db_session.query(ConceptCheckResponse).filter_by(session_id=session.id).first()
        )
        stage = session.stage
        return {
            "session_id": session.id,
            "stage_type": stage.stage_type.value if stage else None,
            "submitted": session.submitted_at is not None,
            "submitted_at": session.submitted_at.isoformat() if session.submitted_at else None,
            "files": files,
            "output": (
                {
                    "stdout": last_run.stdout,
                    "stderr": last_run.stderr,
                    "exit_status": last_run.exit_status,
                    "timed_out": last_run.timed_out,
                }
                if last_run
                else None
            ),
            "ai_chats": len(session.ai_interactions),
            "pastes": self._paste_summary(db_session, session.id),
            "focus_losses": session.focus_losses or 0,
            "note": _SUBMIT_NOTES.get(session.submit_reason or ""),
            "explanation": (
                {"question": explanation.question, "response": explanation.response_text}
                if explanation
                else None
            ),
        }

    @staticmethod
    def _paste_summary(db_session: OrmSession, session_id: int) -> dict[str, int]:
        summary = {"ai": 0, "other": 0, "ai_chars": 0, "other_chars": 0}
        for event in db_session.query(Event).filter_by(
            session_id=session_id, event_type=EventType.PASTE
        ):
            payload = event.payload_json or {}
            source = "ai" if payload.get("source") == "ai" else "other"
            summary[source] += 1
            summary[f"{source}_chars"] += int(payload.get("chars", 0))
        return summary

    def get_student_submission(self, task_id: int, student_id: int) -> dict[str, Any]:
        """A student's work on a lab (or follow-up): each stage's final code and output."""
        professor_id = self._require_professor_id()
        self._event_logger.flush()
        with self._session_factory() as db_session:
            task = db_session.get(Task, int(task_id))
            student = db_session.get(Student, int(student_id))
            if task is None or student is None:
                raise ValueError("No such lab or student.")
            if not self._may_view_student(db_session, professor_id, student, task):
                raise ValueError("That student isn't in your class.")
            stages = []
            for stage in task.stages:
                attempts = (
                    db_session.query(SessionModel)
                    .filter_by(student_id=student.id, stage_id=stage.id)
                    .order_by(SessionModel.id)
                    .all()
                )
                if not attempts:
                    continue
                submitted = [a for a in attempts if a.submitted_at is not None]
                stages.append(self._stage_submission(db_session, (submitted or attempts)[-1]))
            final: int | None = stages[-1]["session_id"] if stages else None
            done = bool(stages) and stages[-1]["submitted"]
            return {
                "task_title": task.title,
                "student": _student_card(student),
                "stages": stages,
                "score": self._overall_score(final) if final is not None and done else None,
            }

    def get_session_submission(self, session_id: int) -> dict[str, Any]:
        """One submitted session's final code and output (from a student's progress history)."""
        professor_id = self._require_professor_id()
        self._event_logger.flush()
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, int(session_id))
            if session is None:
                raise ValueError("No such session.")
            student = db_session.get(Student, session.student_id)
            if student is None or not self._may_view_student(
                db_session, professor_id, student, session.task
            ):
                raise ValueError("That student isn't in your class.")
            return {
                "task_title": session.task.title,
                "student": _student_card(student),
                "stages": [self._stage_submission(db_session, session)],
                "score": self._overall_score(session.id) if session.submitted_at else None,
            }

    # -- professor: AI help with writing ----------------------------------------

    def draft_problem_description(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Have the AI draft (or tidy up) a problem statement from a title, topic and notes."""
        self._require_professor_id()
        title = str(spec.get("title") or "").strip()
        notes = str(spec.get("notes") or "").strip()
        if not title and not notes:
            return {"ok": False, "error": "Enter a title (or a few notes) first."}
        lines = [
            "Write a clear programming problem statement for a university lab, in plain text "
            "(no markdown, no headings, no code fences), at most 150 words.",
            "State what to build, what the input and output are, one short example, and any "
            "constraints. Do not include the solution.",
            "Use Python as the language.",
        ]
        for label, key in (("Title", "title"), ("Topic", "topic"), ("Difficulty", "difficulty")):
            if spec.get(key):
                lines.append(f"{label}: {spec[key]}")
        if notes:
            lines.append(f"The professor's notes or current draft (improve this):\n{notes}")
        result = self._ai_provider.generate("\n".join(lines), GenerationContext(), Purpose.CHAT)
        if not result.available or not result.text.strip():
            problem = self._ai_provider.diagnose() or result.error
            return {
                "ok": False,
                "error": problem
                or "The AI assistant didn't answer. Ask your administrator to set it up.",
            }
        return {"ok": True, "text": result.text.strip()}

    # -- persistence helpers ----------------------------------------------

    def _save_snapshot(
        self, session_id: int, files: dict[str, str], *, active_filename: str | None = None
    ) -> int:
        with self._session_factory() as db_session:
            snapshot = CodeSnapshot(
                session_id=session_id,
                content=_bundle_files(files),
                active_filename=active_filename,
            )
            db_session.add(snapshot)
            db_session.commit()
            return snapshot.id

    def _log_event(
        self,
        session_id: int,
        event_type: EventType,
        *,
        code_version_id: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        self._event_logger.log(
            PendingEvent(
                session_id=session_id,
                event_type=event_type,
                payload_json=payload,
                code_version_id=code_version_id,
            )
        )
