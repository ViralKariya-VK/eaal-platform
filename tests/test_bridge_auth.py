"""Tests for CavyApi's login/create_account/logout — the Login screen's
entire backend surface.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import create_professor_account, create_student_account
from eaal_platform.events.logger import EventLogger


def _api(db_session_factory: sessionmaker[OrmSession]) -> tuple[CavyApi, EventLogger]:
    logger = EventLogger(db_session_factory, batch_size=1, flush_interval=0.05)
    logger.start()
    return CavyApi(db_session_factory, logger), logger


def test_create_account_then_login_as_student(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _api(db_session_factory)
    created = api.create_account("student", "Ada", "ada@example.com", "hunter2")
    assert created["ok"] is True

    result = api.login("student", "ada@example.com", "hunter2")
    assert result == {"ok": True, "role": "student", "name": "Ada"}
    assert api.get_student_name() == "Ada"
    logger.stop()


def test_create_account_then_login_as_professor(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _api(db_session_factory)
    api.create_account("professor", "Dr. Kariya", "prof@example.com", "hunter2")

    result = api.login("professor", "prof@example.com", "hunter2")
    assert result == {"ok": True, "role": "professor", "name": "Dr. Kariya"}
    logger.stop()


def test_login_rejects_wrong_password(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _api(db_session_factory)
    create_student_account(
        db_session_factory, display_name="Ada", email="ada@example.com", password="hunter2"
    )
    result = api.login("student", "ada@example.com", "wrong")
    assert result["ok"] is False
    logger.stop()


def test_login_as_student_does_not_authenticate_as_professor(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _api(db_session_factory)
    create_professor_account(
        db_session_factory, display_name="Dr. Kariya", email="prof@example.com", password="pw"
    )
    result = api.login("student", "prof@example.com", "pw")
    assert result["ok"] is False
    logger.stop()


def test_logging_in_as_one_role_clears_the_other(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _api(db_session_factory)
    create_student_account(
        db_session_factory, display_name="Ada", email="ada@example.com", password="hunter2"
    )
    create_professor_account(
        db_session_factory, display_name="Dr. Kariya", email="prof@example.com", password="pw"
    )

    api.login("professor", "prof@example.com", "pw")
    api.login("student", "ada@example.com", "hunter2")

    assert api._current_professor_id is None
    assert api._current_student_id is not None
    logger.stop()


def test_logout_clears_both_roles(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _api(db_session_factory)
    create_student_account(
        db_session_factory, display_name="Ada", email="ada@example.com", password="hunter2"
    )
    api.login("student", "ada@example.com", "hunter2")
    api.logout()

    assert api.get_student_name() == "Student"  # falls back once logged out
    logger.stop()


def test_create_account_rejects_duplicate_email(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _api(db_session_factory)
    api.create_account("student", "Ada", "ada@example.com", "hunter2")
    result = api.create_account("student", "Someone Else", "ada@example.com", "other")
    assert result["ok"] is False
    assert "already registered" in result["error"]
    logger.stop()
