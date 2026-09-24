"""Tests for the professor-side bridge methods: authoring a Lab and reading
its report.

A separate file from ``test_bridge.py`` (which covers the student-facing
surface) purely for readability — same conventions, same fixtures. Since
``CavyApi`` only ever holds one logged-in identity at a time (logging in
as one role clears the other — see ``bridge.py``'s ``login`` docstring),
tests that need both a professor (to author/view) and a student (to
attempt) switch back and forth with ``api.login(...)``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    seed_demo_content,
)
from eaal_platform.events.logger import EventLogger

_PROFESSOR_EMAIL = "prof@example.com"
_STUDENT_EMAIL = "student@example.com"
_PASSWORD = "hunter2"


def _make_professor_api(
    db_session_factory: sessionmaker[OrmSession],
) -> tuple[CavyApi, EventLogger]:
    seed_demo_content(db_session_factory)
    create_professor_account(
        db_session_factory, display_name="Dr. Kariya", email=_PROFESSOR_EMAIL, password=_PASSWORD
    )
    create_student_account(
        db_session_factory,
        display_name="Local Student",
        email=_STUDENT_EMAIL,
        password=_PASSWORD,
        enrollment_no="BSC2026007",
    )
    logger = EventLogger(db_session_factory, batch_size=1, flush_interval=0.05)
    logger.start()
    api = CavyApi(db_session_factory, logger)
    assert api.login("professor", _PROFESSOR_EMAIL, _PASSWORD)["ok"] is True
    return api, logger


def _login_as_student(api: CavyApi) -> None:
    assert api.login("student", _STUDENT_EMAIL, _PASSWORD)["ok"] is True


def _login_as_professor(api: CavyApi) -> None:
    assert api.login("professor", _PROFESSOR_EMAIL, _PASSWORD)["ok"] is True


def _lab_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Sorting Algorithms",
        "course": "B.Sc. Data Science",
        "division": "A",
        "batch": "2026",
        "topic": "Algorithms",
        "description": "Implement and compare sorting algorithms.",
        "difficulty": "Medium",
        "stages": [
            {"duration_minutes": 20, "ai_assistance_mode": "FULL"},
            {"duration_minutes": 25, "ai_assistance_mode": "FULL"},
            {"duration_minutes": 30, "ai_assistance_mode": "RESTRICTED"},
        ],
    }
    payload.update(overrides)
    return payload


def test_create_lab_creates_task_with_three_stages(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    result = api.create_lab(_lab_payload())
    task_id = result["task_id"]

    _login_as_student(api)
    stages = api.get_stages(task_id)["stages"]
    assert len(stages) == 3
    assert [s["stage_type"] for s in stages] == ["LEARNING", "EXPLORATION", "ASSESSMENT"]
    assert stages[2]["ai_assistance_mode"] == "RESTRICTED"
    assert stages[2]["duration_minutes"] == 30
    logger.stop()


def test_create_lab_requires_professor_login(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    _login_as_student(api)
    with pytest.raises(ValueError, match="Not logged in"):
        api.create_lab(_lab_payload())
    logger.stop()


def test_create_lab_rejects_wrong_stage_count(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    payload = _lab_payload(
        stages=[{"duration_minutes": 20, "ai_assistance_mode": "FULL"}],
    )
    with pytest.raises(ValueError, match="stage config"):
        api.create_lab(payload)
    logger.stop()


def test_get_professor_labs_includes_cohort_tags_and_excludes_practice(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    api.create_lab(_lab_payload())

    labs = api.get_professor_labs()
    assert all(lab["title"] != "Practice" for lab in labs)
    created = next(lab for lab in labs if lab["title"] == "Sorting Algorithms")
    assert created["course"] == "B.Sc. Data Science"
    assert created["division"] == "A"
    assert created["batch"] == "2026"
    logger.stop()


def test_lab_report_before_any_attempt_is_empty(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    task_id = api.create_lab(_lab_payload())["task_id"]

    report = api.get_lab_report(task_id)
    assert report["total_students"] == 0
    assert report["submitted_count"] == 0
    assert report["average_score"] is None
    assert report["rows"] == []
    logger.stop()


def test_lab_report_reflects_unsubmitted_assessment_attempt(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    task_id = api.create_lab(_lab_payload())["task_id"]

    _login_as_student(api)
    stages = api.get_stages(task_id)["stages"]
    api.start_stage(stages[2]["id"])  # opens the assessment stage, never submits

    _login_as_professor(api)
    report = api.get_lab_report(task_id)
    assert report["total_students"] == 1
    assert report["submitted_count"] == 0
    assert report["not_submitted_count"] == 1
    assert report["rows"][0]["status"] == "Not Submitted"
    assert report["rows"][0]["score"] is None
    logger.stop()


def test_lab_report_reflects_submitted_assessment(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    task_id = api.create_lab(_lab_payload())["task_id"]

    _login_as_student(api)
    stages = api.get_stages(task_id)["stages"]
    session_id = api.start_stage(stages[2]["id"])["session_id"]
    api.log_code_edit(session_id, {"main.py": "print(1)"})
    api.run_code(session_id, {"main.py": "print(1)"})
    api.submit_session(session_id, {"main.py": "print(1)"})

    _login_as_professor(api)
    report = api.get_lab_report(task_id)
    assert report["total_students"] == 1
    assert report["submitted_count"] == 1
    assert report["rows"][0]["status"] == "Submitted"
    assert report["rows"][0]["student_name"] == "Local Student"
    assert report["rows"][0]["enrollment_no"] == "BSC2026007"
    assert report["rows"][0]["submitted_at"] is not None
    assert isinstance(report["average_score"], float)
    logger.stop()


def test_dashboard_summary_before_any_activity_is_empty(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    api.create_lab(_lab_payload())

    # `seed_demo_content` (called by `_make_professor_api`) already seeds two
    # demo Labs with stages, so the count is "seeded + created", not just 1.
    summary = api.get_professor_dashboard_summary()
    assert summary["total_labs"] == 3
    assert summary["total_students"] == 0
    assert summary["average_score"] is None
    assert summary["pending_submissions"] == 0
    assert summary["recent_activity"] == []
    logger.stop()


def test_dashboard_summary_reflects_submitted_and_pending_sessions(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    task_id = api.create_lab(_lab_payload())["task_id"]
    other_task_id = api.create_lab(_lab_payload(title="Recursion Basics"))["task_id"]

    _login_as_student(api)
    stages = api.get_stages(task_id)["stages"]
    session_id = api.start_stage(stages[2]["id"])["session_id"]
    api.log_code_edit(session_id, {"main.py": "print(1)"})
    api.run_code(session_id, {"main.py": "print(1)"})
    api.submit_session(session_id, {"main.py": "print(1)"})

    other_stages = api.get_stages(other_task_id)["stages"]
    api.start_stage(other_stages[2]["id"])  # never submits

    _login_as_professor(api)
    summary = api.get_professor_dashboard_summary()
    # 2 seeded demo labs + the 2 created here.
    assert summary["total_labs"] == 4
    assert summary["total_students"] == 1
    assert summary["pending_submissions"] == 1
    assert isinstance(summary["average_score"], float)
    assert len(summary["recent_activity"]) == 1
    assert summary["recent_activity"][0]["student_name"] == "Local Student"
    assert summary["recent_activity"][0]["lab_title"] == "Sorting Algorithms"
    logger.stop()
