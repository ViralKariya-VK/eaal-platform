"""Shared plumbing for the signal-validation suite.

Every synthetic scenario in this suite is driven through the *real*
``CavyApi`` bridge — the same code path the actual frontend calls — against
a throwaway SQLite database. Nothing here reimplements event-logging or
signal-computation logic; the point of validation is to exercise the
production code, not a parallel model of it.

This is deliberately a separate tree from ``tests/``: ``tests/`` asserts
"the code behaves as coded" (unit/regression tests that gate every commit).
``validation/`` asks a different question — "does what the code computes
actually correspond to the EAAL construct it claims to measure?" — using
larger synthetic corpora, descriptive statistics, and a formal written
report, not pass/fail assertions.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose
from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    seed_demo_content,
)
from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db
from eaal_platform.events.logger import EventLogger
from eaal_platform.signals.compute import SignalResult, compute_all_signals

VALIDATION_DIR = Path(__file__).resolve().parent
DATA_DIR = VALIDATION_DIR / "data"
FIGURES_DIR = VALIDATION_DIR / "figures"
_DB_PATH = DATA_DIR / "_scratch_validation.db"

_PASSWORD = "validation-pw"


class ScriptedProvider(AIProvider):
    """A fully controllable fake ``AIProvider`` for deterministic scenarios.

    Unlike the app's real ``UnavailableProvider``/``OllamaProvider``, every
    call is driven by a caller-supplied ``responder`` function so a
    scenario can dictate exactly what the "AI" says or scores, without
    depending on a live model's variance — that variance is measured
    separately, deliberately, in ``run_llm_reliability.py``.
    """

    def __init__(
        self, responder: Callable[[str, GenerationContext, Purpose], GenerationResult]
    ) -> None:
        self._responder = responder
        self.calls: list[tuple[str, GenerationContext, Purpose]] = []

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        self.calls.append((prompt, context, purpose))
        return self._responder(prompt, context, purpose)

    def ping(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        return "scripted-validation"

    @property
    def model_name(self) -> str | None:
        return None


def fixed_reply_provider(
    reply: str = "Try breaking the problem into smaller steps.",
) -> ScriptedProvider:
    """A provider whose chat reply never changes and whose rubric calls
    always fail (``available=False``) — for scenarios where the specific
    AI wording doesn't matter, only that *some* interaction happened."""

    def _respond(prompt: str, context: GenerationContext, purpose: Purpose) -> GenerationResult:
        if purpose is Purpose.RUBRIC_SCORING:
            return GenerationResult(text="", available=False, error="not scripted for this test")
        return GenerationResult(text=reply, available=True)

    return ScriptedProvider(_respond)


def code_reply_provider(code: str) -> ScriptedProvider:
    """A provider whose chat reply is a fenced code block — for scenarios
    that need ``_extract_code_blocks`` to find AI-suggested code."""

    def _respond(prompt: str, context: GenerationContext, purpose: Purpose) -> GenerationResult:
        if purpose is Purpose.RUBRIC_SCORING:
            return GenerationResult(text="", available=False, error="not scripted for this test")
        return GenerationResult(text=f"```python\n{code}\n```", available=True)

    return ScriptedProvider(_respond)


def rubric_score_provider(scores: dict[str, float]) -> ScriptedProvider:
    """A provider whose *rubric* calls always return the given scores
    verbatim (as JSON) — used to validate the rubric plumbing (parsing,
    clamping, evidence) independent of what a real model would say."""

    def _respond(prompt: str, context: GenerationContext, purpose: Purpose) -> GenerationResult:
        if purpose is Purpose.RUBRIC_SCORING:
            return GenerationResult(text=json.dumps(scores), available=True)
        return GenerationResult(text="(not used)", available=True)

    return ScriptedProvider(_respond)


def fresh_session_factory() -> sessionmaker[OrmSession]:
    """A brand-new, empty SQLite database for one run of the suite.

    File-based (not ``:memory:``) so multiple ``CavyApi``/engine
    connections within one script share the same data — mirrors how the
    real app's single file-based DB works.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _DB_PATH.exists():
        _DB_PATH.unlink()
    engine = create_db_engine(_DB_PATH)
    init_db(engine)
    session_factory = create_session_factory(engine)
    seed_demo_content(session_factory)  # provides the "Practice" task start_practice() needs
    return session_factory


def cleanup_scratch_db() -> None:
    if _DB_PATH.exists():
        _DB_PATH.unlink()
    for suffix in ("-wal", "-shm"):
        p = _DB_PATH.with_name(_DB_PATH.name + suffix)
        if p.exists():
            p.unlink()


def make_api(
    session_factory: sessionmaker[OrmSession], provider: AIProvider | None = None
) -> CavyApi:
    logger = EventLogger(session_factory, batch_size=1, flush_interval=0.02)
    logger.start()
    return CavyApi(session_factory, logger, ai_provider=provider)


_student_counter = 0
_professor_counter = 0


def new_student(session_factory: sessionmaker[OrmSession], api: CavyApi) -> str:
    """Create-and-log-in a fresh student account (unique per call). Returns
    the email so the caller can ``login_as`` it again later (e.g. to
    switch back after a professor operation on the same ``CavyApi``)."""
    global _student_counter
    _student_counter += 1
    email = f"validation.student.{_student_counter}@example.com"
    create_student_account(
        session_factory,
        display_name=f"Validation Student {_student_counter}",
        email=email,
        password=_PASSWORD,
    )
    result = api.login("student", email, _PASSWORD)
    assert result["ok"], result
    return email


def new_professor(session_factory: sessionmaker[OrmSession], api: CavyApi) -> str:
    global _professor_counter
    _professor_counter += 1
    email = f"validation.professor.{_professor_counter}@example.com"
    create_professor_account(
        session_factory,
        display_name=f"Validation Professor {_professor_counter}",
        email=email,
        password=_PASSWORD,
    )
    result = api.login("professor", email, _PASSWORD)
    assert result["ok"], result
    return email


def login_as_student(api: CavyApi, email: str) -> None:
    result = api.login("student", email, _PASSWORD)
    assert result["ok"], result


def login_as_professor(api: CavyApi, email: str) -> None:
    result = api.login("professor", email, _PASSWORD)
    assert result["ok"], result


def compute(
    session_factory: sessionmaker[OrmSession], session_id: int, provider: AIProvider | None
) -> dict[str, SignalResult]:
    return compute_all_signals(session_factory, session_id, provider)


def reset_output_dirs() -> None:
    for d in (DATA_DIR, FIGURES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def clear_figures() -> None:
    if FIGURES_DIR.exists():
        shutil.rmtree(FIGURES_DIR)
    FIGURES_DIR.mkdir(parents=True)


def save_json(name: str, payload: Any) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
