"""Local-account, demo-content, and session-creation helpers.

Accounts are local-only (see ``auth.py``): there's no server to
authenticate against, but a shared computer can still have more than one
student or professor using it, so both roles get real email/password
accounts (``create_student_account`` / ``create_professor_account``,
checked by ``authenticate_student`` / ``authenticate_professor``) rather
than a single hardcoded local user.

``seed_demo_content`` seeds a couple of demo Labs with real stages, plus a
standalone Practice task, so the Labs screen has something to show before
any professor has authored a real one via ``create_lab``.

Actual ``Session`` rows are created at the moment a student clicks Start,
via ``start_stage_session`` / ``start_practice_session`` — never eagerly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.auth import (
    generate_temporary_password,
    hash_password,
    password_problem,
    verify_password,
)
from eaal_platform.db.models import (
    AIAssistanceMode,
    AssessmentKind,
    Professor,
    Session,
    Stage,
    StageType,
    Student,
    Task,
)

PRACTICE_TASK_TITLE = "Practice"

# (stage_type, ai_assistance_mode, duration_minutes) for each of a Lab's
# three stages, in order. Assessment is RESTRICTED by default — matching
# the wireframe — because it's the one stage where "how much did the AI
# just hand the student" is the whole point of the measurement.
_DEFAULT_STAGE_PLAN: tuple[tuple[StageType, AIAssistanceMode, int], ...] = (
    (StageType.LEARNING, AIAssistanceMode.FULL, 20),
    (StageType.EXPLORATION, AIAssistanceMode.FULL, 20),
    (StageType.ASSESSMENT, AIAssistanceMode.RESTRICTED, 20),
)

_DEMO_LABS: tuple[tuple[str, str, str], ...] = (
    (
        "Reversing a Linked List",
        "Data Structures",
        "Implement a function that reverses a singly linked list in place.",
    ),
    (
        "Calculating Entropy",
        "Machine Learning Foundations",
        "Implement Shannon entropy for a set of class probabilities.",
    ),
)


def create_student_account(
    session_factory: sessionmaker[OrmSession],
    *,
    display_name: str,
    email: str,
    password: str,
    enrollment_no: str | None = None,
) -> int:
    """Create a new local Student account and return its id.

    Raises ``ValueError`` if the email is already registered (as either a
    student or a professor — one email shouldn't silently work for both
    tabs on the Login screen).
    """
    with session_factory() as db_session:
        _ensure_email_is_free(db_session, email)
        student = Student(
            display_name=display_name,
            email=email,
            password_hash=hash_password(password),
            enrollment_no=enrollment_no,
        )
        db_session.add(student)
        db_session.commit()
        return student.id


def create_professor_account(
    session_factory: sessionmaker[OrmSession], *, display_name: str, email: str, password: str
) -> int:
    """Create a new local Professor account and return its id."""
    with session_factory() as db_session:
        _ensure_email_is_free(db_session, email)
        professor = Professor(
            display_name=display_name, email=email, password_hash=hash_password(password)
        )
        db_session.add(professor)
        db_session.commit()
        return professor.id


def _ensure_email_is_free(db_session: OrmSession, email: str) -> None:
    if db_session.query(Student).filter_by(email=email).first() is not None:
        raise ValueError(f"{email} is already registered as a student")
    if db_session.query(Professor).filter_by(email=email).first() is not None:
        raise ValueError(f"{email} is already registered as a professor")


def authenticate_student(
    session_factory: sessionmaker[OrmSession], *, email: str, password: str
) -> tuple[int, str] | None:
    """Return ``(id, display_name)`` if the credentials match a Student account, else ``None``."""
    with session_factory() as db_session:
        student = db_session.query(Student).filter_by(email=email).first()
        if student is None or student.password_hash is None:
            return None
        if not verify_password(password, student.password_hash):
            return None
        return student.id, student.display_name


def authenticate_professor(
    session_factory: sessionmaker[OrmSession], *, email: str, password: str
) -> tuple[int, str] | None:
    """Return ``(id, display_name)`` if the credentials match a Professor account, else ``None``."""
    with session_factory() as db_session:
        professor = db_session.query(Professor).filter_by(email=email).first()
        if professor is None or professor.password_hash is None:
            return None
        if not verify_password(password, professor.password_hash):
            return None
        return professor.id, professor.display_name


def seed_demo_content(session_factory: sessionmaker[OrmSession]) -> None:
    """Create the demo Labs (with stages) and the Practice task, if missing.

    Safe to call on every app launch: each Lab/Task is looked up by title
    first, so re-running this never creates duplicates.
    """
    with session_factory() as db_session:
        if db_session.query(Task).filter_by(title=PRACTICE_TASK_TITLE).first() is None:
            db_session.add(Task(title=PRACTICE_TASK_TITLE))

        for title, topic, description in _DEMO_LABS:
            existing = db_session.query(Task).filter_by(title=title).first()
            if existing is not None:
                continue
            task = Task(
                title=title,
                description=description,
                learning_objective=topic,
                professor_name="Dr. Kariya",
            )
            db_session.add(task)
            db_session.flush()
            for index, (stage_type, mode, duration) in enumerate(_DEFAULT_STAGE_PLAN):
                db_session.add(
                    Stage(
                        task_id=task.id,
                        stage_type=stage_type,
                        ai_assistance_mode=mode,
                        duration_minutes=duration,
                        order_index=index,
                    )
                )
        db_session.commit()


def create_lab(
    session_factory: sessionmaker[OrmSession],
    *,
    title: str,
    description: str | None,
    learning_objective: str | None,
    difficulty: str | None,
    professor_name: str | None,
    course: str | None,
    division: str | None,
    batch: str | None,
    stage_plan: tuple[tuple[StageType, AIAssistanceMode, int | None], ...],
) -> int:
    """Create a professor-authored Lab (a Task with three Stages) and return its id.

    Takes a full ``stage_plan`` rather than assuming ``_DEFAULT_STAGE_PLAN``
    — that default is only for the seeded demo content; a professor's
    Create Session form sets each stage's duration and AI-assistance mode
    explicitly.
    """
    with session_factory() as db_session:
        task = Task(
            title=title,
            description=description,
            learning_objective=learning_objective,
            difficulty=difficulty,
            professor_name=professor_name,
            course=course,
            division=division,
            batch=batch,
        )
        db_session.add(task)
        db_session.flush()
        for index, (stage_type, mode, duration) in enumerate(stage_plan):
            db_session.add(
                Stage(
                    task_id=task.id,
                    stage_type=stage_type,
                    ai_assistance_mode=mode,
                    duration_minutes=duration,
                    order_index=index,
                )
            )
        db_session.commit()
        return task.id


def create_followup_assessment(
    session_factory: sessionmaker[OrmSession],
    *,
    source_task_id: int,
    kind: AssessmentKind,
    title: str,
    description: str | None,
    ai_assistance_mode: AIAssistanceMode,
    duration_minutes: int | None,
) -> int:
    """Spin off a Transfer Task or Retention Check from an existing Lab.

    Reuses the ordinary Task/Stage machinery — it's a single-stage Task
    linked back to ``source_task_id`` via ``linked_task_id``, so a student
    starts and submits it exactly like any other stage (``get_stages``,
    ``start_stage``, ``submit_session`` don't need to know it's special;
    only ``signals/compute.py`` looks at ``assessment_kind`` when deciding
    whether a session counts as Transfer/Retention evidence).
    """
    with session_factory() as db_session:
        source = db_session.get(Task, source_task_id)
        if source is None:
            raise ValueError(f"No task with id {source_task_id}")
        task = Task(
            title=title,
            description=description,
            learning_objective=source.learning_objective,
            professor_name=source.professor_name,
            course=source.course,
            division=source.division,
            batch=source.batch,
            linked_task_id=source_task_id,
            assessment_kind=kind,
        )
        db_session.add(task)
        db_session.flush()
        db_session.add(
            Stage(
                task_id=task.id,
                stage_type=StageType.ASSESSMENT,
                ai_assistance_mode=ai_assistance_mode,
                duration_minutes=duration_minutes,
                order_index=0,
            )
        )
        db_session.commit()
        return task.id


def start_practice_session(session_factory: sessionmaker[OrmSession], student_id: int) -> int:
    """Start a fresh Practice attempt (no stage, no grading) and return its session id."""
    with session_factory() as db_session:
        task = db_session.query(Task).filter_by(title=PRACTICE_TASK_TITLE).one()
        session = Session(student_id=student_id, task_id=task.id)
        db_session.add(session)
        db_session.commit()
        return session.id


def start_stage_session(
    session_factory: sessionmaker[OrmSession], student_id: int, stage_id: int
) -> int:
    """Start a fresh attempt at a specific Lab stage and return its session id."""
    with session_factory() as db_session:
        stage = db_session.get(Stage, stage_id)
        if stage is None:
            raise ValueError(f"No stage with id {stage_id}")
        session = Session(student_id=student_id, task_id=stage.task_id, stage_id=stage.id)
        db_session.add(session)
        db_session.commit()
        return session.id


def update_lab(
    session_factory: sessionmaker[OrmSession],
    task_id: int,
    *,
    title: str,
    description: str | None,
    learning_objective: str | None,
    difficulty: str | None,
    course: str | None,
    division: str | None,
    batch: str | None,
    stage_plan: tuple[tuple[AIAssistanceMode, int | None], ...],
) -> None:
    """Edit a Lab's details and its stages' AI mode / duration, in place.

    ``stage_plan`` is one ``(ai_assistance_mode, duration_minutes)`` per
    existing stage, in order. A stage's AI mode is locked once any student
    has started it: signals are interpreted against the mode a session ran
    under, so changing it afterwards would silently change what that
    recorded evidence means. Durations and descriptive fields stay editable.
    Cohort tags are copied onto the Lab's follow-up assessments so a Transfer
    Task or Retention Check never drifts out of sync with its parent.
    """
    with session_factory() as db_session:
        task = db_session.get(Task, task_id)
        if task is None:
            raise ValueError(f"No task with id {task_id}")
        if task.assessment_kind is not None:
            raise ValueError("Follow-up assessments can't be edited here; edit their Lab instead.")
        if len(stage_plan) != len(task.stages):
            raise ValueError(f"Expected {len(task.stages)} stage configs, got {len(stage_plan)}")

        for stage, (mode, duration) in zip(task.stages, stage_plan, strict=True):
            if mode != stage.ai_assistance_mode:
                started = db_session.query(Session).filter_by(stage_id=stage.id).first()
                if started is not None:
                    raise ValueError(
                        f"The {stage.stage_type.value.title()} stage's AI mode can't be "
                        "changed because students have already started it."
                    )
            stage.ai_assistance_mode = mode
            stage.duration_minutes = duration

        task.title = title
        task.description = description
        task.learning_objective = learning_objective
        task.difficulty = difficulty
        task.course = course
        task.division = division
        task.batch = batch
        for followup in db_session.query(Task).filter_by(linked_task_id=task_id):
            followup.course = course
            followup.division = division
            followup.batch = batch
            followup.learning_objective = learning_objective
        db_session.commit()


def set_lab_archived(
    session_factory: sessionmaker[OrmSession], task_id: int, *, archived: bool
) -> None:
    """Hide a Lab from students (or restore it) without deleting any history."""
    with session_factory() as db_session:
        task = db_session.get(Task, task_id)
        if task is None:
            raise ValueError(f"No task with id {task_id}")
        if task.assessment_kind is not None:
            raise ValueError("Archive the Lab itself; its follow-ups are archived with it.")
        task.archived_at = datetime.now(UTC) if archived else None
        db_session.commit()


def change_password(
    session_factory: sessionmaker[OrmSession],
    *,
    role: str,
    user_id: int,
    current_password: str,
    new_password: str,
) -> None:
    """Replace a signed-in user's password; needs the current one to prove it's them.

    Raises ``ValueError`` (with a message safe to show the user) if the
    current password is wrong, the new one is too weak, or it's unchanged.
    """
    account: Student | Professor | None
    with session_factory() as db_session:
        if role == "student":
            account = db_session.get(Student, user_id)
        elif role == "professor":
            account = db_session.get(Professor, user_id)
        else:
            raise ValueError(f"Unknown role {role!r}")
        if account is None or account.password_hash is None:
            raise ValueError("Account not found.")
        if not verify_password(current_password, account.password_hash):
            raise ValueError("Your current password is incorrect.")
        problem = password_problem(new_password)
        if problem:
            raise ValueError(problem)
        if new_password == current_password:
            raise ValueError("Choose a password different from your current one.")
        account.password_hash = hash_password(new_password)
        account.must_change_password = False
        db_session.commit()


def reset_professor_password(session_factory: sessionmaker[OrmSession], professor_id: int) -> str:
    """Give a professor a new random temporary password and return it (shown once)."""
    with session_factory() as db_session:
        professor = db_session.get(Professor, professor_id)
        if professor is None:
            raise ValueError(f"No professor with id {professor_id}")
        temporary = generate_temporary_password()
        professor.password_hash = hash_password(temporary)
        professor.must_change_password = True
        db_session.commit()
        return temporary


def reset_student_password(session_factory: sessionmaker[OrmSession], student_id: int) -> str:
    """Give a student a new random temporary password and return it (shown once).

    The student must replace it at next sign-in, so the professor who
    handed it over doesn't keep a working credential.
    """
    with session_factory() as db_session:
        student = db_session.get(Student, student_id)
        if student is None:
            raise ValueError(f"No student with id {student_id}")
        temporary = generate_temporary_password()
        student.password_hash = hash_password(temporary)
        student.must_change_password = True
        db_session.commit()
        return temporary
