"""Tests for CavyApi: the Python side of the JS/Python bridge.

No webview involved — every method here is a plain Python call, tested the
same way the engine underneath it always was.
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose
from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import create_student_account, seed_demo_content
from eaal_platform.db.models import (
    AIInteraction,
    Event,
    EventType,
    SignalScore,
)
from eaal_platform.db.models import (
    Session as SessionModel,
)
from eaal_platform.events.logger import EventLogger


class _FakeProvider(AIProvider):
    def __init__(self, *, available: bool = True, reply: str = "sure, here's how") -> None:
        self._available = available
        self._reply = reply
        self.received_contexts: list[GenerationContext] = []

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        self.received_contexts.append(context)
        if self._available:
            return GenerationResult(text=self._reply, available=True)
        return GenerationResult(text="", available=False, error="unreachable")

    def ping(self) -> bool:
        return self._available

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str | None:
        return "fake-model"


def _make_api(
    db_session_factory: sessionmaker[OrmSession], provider: AIProvider | None = None
) -> tuple[CavyApi, EventLogger]:
    seed_demo_content(db_session_factory)
    create_student_account(
        db_session_factory,
        display_name="Local Student",
        email="student@example.com",
        password="hunter2",
    )
    logger = EventLogger(db_session_factory, batch_size=1, flush_interval=0.1)
    logger.start()
    api = CavyApi(db_session_factory, logger, ai_provider=provider)
    login_result = api.login("student", "student@example.com", "hunter2")
    assert login_result["ok"] is True
    return api, logger


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _files(code: str) -> dict[str, str]:
    return {"main.py": code}


def test_get_student_name(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    assert api.get_student_name() == "Local Student"
    logger.stop()


def test_get_labs_excludes_practice(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    labs = api.get_labs()
    assert all(lab["title"] != "Practice" for lab in labs)
    assert len(labs) >= 1
    assert labs[0]["stage_count"] == 3
    logger.stop()


def test_get_stages_reports_lock_state(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    labs = api.get_labs()
    result = api.get_stages(labs[0]["id"])
    assert len(result["stages"]) == 3
    assert result["stages"][0]["unlocked"] is True
    assert result["stages"][1]["unlocked"] is False
    assert result["stages"][2]["unlocked"] is False
    logger.stop()


def test_start_practice_returns_expected_shape(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    result = api.start_practice()
    assert result["task_title"] == "Practice"
    assert result["is_stage"] is False
    assert result["ai_assistance_mode"] == "FULL"
    assert isinstance(result["session_id"], int)
    logger.stop()


def test_start_stage_returns_stage_ai_mode(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    labs = api.get_labs()
    stages = api.get_stages(labs[0]["id"])["stages"]
    result = api.start_stage(stages[2]["id"])  # assessment stage
    assert result["ai_assistance_mode"] == "RESTRICTED"
    assert result["is_stage"] is True
    logger.stop()


def test_start_practice_logs_task_start(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    result = api.start_practice()
    assert _wait_until(
        lambda: bool(_events(db_session_factory, result["session_id"], EventType.TASK_START))
    )
    logger.stop()


def test_log_code_edit_saves_snapshot_and_event(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    result = api.log_code_edit(session_id, _files("x = 1"))
    assert isinstance(result["snapshot_id"], int)
    assert _wait_until(lambda: bool(_events(db_session_factory, session_id, EventType.CODE_EDIT)))
    logger.stop()


def test_log_code_edit_reset_flag_in_payload(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.log_code_edit(session_id, _files(""), reset=True)
    assert _wait_until(
        lambda: any(
            e.payload_json and e.payload_json.get("reset")
            for e in _events(db_session_factory, session_id, EventType.CODE_EDIT)
        )
    )
    logger.stop()


def test_run_code_executes_and_persists_result(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    outcome = api.run_code(session_id, _files("print('hello from bridge')"))
    assert outcome["exit_status"] == 0
    assert outcome["entry_filename"] == "main.py"
    assert "hello from bridge" in outcome["stdout"]
    assert _wait_until(
        lambda: bool(_events(db_session_factory, session_id, EventType.EXECUTION_RESULT))
    )
    logger.stop()


def test_run_code_can_run_a_non_main_file_as_the_entry_point(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    files = {"main.py": "", "helper.py": "print('ran helper directly')"}
    outcome = api.run_code(session_id, files, "helper.py")
    assert outcome["entry_filename"] == "helper.py"
    assert outcome["exit_status"] == 0
    assert "ran helper directly" in outcome["stdout"]
    logger.stop()


def test_submit_session_marks_submitted_and_logs(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    result = api.submit_session(session_id, _files("print(1)"))
    assert result["ok"] is True

    with db_session_factory() as db_session:
        session = db_session.get(SessionModel, session_id)
        assert session is not None
        assert session.submitted_at is not None

    assert _wait_until(lambda: bool(_events(db_session_factory, session_id, EventType.SUBMISSION)))
    logger.stop()


def test_send_ai_message_persists_interaction(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _FakeProvider(reply="use a for-loop")
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    result = api.send_ai_message(session_id, "how do I loop?", _files("def f(): pass"))
    assert result["available"] is True
    assert result["text"] == "use a for-loop"

    with db_session_factory() as db_session:
        interactions = db_session.query(AIInteraction).filter_by(session_id=session_id).all()
    assert len(interactions) == 1
    assert interactions[0].prompt == "how do I loop?"
    assert interactions[0].response == "use a for-loop"
    logger.stop()


def test_send_ai_message_includes_current_code_in_context(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _FakeProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "is this right?", _files("def add(a, b): return a - b"))
    assert "def add(a, b): return a - b" in provider.received_contexts[0].current_code
    logger.stop()


def test_send_ai_message_includes_last_run_error(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _FakeProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.run_code(session_id, _files("raise ValueError('bad')"))
    api.send_ai_message(session_id, "why does this fail?", _files("raise ValueError('bad')"))

    assert provider.received_contexts[0].recent_stderr is not None
    assert "ValueError" in provider.received_contexts[0].recent_stderr
    logger.stop()


def test_send_ai_message_blocked_during_restricted_stage(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _FakeProvider()
    api, logger = _make_api(db_session_factory, provider)
    labs = api.get_labs()
    stages = api.get_stages(labs[0]["id"])["stages"]
    session_id = api.start_stage(stages[2]["id"])["session_id"]  # ASSESSMENT = RESTRICTED

    result = api.send_ai_message(session_id, "give me the answer", _files("code"))

    assert result["available"] is False
    assert provider.received_contexts == []  # provider never contacted
    with db_session_factory() as db_session:
        assert db_session.query(AIInteraction).filter_by(session_id=session_id).count() == 0
    logger.stop()


def test_ping_ai_reflects_provider_availability(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory, _FakeProvider(available=False))
    assert api.ping_ai() is False
    logger.stop()


def test_get_submission_summary(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.run_code(session_id, _files("print(1)"))
    api.submit_session(session_id, _files("print(1)"))

    summary = api.get_submission_summary(session_id)
    assert summary["submitted_at"] is not None
    assert summary["exit_status"] == "Exit code 0"
    logger.stop()


def test_get_ciq_score_evidence_counts(db_session_factory: sessionmaker[OrmSession]) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.log_code_edit(session_id, _files("x = 1"))

    score = api.get_ciq_score(session_id)
    assert score["evidence"]["snapshot_count"] >= 1
    assert len(score["pillars"]) == 3
    # Signals that require AI interaction (this session used none) stay
    # pending with a reason; S3.3/S3.4 always do (no infra for them yet).
    all_signals = {s["key"]: s for pillar in score["pillars"] for s in pillar["signals"]}
    assert all_signals["S1.3"]["value"] is None
    assert all_signals["S1.3"]["reason"]
    assert all_signals["S3.3"]["value"] is None
    assert all_signals["S3.4"]["value"] is None
    logger.stop()


def test_get_ciq_score_reflects_computed_signal(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    # Coding activity with no AI interaction at all is, by definition, fully
    # independent initiation (S2.1) — a real, deterministic computation
    # rather than a pre-seeded row.
    api.log_code_edit(session_id, _files("x = 1"))
    api.run_code(session_id, _files("x = 1"))

    score = api.get_ciq_score(session_id)
    p2 = next(p for p in score["pillars"] if p["heading"].startswith("P2"))
    s21 = next(s for s in p2["signals"] if s["key"] == "S2.1")
    assert s21["value"] == 1.0

    with db_session_factory() as db_session:
        persisted = db_session.query(SignalScore).filter_by(session_id=session_id).all()
    assert any(row.signal_key == "S2.1" and row.value == 1.0 for row in persisted)
    logger.stop()


def _events(
    session_factory: sessionmaker[OrmSession], session_id: int, event_type: EventType
) -> list[Event]:
    with session_factory() as db_session:
        return db_session.query(Event).filter_by(session_id=session_id, event_type=event_type).all()
