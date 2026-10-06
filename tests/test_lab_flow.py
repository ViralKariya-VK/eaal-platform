"""Doing a lab: learning, exploration and assessment are done in turn and submitted once."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi, _bundle_files, _unbundle_files
from eaal_platform.db.bootstrap import create_student_account, seed_demo_content
from eaal_platform.db.models import Event, EventType
from eaal_platform.db.models import Session as SessionModel
from eaal_platform.events.logger import EventLogger

Factory = sessionmaker[OrmSession]


def _api(factory: Factory) -> tuple[CavyApi, EventLogger, list[dict[str, object]]]:
    seed_demo_content(factory)
    create_student_account(
        factory, display_name="Sam", email="sam@x.com", password="correct-horse-1"
    )
    logger = EventLogger(factory, batch_size=1, flush_interval=0.1)
    logger.start()
    api = CavyApi(factory, logger)
    api.login("student", "sam@x.com", "correct-horse-1")
    stages = api.get_stages(api.get_labs()[0]["id"])["stages"]
    return api, logger, stages


def test_files_survive_bundling() -> None:
    files = {"main.py": "print(1)\n\nx = 2", "util.py": "def f():\n    return 3"}
    assert _unbundle_files(_bundle_files(files)) == files


def test_moving_between_stages_keeps_the_code_and_the_session(db_session_factory: Factory) -> None:
    api, logger, stages = _api(db_session_factory)
    learning = api.start_stage(stages[0]["id"])
    assert learning["resumed"] is False
    api.log_code_edit(learning["session_id"], {"main.py": "x = 1\n"}, "main.py", False, True)

    api.start_stage(stages[1]["id"])  # Next
    again = api.start_stage(stages[0]["id"])  # Back
    assert again["session_id"] == learning["session_id"]
    assert again["resumed"] is True
    assert again["starter_files"]["main.py"] == "x = 1\n"
    logger.stop()


def test_stage_status_follows_the_student(db_session_factory: Factory) -> None:
    api, logger, stages = _api(db_session_factory)
    assert [s["status"] for s in api.get_stages(api.get_labs()[0]["id"])["stages"]] == [
        "not_started",
        "not_started",
        "not_started",
    ]
    api.start_stage(stages[0]["id"])
    after = api.get_stages(api.get_labs()[0]["id"])["stages"]
    assert [s["status"] for s in after] == ["in_progress", "not_started", "not_started"]
    assert after[1]["unlocked"] is True and after[2]["unlocked"] is False
    logger.stop()


def test_one_submit_at_the_end_submits_all_three_stages_and_only_once(
    db_session_factory: Factory,
) -> None:
    api, logger, stages = _api(db_session_factory)
    ids = [api.start_stage(s["id"])["session_id"] for s in stages]
    api.log_code_edit(ids[0], {"main.py": "learn = 1"}, "main.py", False, True)
    with db_session_factory() as db:
        assert all(db.get(SessionModel, i).submitted_at is None for i in ids)  # type: ignore[union-attr]

    api.submit_session(ids[2], {"main.py": "final = 3"})
    logger.flush()

    with db_session_factory() as db:
        assert all(db.get(SessionModel, i).submitted_at is not None for i in ids)  # type: ignore[union-attr]
        submissions = db.query(Event).filter_by(event_type=EventType.SUBMISSION).all()
        assert sorted(e.session_id for e in submissions) == sorted(ids)
    assert [s["status"] for s in api.get_stages(api.get_labs()[0]["id"])["stages"]] == [
        "submitted"
    ] * 3

    # A lab is submitted once: it can't be reopened or submitted again.
    lab = api.get_stages(api.get_labs()[0]["id"])
    assert lab["lab_submitted"] is True and lab["submitted_session_id"] == ids[2]
    with pytest.raises(ValueError, match="already submitted"):
        api.start_stage(stages[0]["id"])
    with pytest.raises(ValueError, match="already submitted"):
        api.submit_session(ids[2], {"main.py": "again"})
    with pytest.raises(ValueError, match="already submitted"):
        api.submit_session(ids[0], {"main.py": "again"})
    logger.stop()


def test_a_stage_that_was_never_opened_does_not_block_the_submit(
    db_session_factory: Factory,
) -> None:
    api, logger, stages = _api(db_session_factory)
    last = api.start_stage(stages[2]["id"])["session_id"]
    assert api.submit_session(last, {"main.py": "x"}) == {"ok": True}
    logger.stop()
