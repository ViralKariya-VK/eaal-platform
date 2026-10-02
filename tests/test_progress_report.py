"""Tests for the student's long-run progress summary."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import (
    create_lab,
    create_student_account,
    seed_demo_content,
    start_practice_session,
    start_stage_session,
)
from eaal_platform.db.models import AIAssistanceMode, SignalScore, StageType
from eaal_platform.db.models import Session as SessionModel
from eaal_platform.events.logger import EventLogger
from eaal_platform.progress_report import SessionRow, build_progress


def _row(
    n: int,
    value: float | None,
    *,
    kind: str = "lab",
    signals: dict[str, float | None] | None = None,
) -> SessionRow:
    return {
        "session_id": n,
        "title": f"Lab {n}",
        "kind": kind,
        "stage": None,
        "submitted_at": f"2026-01-{n:02d}T10:00:00",
        "minutes": 10,
        "ai_interactions": n,
        "signals": signals if signals is not None else {"S1.1": value, "S3.1": value},
    }


def test_empty_history() -> None:
    result = build_progress([])
    assert result["totals"]["sessions"] == 0
    assert result["totals"]["average_score"] is None
    assert result["overall"]["direction"] == "not_enough_data"
    assert result["history"] == []


def test_totals_and_history_order() -> None:
    rows = [_row(2, 0.6), _row(1, 0.4), _row(3, 0.8, kind="practice")]
    result = build_progress(rows)
    assert result["totals"]["sessions"] == 3
    assert result["totals"]["labs"] == 2
    assert result["totals"]["practice"] == 1
    assert result["totals"]["minutes"] == 30
    assert result["totals"]["average_score"] == 60.0
    assert result["totals"]["latest_score"] == 80.0
    assert [h["session_id"] for h in result["history"]] == [3, 2, 1]  # newest first
    assert result["overall"]["series"] == [40.0, 60.0, 80.0]  # oldest first


def test_trend_directions() -> None:
    up = build_progress([_row(i, v) for i, v in enumerate([0.2, 0.3, 0.4, 0.7, 0.8, 0.9], 1)])
    assert up["overall"]["direction"] == "up"
    down = build_progress([_row(i, v) for i, v in enumerate([0.9, 0.8, 0.7, 0.4, 0.3, 0.2], 1)])
    assert down["overall"]["direction"] == "down"
    flat = build_progress([_row(i, v) for i, v in enumerate([0.5, 0.51, 0.5, 0.52], 1)])
    assert flat["overall"]["direction"] == "steady"
    one = build_progress([_row(1, 0.5)])
    assert one["overall"]["direction"] == "not_enough_data"


def test_two_sessions_compare_last_with_first() -> None:
    result = build_progress([_row(1, 0.3), _row(2, 0.6)])
    assert result["overall"]["direction"] == "up"
    assert result["overall"]["change"] == 30.0


def test_missing_signals_are_ignored_not_zero() -> None:
    result = build_progress([_row(1, None, signals={"S1.1": 1.0, "S1.2": None, "S3.1": None})])
    assert result["totals"]["average_score"] == 100.0
    p1, p2, p3 = result["pillars"]
    assert p1["latest"] == 100.0
    assert p2["latest"] is None
    assert p3["latest"] is None


def test_session_with_no_signals_has_no_score() -> None:
    result = build_progress([_row(1, None, signals={})])
    assert result["history"][0]["score"] is None
    assert result["totals"]["average_score"] is None


def test_strengths_and_growth_areas_do_not_overlap() -> None:
    signals: dict[str, float | None] = {"S1.1": 0.9, "S1.2": 0.8, "S2.1": 0.2}
    result = build_progress([_row(1, 0, signals=signals)])
    strengths = {s["key"] for s in result["strengths"]}
    growth = {g["key"] for g in result["growth_areas"]}
    assert strengths == {"S1.1", "S1.2", "S2.1"}
    assert growth == set()  # only three signals: all are "strengths"
    many: dict[str, float | None] = {
        "S1.1": 0.9,
        "S1.2": 0.8,
        "S1.3": 0.7,
        "S2.1": 0.3,
        "S2.2": 0.2,
        "S3.1": 0.1,
    }
    result = build_progress([_row(1, 0, signals=many)])
    assert [s["key"] for s in result["strengths"]] == ["S1.1", "S1.2", "S1.3"]
    assert [g["key"] for g in result["growth_areas"]] == ["S3.1", "S2.2", "S2.1"]


# -- bridge --------------------------------------------------------------------


def _signed_in_api(
    factory: sessionmaker[OrmSession], email: str = "ada@example.com"
) -> tuple[CavyApi, EventLogger, int]:
    logger = EventLogger(factory, batch_size=1, flush_interval=0.05)
    logger.start()
    api = CavyApi(factory, logger)
    student_id = create_student_account(
        factory, display_name="Ada", email=email, password="hunter2-hunter2"
    )
    api.login("student", email, "hunter2-hunter2")
    return api, logger, student_id


def test_progress_requires_student_login(db_session_factory: sessionmaker[OrmSession]) -> None:
    logger = EventLogger(db_session_factory, batch_size=1, flush_interval=0.05)
    api = CavyApi(db_session_factory, logger)
    with pytest.raises(ValueError, match="Not logged in"):
        api.get_my_progress()


def test_progress_scores_submitted_sessions_once_and_only_own(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    seed_demo_content(db_session_factory)
    api, logger, student_id = _signed_in_api(db_session_factory)
    other_id = create_student_account(
        db_session_factory, display_name="Bob", email="bob@example.com", password="hunter2-hunter2"
    )
    # Ada: one submitted practice session, one started-but-unsubmitted. Bob: one submitted.
    submitted = start_practice_session(db_session_factory, student_id)
    start_practice_session(db_session_factory, student_id)
    bobs = start_practice_session(db_session_factory, other_id)
    api.submit_session(submitted, {"main.py": "print(1)\n"})
    with db_session_factory() as db:
        db.get(SessionModel, bobs).submitted_at = db.get(SessionModel, submitted).submitted_at  # type: ignore[union-attr]
        db.commit()

    first = api.get_my_progress()
    assert first["totals"]["sessions"] == 1
    assert first["history"][0]["session_id"] == submitted
    assert first["history"][0]["kind"] == "practice"

    with db_session_factory() as db:
        rows_after_first = db.query(SignalScore).count()
        assert db.query(SignalScore).filter_by(session_id=bobs).count() == 0
    api.get_my_progress()
    with db_session_factory() as db:
        assert db.query(SignalScore).count() == rows_after_first  # not re-scored
    logger.stop()


def test_progress_labels_lab_stage_and_uses_latest_scores(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger, student_id = _signed_in_api(db_session_factory)
    task_id = create_lab(
        db_session_factory,
        title="Primes",
        description="d",
        learning_objective=None,
        difficulty=None,
        professor_name="P",
        course=None,
        division=None,
        batch=None,
        stage_plan=((StageType.LEARNING, AIAssistanceMode.FULL, 10),),
    )
    from eaal_platform.db.models import Stage

    with db_session_factory() as db:
        stage_id = db.query(Stage).filter_by(task_id=task_id).one().id
    session_id = start_stage_session(db_session_factory, student_id, stage_id)
    api.submit_session(session_id, {"main.py": "x=1\n"})
    with db_session_factory() as db:
        db.add(SignalScore(session_id=session_id, signal_key="S2.1", value=0.2))
        db.add(SignalScore(session_id=session_id, signal_key="S2.1", value=0.8))
        db.commit()

    entry = api.get_my_progress()["history"][0]
    assert entry["kind"] == "lab"
    assert entry["stage"] == "LEARNING"
    assert entry["title"] == "Primes"
    logger.stop()
