"""Tests for the stage-unlock rule: a stage opens once the previous one
has a submitted session."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.bootstrap import (
    PRACTICE_TASK_TITLE,
    create_student_account,
    seed_demo_content,
    start_stage_session,
)
from eaal_platform.db.models import Session, Stage, StageType, Task
from eaal_platform.db.progress import has_submitted_stage, is_stage_unlocked


def _student_id(session_factory: sessionmaker[OrmSession], email: str) -> int:
    return create_student_account(
        session_factory, display_name="Local Student", email=email, password="hunter2"
    )


def _get_stages(session_factory: sessionmaker[OrmSession]) -> list[Stage]:
    with session_factory() as db_session:
        task = db_session.query(Task).filter(Task.title != PRACTICE_TASK_TITLE).first()
        assert task is not None
        return list(task.stages)


def test_first_stage_always_unlocked(db_session_factory: sessionmaker[OrmSession]) -> None:
    seed_demo_content(db_session_factory)
    student_id = _student_id(db_session_factory, "student1@example.com")
    stages = _get_stages(db_session_factory)

    assert stages[0].stage_type == StageType.LEARNING
    assert is_stage_unlocked(db_session_factory, student_id, stages[0]) is True


def test_second_stage_locked_until_first_is_submitted(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    student_id = _student_id(db_session_factory, "student2@example.com")
    stages = _get_stages(db_session_factory)

    assert is_stage_unlocked(db_session_factory, student_id, stages[1]) is False

    start_stage_session(db_session_factory, student_id, stages[0].id)
    assert is_stage_unlocked(db_session_factory, student_id, stages[1]) is False


def test_second_stage_unlocks_after_submission(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    student_id = _student_id(db_session_factory, "student3@example.com")
    stages = _get_stages(db_session_factory)

    session_id = start_stage_session(db_session_factory, student_id, stages[0].id)
    with db_session_factory() as db_session:
        session = db_session.get(Session, session_id)
        assert session is not None
        session.submitted_at = datetime.now(UTC)
        db_session.commit()

    assert is_stage_unlocked(db_session_factory, student_id, stages[1]) is True
    # The third stage still isn't — only the immediately-previous one counts.
    assert is_stage_unlocked(db_session_factory, student_id, stages[2]) is False


def test_has_submitted_stage(db_session_factory: sessionmaker[OrmSession]) -> None:
    seed_demo_content(db_session_factory)
    student_id = _student_id(db_session_factory, "student4@example.com")
    stages = _get_stages(db_session_factory)

    assert has_submitted_stage(db_session_factory, student_id, stages[0].id) is False

    session_id = start_stage_session(db_session_factory, student_id, stages[0].id)
    with db_session_factory() as db_session:
        session = db_session.get(Session, session_id)
        assert session is not None
        session.submitted_at = datetime.now(UTC)
        db_session.commit()

    assert has_submitted_stage(db_session_factory, student_id, stages[0].id) is True


def test_unsubmitted_attempt_does_not_count(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    student_id = _student_id(db_session_factory, "student5@example.com")
    stages = _get_stages(db_session_factory)

    start_stage_session(db_session_factory, student_id, stages[0].id)
    assert has_submitted_stage(db_session_factory, student_id, stages[0].id) is False
