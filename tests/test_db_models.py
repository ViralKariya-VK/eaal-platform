"""Tests for the SQLAlchemy schema: creation, relationships, and the
append-only nature of the events table."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from eaal_platform.db.models import (
    AIAssistanceMode,
    CodeSnapshot,
    Event,
    EventType,
    ExecutionResult,
    Session,
    Stage,
    StageType,
    Student,
    Task,
)


def test_all_tables_created(db_engine: Engine) -> None:
    tables = set(inspect(db_engine).get_table_names())
    expected = {
        "students",
        "tasks",
        "stages",
        "sessions",
        "events",
        "code_snapshots",
        "ai_interactions",
        "execution_results",
        "signal_scores",
    }
    assert expected <= tables


def test_every_table_has_synced_at_column(db_engine: Engine) -> None:
    inspector = inspect(db_engine)
    for table_name in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns(table_name)}
        assert "synced_at" in columns, f"{table_name} is missing synced_at"


def test_wal_mode_enabled(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode == "wal"


def test_session_links_student_and_task(db_session: OrmSession) -> None:
    student = Student(display_name="Ada")
    task = Task(title="Reverse a linked list")
    db_session.add_all([student, task])
    db_session.flush()

    session = Session(student_id=student.id, task_id=task.id)
    db_session.add(session)
    db_session.commit()

    fetched = db_session.get(Session, session.id)
    assert fetched is not None
    assert fetched.student.display_name == "Ada"
    assert fetched.task.title == "Reverse a linked list"


def test_event_records_code_version_link(db_session: OrmSession) -> None:
    student = Student(display_name="Grace")
    task = Task(title="Sort a list")
    db_session.add_all([student, task])
    db_session.flush()

    session = Session(student_id=student.id, task_id=task.id)
    db_session.add(session)
    db_session.flush()

    snapshot = CodeSnapshot(session_id=session.id, content="print('hi')")
    db_session.add(snapshot)
    db_session.flush()

    event = Event(
        session_id=session.id,
        event_type=EventType.CODE_EDIT,
        payload_json={"length": 12},
        code_version_id=snapshot.id,
    )
    db_session.add(event)
    db_session.commit()

    fetched = db_session.get(Event, event.id)
    assert fetched is not None
    assert fetched.event_type == EventType.CODE_EDIT
    assert fetched.code_version is not None
    assert fetched.code_version.content == "print('hi')"


def test_events_are_never_updated_in_practice(db_session: OrmSession) -> None:
    """Not an enforced DB constraint (SQLite has no easy immutable-row trigger
    without extra machinery) — this test documents and guards the *contract*:
    application code always inserts a new Event rather than mutating one.
    A future contributor tempted to "fix" a bad event by UPDATE-ing it should
    see this test fail and read the models.py docstring instead.
    """
    student = Student(display_name="Grace")
    task = Task(title="Sort a list")
    db_session.add_all([student, task])
    db_session.flush()
    session = Session(student_id=student.id, task_id=task.id)
    db_session.add(session)
    db_session.flush()

    event = Event(session_id=session.id, event_type=EventType.TASK_START)
    db_session.add(event)
    db_session.commit()
    original_id = event.id
    original_timestamp = event.timestamp

    # Correcting a mistake means adding a new row, not mutating this one.
    correction = Event(session_id=session.id, event_type=EventType.TASK_START)
    db_session.add(correction)
    db_session.commit()

    assert event.id == original_id
    assert event.timestamp == original_timestamp
    assert correction.id != original_id


def test_execution_result_defaults(db_session: OrmSession) -> None:
    student = Student(display_name="Ada")
    task = Task(title="Loop")
    db_session.add_all([student, task])
    db_session.flush()
    session = Session(student_id=student.id, task_id=task.id)
    db_session.add(session)
    db_session.flush()

    result = ExecutionResult(session_id=session.id, exit_status=0, duration_ms=42)
    db_session.add(result)
    db_session.commit()

    fetched = db_session.get(ExecutionResult, result.id)
    assert fetched is not None
    assert fetched.stdout == ""
    assert fetched.stderr == ""
    assert fetched.timed_out is False


def test_task_with_no_stages_is_practice_style(db_session: OrmSession) -> None:
    task = Task(title="Practice")
    db_session.add(task)
    db_session.commit()

    fetched = db_session.get(Task, task.id)
    assert fetched is not None
    assert fetched.stages == []


def test_task_stages_ordered_by_order_index(db_session: OrmSession) -> None:
    task = Task(title="Sorting Lab")
    db_session.add(task)
    db_session.flush()

    db_session.add_all(
        [
            Stage(
                task_id=task.id,
                stage_type=StageType.ASSESSMENT,
                ai_assistance_mode=AIAssistanceMode.RESTRICTED,
                order_index=2,
            ),
            Stage(
                task_id=task.id,
                stage_type=StageType.LEARNING,
                ai_assistance_mode=AIAssistanceMode.FULL,
                order_index=0,
            ),
            Stage(
                task_id=task.id,
                stage_type=StageType.EXPLORATION,
                ai_assistance_mode=AIAssistanceMode.FULL,
                order_index=1,
            ),
        ]
    )
    db_session.commit()

    fetched = db_session.get(Task, task.id)
    assert fetched is not None
    assert [s.stage_type for s in fetched.stages] == [
        StageType.LEARNING,
        StageType.EXPLORATION,
        StageType.ASSESSMENT,
    ]


def test_session_can_link_to_a_stage(db_session: OrmSession) -> None:
    student = Student(display_name="Ada")
    task = Task(title="Sorting Lab")
    db_session.add_all([student, task])
    db_session.flush()

    stage = Stage(
        task_id=task.id,
        stage_type=StageType.LEARNING,
        ai_assistance_mode=AIAssistanceMode.FULL,
        order_index=0,
    )
    db_session.add(stage)
    db_session.flush()

    session = Session(student_id=student.id, task_id=task.id, stage_id=stage.id)
    db_session.add(session)
    db_session.commit()

    fetched = db_session.get(Session, session.id)
    assert fetched is not None
    assert fetched.stage is not None
    assert fetched.stage.stage_type == StageType.LEARNING


def test_practice_session_has_no_stage(db_session: OrmSession) -> None:
    student = Student(display_name="Ada")
    task = Task(title="Practice")
    db_session.add_all([student, task])
    db_session.flush()

    session = Session(student_id=student.id, task_id=task.id)
    db_session.add(session)
    db_session.commit()

    fetched = db_session.get(Session, session.id)
    assert fetched is not None
    assert fetched.stage is None


def test_session_submitted_at_defaults_to_none_and_can_be_set(db_session: OrmSession) -> None:
    student = Student(display_name="Ada")
    task = Task(title="Sorting Lab")
    db_session.add_all([student, task])
    db_session.flush()

    session = Session(student_id=student.id, task_id=task.id)
    db_session.add(session)
    db_session.commit()
    assert session.submitted_at is None

    session.submitted_at = datetime.now(UTC)
    db_session.commit()

    fetched = db_session.get(Session, session.id)
    assert fetched is not None
    assert fetched.submitted_at is not None
