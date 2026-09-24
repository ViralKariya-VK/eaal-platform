"""Tests for S3.3 (Knowledge Transfer) and S3.4 (Retention & Independent
Recall) — the two signals that require a professor-authored follow-on Task
(see ``db.models.AssessmentKind``) rather than ordinary Lab activity.

Needs both a professor (to spin off the follow-on assessment) and a student
(to attempt it), so this mirrors ``test_bridge_professor.py``'s fixtures
rather than ``test_signals.py``'s student-only ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import create_professor_account, create_student_account
from eaal_platform.db.models import Session as SessionModel
from eaal_platform.events.logger import EventLogger
from eaal_platform.signals.compute import compute_all_signals

_PROFESSOR_EMAIL = "prof.followup@example.com"
_STUDENT_EMAIL = "student.followup@example.com"
_PASSWORD = "hunter2"


def _make_professor_api(
    db_session_factory: sessionmaker[OrmSession],
) -> tuple[CavyApi, EventLogger]:
    create_professor_account(
        db_session_factory, display_name="Dr. Kariya", email=_PROFESSOR_EMAIL, password=_PASSWORD
    )
    create_student_account(
        db_session_factory, display_name="Local Student", email=_STUDENT_EMAIL, password=_PASSWORD
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


def _files(code: str) -> dict[str, str]:
    return {"main.py": code}


def _create_lab(api: CavyApi) -> int:
    result = api.create_lab(
        {
            "title": "Recursion Basics",
            "course": "CS",
            "division": "A",
            "batch": "2026",
            "topic": "Recursion",
            "description": "Implement a recursive factorial.",
            "difficulty": "Easy",
            "stages": [
                {"duration_minutes": 20, "ai_assistance_mode": "FULL"},
                {"duration_minutes": 20, "ai_assistance_mode": "FULL"},
                {"duration_minutes": 20, "ai_assistance_mode": "RESTRICTED"},
            ],
        }
    )
    return int(result["task_id"])


def test_get_followup_assessments_readable_by_professor_who_created_it(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    """The Lab Report screen (professor-only) reads this too — it must not
    require a student login the way the taking side does."""
    api, logger = _make_professor_api(db_session_factory)
    source_task_id = _create_lab(api)
    api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "TRANSFER",
            "title": "Recursion Basics — Transfer Task",
            "description": "A different problem, same concept.",
            "ai_assistance_mode": "RESTRICTED",
            "duration_minutes": 20,
        }
    )

    followups = api.get_followup_assessments(source_task_id)
    logger.stop()
    assert len(followups) == 1
    assert followups[0]["title"] == "Recursion Basics — Transfer Task"


def test_knowledge_transfer_none_for_an_ordinary_lab_session(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    task_id = _create_lab(api)

    _login_as_student(api)
    stages = api.get_stages(task_id)["stages"]
    session_id = api.start_stage(stages[2]["id"])["session_id"]
    api.run_code(session_id, _files("print('ok')"))

    logger.stop()
    results = compute_all_signals(db_session_factory, session_id, None)
    assert results["S3.3"].value is None
    assert results["S3.3"].reason


def test_knowledge_transfer_scores_a_clean_run_on_the_transfer_task(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    source_task_id = _create_lab(api)
    transfer_task_id = api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "TRANSFER",
            "title": "Recursion Basics — Transfer Task",
            "description": "Now implement a recursive Fibonacci instead.",
            "ai_assistance_mode": "RESTRICTED",
            "duration_minutes": 20,
        }
    )["task_id"]

    _login_as_student(api)
    followups = api.get_followup_assessments(source_task_id)
    assert len(followups) == 1
    assert followups[0]["assessment_kind"] == "TRANSFER"
    stage_id = followups[0]["stage_id"]

    session_id = api.start_stage(stage_id)["session_id"]
    api.run_code(session_id, _files("print('fib ok')"))

    logger.stop()
    results = compute_all_signals(db_session_factory, session_id, None)
    assert results["S3.3"].value == 1.0

    # Also confirmed unrelated: a Retention read on this same Transfer
    # session shouldn't accidentally pick up any credit either.
    assert results["S3.4"].value is None
    _ = transfer_task_id


def test_retention_recall_none_when_attempted_too_soon(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    source_task_id = _create_lab(api)
    retention_task_id = api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "RETENTION",
            "title": "Recursion Basics — Retention Check",
            "description": "Re-implement it now, without the AI's help.",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 15,
        }
    )["task_id"]

    _login_as_student(api)
    original_stages = api.get_stages(source_task_id)["stages"]
    api.start_stage(original_stages[2]["id"])  # establishes the "original activity" timestamp

    retention_stages = api.get_stages(retention_task_id)["stages"]
    session_id = api.start_stage(retention_stages[0]["id"])["session_id"]
    api.run_code(session_id, _files("print('too soon')"))

    logger.stop()
    results = compute_all_signals(db_session_factory, session_id, None)
    assert results["S3.4"].value is None
    assert "24" in (results["S3.4"].reason or "")


def test_retention_recall_scores_a_clean_run_after_a_meaningful_delay(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_professor_api(db_session_factory)
    source_task_id = _create_lab(api)
    retention_task_id = api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "RETENTION",
            "title": "Recursion Basics — Retention Check",
            "description": "Re-implement it now, without the AI's help.",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 15,
        }
    )["task_id"]

    _login_as_student(api)
    original_stages = api.get_stages(source_task_id)["stages"]
    original_session_id = api.start_stage(original_stages[2]["id"])["session_id"]

    retention_stages = api.get_stages(retention_task_id)["stages"]
    session_id = api.start_stage(retention_stages[0]["id"])["session_id"]
    api.run_code(session_id, _files("print('recalled it')"))
    logger.stop()

    # No real clock can wait 24h in a test — backdate the original session's
    # start instead of the retention session's, so the *measured delay* is
    # exactly what a real 24h-later attempt would produce.
    with db_session_factory() as db_session:
        original_session = db_session.get(SessionModel, original_session_id)
        assert original_session is not None
        original_session.started_at = datetime.now(UTC) - timedelta(hours=30)
        db_session.commit()

    results = compute_all_signals(db_session_factory, session_id, None)
    assert results["S3.4"].value == 1.0
    assert results["S3.4"].evidence["delay_hours"] >= 24
