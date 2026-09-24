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

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, GenerationContext, Purpose, UnavailableProvider
from eaal_platform.db.bootstrap import (
    authenticate_professor,
    authenticate_student,
    create_professor_account,
    create_student_account,
    start_practice_session,
    start_stage_session,
)
from eaal_platform.db.bootstrap import (
    create_followup_assessment as create_followup_assessment_row,
)
from eaal_platform.db.bootstrap import (
    create_lab as create_lab_row,
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
    Stage,
    StageType,
    Student,
    Task,
)
from eaal_platform.db.models import Session as SessionModel
from eaal_platform.db.progress import is_stage_unlocked
from eaal_platform.events.logger import EventLogger, PendingEvent
from eaal_platform.sandbox.executor import run_code as sandbox_run_code
from eaal_platform.signals.compute import compute_all_signals, persist_signal_scores

_ENTRY_FILENAME = "main.py"

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
    ) -> None:
        self._session_factory = session_factory
        self._event_logger = event_logger
        self._ai_provider = ai_provider or UnavailableProvider()
        self._current_student_id: int | None = None
        self._current_student_name: str | None = None
        self._current_professor_id: int | None = None
        self._current_professor_name: str | None = None

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
            self._current_student_id, self._current_student_name = result
            self._current_professor_id, self._current_professor_name = None, None
            return {"ok": True, "role": "student", "name": self._current_student_name}
        if role == "professor":
            result = authenticate_professor(self._session_factory, email=email, password=password)
            if result is None:
                return {"ok": False, "error": "Incorrect email or password."}
            self._current_professor_id, self._current_professor_name = result
            self._current_student_id, self._current_student_name = None, None
            return {"ok": True, "role": "professor", "name": self._current_professor_name}
        raise ValueError(f"Unknown role {role!r}")

    def create_account(
        self,
        role: str,
        display_name: str,
        email: str,
        password: str,
        enrollment_no: str | None = None,
    ) -> dict[str, Any]:
        try:
            if role == "student":
                create_student_account(
                    self._session_factory,
                    display_name=display_name,
                    email=email,
                    password=password,
                    enrollment_no=enrollment_no,
                )
            elif role == "professor":
                create_professor_account(
                    self._session_factory, display_name=display_name, email=email, password=password
                )
            else:
                raise ValueError(f"Unknown role {role!r}")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

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

    def _require_logged_in(self) -> None:
        if self._current_student_id is None and self._current_professor_id is None:
            raise ValueError("Not logged in")

    # -- bootstrap / navigation ------------------------------------------

    def get_student_name(self) -> str:
        return self._current_student_name or "Student"

    def get_labs(self) -> list[dict[str, Any]]:
        with self._session_factory() as db_session:
            tasks = [task for task in db_session.query(Task).all() if task.stages]
            return [
                {
                    "id": task.id,
                    "title": task.title,
                    "learning_objective": task.learning_objective,
                    "professor_name": task.professor_name,
                    "stage_count": len(task.stages),
                }
                for task in tasks
            ]

    def get_stages(self, task_id: int) -> dict[str, Any]:
        student_id = self._require_student_id()
        with self._session_factory() as db_session:
            task = db_session.get(Task, task_id)
            if task is None:
                raise ValueError(f"No task with id {task_id}")
            stages = list(task.stages)
            return {
                "task_title": task.title,
                "stages": [
                    {
                        "id": stage.id,
                        "stage_type": stage.stage_type.value,
                        "ai_assistance_mode": stage.ai_assistance_mode.value,
                        "duration_minutes": stage.duration_minutes,
                        "unlocked": is_stage_unlocked(self._session_factory, student_id, stage),
                    }
                    for stage in stages
                ],
            }

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
        self._require_professor_id()
        stage_inputs = lab.get("stages") or []
        stage_types = (StageType.LEARNING, StageType.EXPLORATION, StageType.ASSESSMENT)
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
            course=lab.get("course"),
            division=lab.get("division"),
            batch=lab.get("batch"),
            stage_plan=stage_plan,
        )
        return {"task_id": task_id}

    def get_professor_labs(self) -> list[dict[str, Any]]:
        self._require_professor_id()
        with self._session_factory() as db_session:
            tasks = [task for task in db_session.query(Task).all() if task.stages]
            return [
                {
                    "id": task.id,
                    "title": task.title,
                    "course": task.course,
                    "division": task.division,
                    "batch": task.batch,
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
            tasks = [task for task in db_session.query(Task).all() if task.stages]

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
            "student_name": student.display_name if student else "Unknown",
            "enrollment_no": student.enrollment_no if student else None,
            "score": self._overall_score(session.id) if submitted else None,
            "submitted_at": session.submitted_at.isoformat() if session.submitted_at else None,
            "status": "Submitted" if submitted else "Not Submitted",
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
        session_id = start_stage_session(
            self._session_factory, self._require_student_id(), stage_id
        )
        with self._session_factory() as db_session:
            stage = db_session.get(Stage, stage_id)
            if stage is None:
                raise ValueError(f"No stage with id {stage_id}")
            task = stage.task
            self._log_event(session_id, EventType.TASK_START)
            return {
                "session_id": session_id,
                "stage_id": stage.id,
                "task_title": f"{task.title} — {stage.stage_type.value.title()}",
                "task_description": task.description,
                "starter_files": {_ENTRY_FILENAME: task.starter_code or ""},
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

    def run_code(
        self, session_id: int, files: dict[str, str], entry_filename: str = _ENTRY_FILENAME
    ) -> dict[str, Any]:
        if entry_filename not in files:
            entry_filename = _ENTRY_FILENAME
        snapshot_id = self._save_snapshot(session_id, files, active_filename=entry_filename)
        self._log_event(session_id, EventType.CODE_RUN, code_version_id=snapshot_id)

        outcome = sandbox_run_code(files, entry_filename=entry_filename)

        with self._session_factory() as db_session:
            db_session.add(
                ExecutionResult(
                    session_id=session_id,
                    code_version_id=snapshot_id,
                    stdout=outcome.stdout,
                    stderr=outcome.stderr,
                    exit_status=outcome.exit_status,
                    duration_ms=outcome.duration_ms,
                    timed_out=outcome.timed_out,
                )
            )
            db_session.commit()

        self._log_event(
            session_id,
            EventType.EXECUTION_RESULT,
            code_version_id=snapshot_id,
            payload={
                "exit_status": outcome.exit_status,
                "duration_ms": outcome.duration_ms,
                "timed_out": outcome.timed_out,
                # Whether the run actually produced output — S3.2 (Knowledge
                # Application) uses this so a no-op program (e.g. bare
                # `pass`) that merely exits cleanly can't score the same as
                # one that did something.
                "has_output": bool(outcome.stdout.strip()),
            },
        )
        return {
            "entry_filename": entry_filename,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "exit_status": outcome.exit_status,
            "duration_ms": outcome.duration_ms,
            "timed_out": outcome.timed_out,
        }

    def submit_session(self, session_id: int, files: dict[str, str]) -> dict[str, Any]:
        snapshot_id = self._save_snapshot(session_id, files)
        with self._session_factory() as db_session:
            session = db_session.get(SessionModel, session_id)
            if session is not None:
                session.submitted_at = datetime.now(UTC)
                db_session.commit()
        self._log_event(session_id, EventType.SUBMISSION, code_version_id=snapshot_id)
        return {"ok": True}

    def get_concept_check_question(self) -> str:
        return _CONCEPT_CHECK_QUESTION

    def submit_concept_check(self, session_id: int, response_text: str) -> dict[str, Any]:
        """Store the student's explanation of their solution — S3.1's evidence.

        One row per session: if this session already has a response (e.g.
        the student edited their explanation before the final Submit),
        update it in place rather than accumulating duplicates.
        """
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
        return self._ai_provider.ping()

    def send_ai_message(
        self, session_id: int, message: str, files: dict[str, str]
    ) -> dict[str, Any]:
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
            return {"available": False, "error": "AI assistance is restricted during this stage."}

        snapshot_id = self._save_snapshot(session_id, files)
        had_recent_error = self._last_execution_had_error(session_id)
        self._log_event(
            session_id,
            EventType.AI_PROMPT,
            code_version_id=snapshot_id,
            payload={"had_recent_error": had_recent_error},
        )

        context = self._build_ai_context(session_id, task_description, files)
        result = self._ai_provider.generate(message, context, Purpose.CHAT)

        error = None if result.available else result.error
        self._log_event(
            session_id,
            EventType.AI_RESPONSE,
            code_version_id=snapshot_id,
            payload={"available": result.available, "error": error},
        )

        response_or_error = result.text if result.available else (result.error or "")
        with self._session_factory() as db_session:
            db_session.add(
                AIInteraction(
                    session_id=session_id,
                    prompt=message,
                    response=response_or_error if result.available else None,
                    provider=self._ai_provider.provider_name,
                    model=self._ai_provider.model_name,
                    code_version_before_id=snapshot_id,
                    code_version_after_id=snapshot_id,
                )
            )
            db_session.commit()

        return {"available": result.available, "text": response_or_error}

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
            return {"submitted_at": submitted_at, "exit_status": exit_status}

    def get_ciq_score(self, session_id: int) -> dict[str, Any]:
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
