"""SQLAlchemy 2.0 declarative models for the CAVY local data store.

Design principle carried over from the EAAL framework docs: ``events`` is the
single source of truth (Layer 1). It is append-only — rows are written once
and never updated or deleted, even when a signal's scoring logic changes
later. Every other derived table (signal_scores, in a later phase) can be
recomputed from ``events`` without losing history. Treat any code path that
UPDATEs or DELETEs an ``Event`` row as a bug, not a feature.

Every table carries a nullable ``synced_at`` column from day one so a future
central-sync layer can select ``WHERE synced_at IS NULL`` and push rows
upstream without an schema migration to *add* sync support later.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all CAVY tables."""


class EventType(enum.StrEnum):
    """Vocabulary for ``Event.event_type``.

    Kept as an enum (rather than a free-text column) so the analytics engine
    (a later phase) can exhaustively pattern-match on event kind without
    guessing at string spellings scattered across the codebase.
    """

    TASK_START = "TASK_START"
    CODE_EDIT = "CODE_EDIT"
    CODE_RUN = "CODE_RUN"
    EXECUTION_RESULT = "EXECUTION_RESULT"
    TEST_RUN = "TEST_RUN"
    AI_PROMPT = "AI_PROMPT"
    AI_RESPONSE = "AI_RESPONSE"
    AI_CODE_ADOPT = "AI_CODE_ADOPT"
    AI_CODE_MODIFY = "AI_CODE_MODIFY"
    SUBMISSION = "SUBMISSION"


class StageType(enum.StrEnum):
    """The three stages a Lab moves a student through.

    Matches the wireframed Session screen (Learning -> Exploration ->
    Assessment) rather than being an open-ended list, because the point is
    a fixed, comparable structure: every Lab has the same three stages,
    which is what makes it possible to later compare, say, Assessment-stage
    behavior across different labs.
    """

    LEARNING = "LEARNING"
    EXPLORATION = "EXPLORATION"
    ASSESSMENT = "ASSESSMENT"


class AssessmentKind(enum.StrEnum):
    """What a follow-on Task (beyond a normal Lab) is evidence for.

    ``None`` (the default, no row needed) means an ordinary Lab or Practice
    task. ``TRANSFER`` and ``RETENTION`` mark a Task a professor spun off
    from an existing Lab (see ``Task.linked_task_id``) specifically to
    collect evidence for S3.3 (Knowledge Transfer) and S3.4 (Retention &
    Independent Recall) — signals the framework says require a *different*
    problem and a *delayed* attempt respectively, not just more activity on
    the original task (see ``docs/The EAAL Framework.pdf`` §6.6).
    """

    TRANSFER = "TRANSFER"
    RETENTION = "RETENTION"


class AIAssistanceMode(enum.StrEnum):
    """How much the AI is allowed to help during a given stage.

    This is what turns "how much AI help is appropriate" from something we
    can only guess at after the fact into a condition the professor sets
    up front — e.g. FULL during Learning, RESTRICTED during Assessment.
    That's valuable evidence in itself when later validating signals like
    Help-Seeking Calibration: the same request means something different
    under FULL than under RESTRICTED.
    """

    FULL = "FULL"
    RESTRICTED = "RESTRICTED"
    NONE = "NONE"


class Student(Base):
    """A local student account.

    ``email``/``password_hash`` back the Login screen's Student tab — see
    ``auth.py`` for hashing and ``db/bootstrap.py`` for account creation
    and authentication. Nullable because rows seeded before accounts
    existed (or created some other way) shouldn't become invalid.
    """

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enrollment_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list[Session]] = relationship(back_populates="student")


class Professor(Base):
    """A local professor/teacher account — the Login screen's Teacher tab.

    Deliberately a separate table from ``Student`` rather than a shared
    ``users`` table with a role flag: the two roles have almost no
    overlapping fields or behavior (a professor has no CIQ signals, a
    student doesn't author labs), and keeping them separate means a bug in
    one query can't accidentally leak or corrupt the other's rows.
    """

    __tablename__ = "professors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Task(Base):
    """A coding task/lab a session attempts.

    A Task with no ``stages`` is a standalone Practice task (no grading, no
    progression) — the Labs screen tells the two apart by whether
    ``stages`` is empty, rather than a separate boolean flag, so a Task
    can't end up in an inconsistent "is it a lab or practice" state.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    difficulty: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    learning_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    starter_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    professor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Cohort tags a professor sets when creating a lab, e.g. "B.Sc. Data
    # Science" / "A" / "2026" — free-text labels for the dashboard's filters,
    # not enrollment records. This device only ever holds one local
    # student's data (see Student), so a lab's real class roster and the
    # per-student rows on its report depend on the future multi-user/sync
    # phase; these fields exist now so that data model doesn't need a
    # migration once it does.
    course: Mapped[str | None] = mapped_column(String(200), nullable=True)
    division: Mapped[str | None] = mapped_column(String(50), nullable=True)
    batch: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Set only on a follow-on Task a professor spun off from an existing Lab
    # to collect Knowledge Transfer / Retention evidence — see
    # ``AssessmentKind``. ``None`` for every ordinary Lab or Practice task.
    linked_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    assessment_kind: Mapped[AssessmentKind | None] = mapped_column(
        Enum(AssessmentKind), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list[Session]] = relationship(back_populates="task")
    stages: Mapped[list[Stage]] = relationship(back_populates="task", order_by="Stage.order_index")
    linked_task: Mapped[Task | None] = relationship(remote_side=[id])


class Stage(Base):
    """One of a Lab's (Task's) three stages: Learning, Exploration, Assessment."""

    __tablename__ = "stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    stage_type: Mapped[StageType] = mapped_column(Enum(StageType), nullable=False)
    ai_assistance_mode: Mapped[AIAssistanceMode] = mapped_column(
        Enum(AIAssistanceMode), nullable=False, default=AIAssistanceMode.FULL
    )
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[Task] = relationship(back_populates="stages")
    sessions: Mapped[list[Session]] = relationship(back_populates="stage")


class Session(Base):
    """One coding session: a single attempt at a task (or one of its stages) by a student.

    ``stage_id`` is nullable because a Practice session isn't part of any
    stage progression. ``submitted_at`` is separate from ``ended_at``:
    ``ended_at`` would mean "the student stopped working," which can
    happen without ever submitting (closing the app mid-attempt);
    ``submitted_at`` specifically marks the Submit action that the Stages
    screen's unlock logic and the Lab Report depend on.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    stage_id: Mapped[int | None] = mapped_column(ForeignKey("stages.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    student: Mapped[Student] = relationship(back_populates="sessions")
    task: Mapped[Task] = relationship(back_populates="sessions")
    stage: Mapped[Stage | None] = relationship(back_populates="sessions")
    events: Mapped[list[Event]] = relationship(back_populates="session")
    code_snapshots: Mapped[list[CodeSnapshot]] = relationship(back_populates="session")
    ai_interactions: Mapped[list[AIInteraction]] = relationship(back_populates="session")
    execution_results: Mapped[list[ExecutionResult]] = relationship(back_populates="session")
    signal_scores: Mapped[list[SignalScore]] = relationship(back_populates="session")
    concept_check_response: Mapped[ConceptCheckResponse | None] = relationship(
        back_populates="session"
    )


class Event(Base):
    """Append-only raw event log — the single source of truth.

    Never updated or deleted after insert (see module docstring). Later
    processing stages (episode reconstruction, signal extraction) always
    read from here rather than from any derived table, so a bug in a
    derived table can always be fixed by recomputing from this table.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    code_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("code_snapshots.id"), nullable=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[Session] = relationship(back_populates="events")
    code_version: Mapped[CodeSnapshot | None] = relationship()


class CodeSnapshot(Base):
    """A versioned state of the student's code.

    ``content`` holds *all* of the session's files bundled into one text
    blob (see ``api/bridge.py``'s ``_bundle_files``) rather than one file's
    text, because a workspace can hold several files side by side and a
    signal like Modification & Verification needs to see the whole program
    the student was looking at, not just whichever file last changed.
    ``active_filename`` separately records which single file was being
    edited when this snapshot was taken (``None`` for snapshots — like a
    run or a submission — that represent the whole program rather than one
    edit).
    """

    __tablename__ = "code_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    active_filename: Mapped[str | None] = mapped_column(String(260), nullable=True)
    ast_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[Session] = relationship(back_populates="code_snapshots")


class AIInteraction(Base):
    """One prompt/response pair with an AI provider."""

    __tablename__ = "ai_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    code_version_before_id: Mapped[int | None] = mapped_column(
        ForeignKey("code_snapshots.id"), nullable=True
    )
    code_version_after_id: Mapped[int | None] = mapped_column(
        ForeignKey("code_snapshots.id"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[Session] = relationship(back_populates="ai_interactions")
    code_version_before: Mapped[CodeSnapshot | None] = relationship(
        foreign_keys=[code_version_before_id]
    )
    code_version_after: Mapped[CodeSnapshot | None] = relationship(
        foreign_keys=[code_version_after_id]
    )


class ExecutionResult(Base):
    """One sandboxed run outcome."""

    __tablename__ = "execution_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    code_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("code_snapshots.id"), nullable=True
    )
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    exit_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timed_out: Mapped[bool] = mapped_column(nullable=False, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[Session] = relationship(back_populates="execution_results")
    code_version: Mapped[CodeSnapshot | None] = relationship()


class ConceptCheckResponse(Base):
    """The student's written explanation of the concept behind their solution.

    One per session, captured at Assessment-stage submit time — the raw
    evidence S3.1 (Conceptual Understanding) scores via an LLM rubric,
    exactly as the framework proposes ("assessed through structured
    explanations or concept-focused questions, potentially evaluated using
    predefined rubrics", §6.6). ``question`` is stored alongside the
    response (rather than assumed fixed) so a future version could vary it
    per task without losing the ability to interpret past answers.
    """

    __tablename__ = "concept_check_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False, unique=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[Session] = relationship(back_populates="concept_check_response")


class SignalScore(Base):
    """A computed EAAL signal value (Layer 3), with its supporting evidence (Layer 2).

    Not populated by this phase's code — the analytics engine that writes
    here is a later phase. The table exists now so the schema is stable
    from day one and doesn't need a migration once scoring lands.
    """

    __tablename__ = "signal_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    signal_key: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "S1.1"
    value: Mapped[float | None] = mapped_column(nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[Session] = relationship(back_populates="signal_scores")
