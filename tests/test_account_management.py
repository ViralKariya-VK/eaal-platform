"""Tests for the Profile / account features: change password, professor-driven
reset, the student roster, and the password rule."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.auth import generate_temporary_password, password_problem, verify_password
from eaal_platform.db.bootstrap import create_professor_account, create_student_account
from eaal_platform.db.engine import create_db_engine, init_db
from eaal_platform.db.models import Student
from eaal_platform.events.logger import EventLogger

_OLD = "old-password-1"
_NEW = "brand-new-pass-2"


@pytest.fixture
def api(db_session_factory: sessionmaker[OrmSession]) -> CavyApi:
    create_professor_account(
        db_session_factory, display_name="Dr. K", email="prof@example.com", password=_OLD
    )
    create_student_account(
        db_session_factory,
        display_name="Asha",
        email="asha@example.com",
        password=_OLD,
        enrollment_no="BSC1",
    )
    create_student_account(
        db_session_factory, display_name="Bram", email="bram@example.com", password=_OLD
    )
    return CavyApi(db_session_factory, EventLogger(db_session_factory))


# -- the password rule ---------------------------------------------------------------------


def test_password_rule() -> None:
    assert password_problem("1234567") == "Password must be at least 8 characters."
    assert password_problem("12345678") is None


def test_create_account_enforces_the_minimum_in_the_backend(api: CavyApi) -> None:
    result = api.create_account("student", "Cy", "cy@example.com", "short")

    assert result == {"ok": False, "error": "Password must be at least 8 characters."}
    assert api.login("student", "cy@example.com", "short")["ok"] is False


def test_temporary_passwords_are_valid_and_unambiguous() -> None:
    seen = {generate_temporary_password() for _ in range(50)}

    assert len(seen) == 50
    for password in seen:
        assert password_problem(password) is None
        assert not set(password) & set("0O1lI")


# -- change password -----------------------------------------------------------------------


def test_student_changes_password(api: CavyApi) -> None:
    api.login("student", "asha@example.com", _OLD)

    assert api.change_password(_OLD, _NEW) == {"ok": True}

    api.logout()
    assert api.login("student", "asha@example.com", _OLD)["ok"] is False
    assert api.login("student", "asha@example.com", _NEW)["ok"] is True


def test_professor_changes_password(api: CavyApi) -> None:
    api.login("professor", "prof@example.com", _OLD)

    assert api.change_password(_OLD, _NEW) == {"ok": True}

    api.logout()
    assert api.login("professor", "prof@example.com", _NEW)["ok"] is True


def test_wrong_current_password_is_refused(api: CavyApi) -> None:
    api.login("student", "asha@example.com", _OLD)

    result = api.change_password("not-my-password", _NEW)

    assert result == {"ok": False, "error": "Your current password is incorrect."}
    api.logout()
    assert api.login("student", "asha@example.com", _OLD)["ok"] is True


def test_weak_or_unchanged_new_password_is_refused(api: CavyApi) -> None:
    api.login("student", "asha@example.com", _OLD)

    assert api.change_password(_OLD, "short")["error"] == (
        "Password must be at least 8 characters."
    )
    assert "different" in api.change_password(_OLD, _OLD)["error"]


def test_change_password_needs_a_login(api: CavyApi) -> None:
    with pytest.raises(ValueError, match="Not logged in"):
        api.change_password(_OLD, _NEW)


# -- reset by professor --------------------------------------------------------------------


def _student_id(api: CavyApi, email: str) -> int:
    """Sign in as the professor and put both students in their class."""
    api.login("professor", "prof@example.com", _OLD)
    api.add_students_to_class([s["id"] for s in api.get_unassigned_students()])
    return next(s["id"] for s in api.get_students() if s["email"] == email)


def test_reset_gives_a_working_temporary_password_that_must_be_replaced(api: CavyApi) -> None:
    student_id = _student_id(api, "asha@example.com")

    result = api.reset_student_password(student_id)

    assert result["ok"] is True
    temporary = result["temporary_password"]
    api.logout()
    assert api.login("student", "asha@example.com", _OLD)["ok"] is False
    login = api.login("student", "asha@example.com", temporary)
    assert login["ok"] is True
    assert login["must_change_password"] is True
    assert api.get_profile()["must_change_password"] is True

    assert api.change_password(temporary, _NEW) == {"ok": True}
    assert api.get_profile()["must_change_password"] is False
    api.logout()
    assert api.login("student", "asha@example.com", _NEW)["must_change_password"] is False


def test_reset_only_affects_the_chosen_student(api: CavyApi) -> None:
    student_id = _student_id(api, "asha@example.com")

    api.reset_student_password(student_id)

    api.logout()
    assert api.login("student", "bram@example.com", _OLD)["ok"] is True


def test_reset_is_professor_only_and_validates_the_student(api: CavyApi) -> None:
    student_id = _student_id(api, "asha@example.com")
    assert api.reset_student_password(99999) == {
        "ok": False,
        "error": "That student isn't in your class.",
    }

    api.login("student", "bram@example.com", _OLD)
    with pytest.raises(ValueError, match="professor"):
        api.reset_student_password(student_id)
    with pytest.raises(ValueError, match="professor"):
        api.get_students()


def test_the_temporary_password_is_stored_hashed(
    api: CavyApi, db_session_factory: sessionmaker[OrmSession]
) -> None:
    student_id = _student_id(api, "asha@example.com")

    temporary = api.reset_student_password(student_id)["temporary_password"]

    with db_session_factory() as db:
        stored = db.get(Student, student_id).password_hash  # type: ignore[union-attr]
    assert stored is not None
    assert temporary not in stored
    assert verify_password(temporary, stored)


# -- roster / profile ----------------------------------------------------------------------


def test_roster_lists_students_alphabetically(api: CavyApi) -> None:
    _student_id(api, "asha@example.com")

    roster = api.get_students()

    assert [s["name"] for s in roster] == ["Asha", "Bram"]
    assert roster[0]["enrollment_no"] == "BSC1"
    assert all(
        set(entry)
        == {
            "id",
            "name",
            "email",
            "enrollment_no",
            "must_change_password",
            "professor_id",
            "professor_name",
        }
        for entry in roster
    )


def test_get_profile(api: CavyApi) -> None:
    api.login("student", "asha@example.com", _OLD)
    assert api.get_profile() == {
        "role": "student",
        "name": "Asha",
        "email": "asha@example.com",
        "enrollment_no": "BSC1",
        "must_change_password": False,
    }
    api.login("professor", "prof@example.com", _OLD)
    assert api.get_profile()["role"] == "professor"
    api.logout()
    with pytest.raises(ValueError, match="Not logged in"):
        api.get_profile()


# -- migration -----------------------------------------------------------------------------


def test_init_db_upgrades_students_without_the_reset_flag(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "old.db")
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE students DROP COLUMN must_change_password"))
        conn.execute(text("INSERT INTO students (display_name) VALUES ('Legacy')"))
    engine.dispose()

    upgraded = create_db_engine(tmp_path / "old.db")
    init_db(upgraded)

    with upgraded.connect() as conn:
        rows = conn.execute(text("select display_name, must_change_password from students"))
        assert rows.fetchall() == [("Legacy", 0)]
    upgraded.dispose()
