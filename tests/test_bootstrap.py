"""Tests for local-account, demo-content, and session-creation helpers."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.bootstrap import (
    PRACTICE_TASK_TITLE,
    authenticate_professor,
    authenticate_student,
    create_professor_account,
    create_student_account,
    seed_demo_content,
    start_practice_session,
    start_stage_session,
)
from eaal_platform.db.models import Session, Stage, StageType, Task


def _student_id(session_factory: sessionmaker[OrmSession], email: str = "a@example.com") -> int:
    return create_student_account(
        session_factory, display_name="Local Student", email=email, password="hunter2"
    )


def test_create_student_account_and_authenticate(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    student_id = create_student_account(
        db_session_factory, display_name="Ada", email="ada@example.com", password="hunter2"
    )
    result = authenticate_student(db_session_factory, email="ada@example.com", password="hunter2")
    assert result == (student_id, "Ada")


def test_authenticate_student_rejects_wrong_password(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    create_student_account(
        db_session_factory, display_name="Ada", email="ada@example.com", password="hunter2"
    )
    assert (
        authenticate_student(db_session_factory, email="ada@example.com", password="wrong") is None
    )


def test_authenticate_student_rejects_unknown_email(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    assert (
        authenticate_student(db_session_factory, email="nobody@example.com", password="x") is None
    )


def test_create_student_account_rejects_duplicate_email(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    create_student_account(
        db_session_factory, display_name="Ada", email="ada@example.com", password="hunter2"
    )
    with pytest.raises(ValueError, match="already registered"):
        create_student_account(
            db_session_factory, display_name="Ada Two", email="ada@example.com", password="other"
        )


def test_student_and_professor_emails_cannot_collide(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    create_student_account(
        db_session_factory, display_name="Ada", email="shared@example.com", password="hunter2"
    )
    with pytest.raises(ValueError, match="already registered"):
        create_professor_account(
            db_session_factory,
            display_name="Dr. Ada",
            email="shared@example.com",
            password="other",
        )


def test_create_professor_account_and_authenticate(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    professor_id = create_professor_account(
        db_session_factory, display_name="Dr. Kariya", email="prof@example.com", password="pw"
    )
    result = authenticate_professor(db_session_factory, email="prof@example.com", password="pw")
    assert result == (professor_id, "Dr. Kariya")


def test_seed_demo_content_creates_practice_task(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    with db_session_factory() as db_session:
        practice = db_session.query(Task).filter_by(title=PRACTICE_TASK_TITLE).one()
        assert practice.stages == []


def test_seed_demo_content_creates_labs_with_three_ordered_stages(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    with db_session_factory() as db_session:
        labs = db_session.query(Task).filter(Task.title != PRACTICE_TASK_TITLE).all()
        assert len(labs) >= 1
        for lab in labs:
            assert len(lab.stages) == 3
            assert [s.stage_type for s in lab.stages] == [
                StageType.LEARNING,
                StageType.EXPLORATION,
                StageType.ASSESSMENT,
            ]


def test_seed_demo_content_is_idempotent(db_session_factory: sessionmaker[OrmSession]) -> None:
    seed_demo_content(db_session_factory)
    seed_demo_content(db_session_factory)
    with db_session_factory() as db_session:
        tasks = db_session.query(Task).filter_by(title=PRACTICE_TASK_TITLE).all()
        assert len(tasks) == 1


def test_start_practice_session_links_no_stage(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    student_id = _student_id(db_session_factory)

    session_id = start_practice_session(db_session_factory, student_id)

    with db_session_factory() as db_session:
        session = db_session.get(Session, session_id)
        assert session is not None
        assert session.stage_id is None
        assert session.task.title == PRACTICE_TASK_TITLE


def test_start_stage_session_links_correct_stage_and_task(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    student_id = _student_id(db_session_factory)

    with db_session_factory() as db_session:
        stage = db_session.query(Stage).filter_by(stage_type=StageType.LEARNING).first()
        assert stage is not None
        stage_id = stage.id
        task_id = stage.task_id

    session_id = start_stage_session(db_session_factory, student_id, stage_id)

    with db_session_factory() as db_session:
        session = db_session.get(Session, session_id)
        assert session is not None
        assert session.stage_id == stage_id
        assert session.task_id == task_id


def test_start_stage_session_rejects_unknown_stage(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    student_id = _student_id(db_session_factory)
    with pytest.raises(ValueError, match="999999"):
        start_stage_session(db_session_factory, student_id, stage_id=999999)
