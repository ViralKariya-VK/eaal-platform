"""Shared pytest fixtures for CAVY tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """A fresh SQLite-backed engine in a temp directory, schema created."""
    engine = create_db_engine(tmp_path / "test.db")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_factory(db_engine: Engine) -> sessionmaker[OrmSession]:
    return create_session_factory(db_engine)


@pytest.fixture
def db_session(db_session_factory: sessionmaker[OrmSession]) -> Iterator[OrmSession]:
    with db_session_factory() as session:
        yield session


def allot(
    factory: Any,
    professor_id: int,
    course_id: int,
    *,
    year: int = 1,
    division: str = "A",
    batch: str | None = None,
) -> None:
    """Allot a professor a subject of a course for one division (an administrator does this).

    Written straight to the tables, so no student is put in a class as a side effect.
    """
    from eaal_platform.db.models import Subject, TeachingAssignment

    with factory() as db:
        subject = db.query(Subject).filter_by(course_id=course_id, semester=2 * year - 1).first()
        if subject is None:
            subject = Subject(course_id=course_id, semester=2 * year - 1, name=f"Subject {year}")
            db.add(subject)
            db.flush()
        db.add(
            TeachingAssignment(
                professor_id=professor_id,
                subject_id=subject.id,
                division=division,
                batch=batch,
            )
        )
        db.commit()


def assign_any_course(factory: Any, professor_email: str) -> int:
    """Allot a professor division A of a test course (year 1); returns the course id."""
    from eaal_platform.db import academics
    from eaal_platform.db.models import Course, Professor

    with factory() as db:
        course = db.query(Course).filter_by(name="Test course").first()
        course_id = course.id if course else None
        professor_id = db.query(Professor).filter_by(email=professor_email).one().id
    if course_id is None:
        course_id = academics.add_course(factory, "Test course", 3)
        for division in ("A", "B"):
            academics.add_option(factory, course_id, "division", division)
        academics.add_option(factory, course_id, "batch", "1")
    existing = academics.professor_course_ids(factory, professor_id)
    academics.set_professor_courses(factory, professor_id, [*existing, course_id], adopt=False)
    allot(factory, professor_id, course_id)
    return course_id
