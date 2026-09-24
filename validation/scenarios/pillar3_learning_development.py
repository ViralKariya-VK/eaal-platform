"""Synthetic scenarios for Pillar 3 (Learning & Knowledge Development): S3.1-S3.4.

S3.1's rubric plumbing is checked here the same way S1.1/S1.2's is in
Pillar 1 (scripted JSON in, clamped value out) — its actual construct
validity against real explanations is measured in
``run_llm_reliability.py`` against the live model.

S3.3 (Knowledge Transfer) and S3.4 (Retention & Independent Recall) are
the two signals the framework itself flags as needing assessment
infrastructure "beyond simple AI interaction logging" (§6.6) — these
scenarios specifically validate that the *gating* logic (is this session
even eligible to count as Transfer/Retention evidence?) is exactly
right, since that structural correctness is what stands between "S3.3/S3.4
are honest" and "S3.3/S3.4 quietly count the wrong thing."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from validation.harness import (
    fixed_reply_provider,
    login_as_professor,
    login_as_student,
    make_api,
    new_professor,
    new_student,
    rubric_score_provider,
)

from eaal_platform.db.models import Session as SessionModel

_LAB_PAYLOAD_BASE: dict[str, Any] = {
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


@dataclass
class Trial:
    signal: str
    scenario: str
    design: float | str | None
    session_id: int
    provider: Any
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def build_conceptual_understanding_plumbing(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    trials = []
    for label, score in (("low_score", 0.1), ("high_score", 0.95), ("out_of_range", 1.7)):
        provider = rubric_score_provider({"conceptual_understanding": score})
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        api.submit_concept_check(session_id, "Because the recursion has a base case.")
        expected = max(0.0, min(1.0, score))
        trials.append(
            Trial(
                "S3.1",
                label,
                expected,
                session_id,
                provider,
                notes="expected value is the clamped scripted score",
            )
        )
    return trials


def build_conceptual_understanding_no_response(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    session_id = api.start_practice()["session_id"]
    return [Trial("S3.1", "no_concept_check_submitted", None, session_id, provider)]


def build_knowledge_application_grid(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    """S3.2 known-answer: clean exit + actual output is the only case
    scoring 1.0 — specifically re-testing the exact gaming case (a no-op
    ``pass`` program) the audit found scored a false 1.0 under the old
    bare-exit-code check."""
    trials = []
    cases = [
        ("clean_exit_with_output", "print('hello')", 1.0),
        ("no_op_pass_program", "pass", 0.0),
        ("uncaught_exception", "raise ValueError('boom')", 0.0),
        ("clean_exit_no_output_at_all", "x = 1 + 1", 0.0),
    ]
    for label, code, expected in cases:
        provider = fixed_reply_provider()
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        api.run_code(session_id, {"main.py": code})
        trials.append(
            Trial(
                "S3.2",
                label,
                expected,
                session_id,
                provider,
                notes="expected value is the known closed-form answer",
            )
        )
    return trials


def build_knowledge_application_no_execution(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    session_id = api.start_practice()["session_id"]
    return [Trial("S3.2", "no_code_executed", None, session_id, provider)]


def _new_lab(session_factory: sessionmaker[OrmSession]) -> tuple[Any, Any, str, str, int]:
    """One professor authors one Lab. Returns
    ``(api, provider, professor_email, student_email, source_task_id)`` —
    a single ``CavyApi`` shared by both roles (student created but not
    logged in yet), matching how one app window holds exactly one login
    at a time (see ``bridge.py``'s ``login`` docstring)."""
    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    professor_email = new_professor(session_factory, api)
    source_task_id = api.create_lab(_LAB_PAYLOAD_BASE)["task_id"]
    student_email = new_student(session_factory, api)
    return api, provider, professor_email, student_email, source_task_id


def build_knowledge_transfer_gating(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    trials = []

    # Not a Transfer task at all -> must be None ("not applicable"), never
    # a fabricated 0 — an ordinary Lab's Assessment stage carries no
    # Transfer evidence whatsoever.
    api, provider, _prof_email, _stud_email, source_task_id = _new_lab(session_factory)
    stages = api.get_stages(source_task_id)["stages"]
    ordinary_session_id = api.start_stage(stages[2]["id"])["session_id"]
    api.run_code(ordinary_session_id, {"main.py": "print('ok')"})
    trials.append(
        Trial("S3.3", "ordinary_lab_session_not_applicable", None, ordinary_session_id, provider)
    )

    # A real Transfer task, clean substantive run -> 1.0 (same "clean run"
    # proxy as S3.2, just scoped to a Transfer-flagged task).
    api, provider, prof_email, stud_email, source_task_id = _new_lab(session_factory)
    login_as_professor(api, prof_email)
    transfer_task_id = api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "TRANSFER",
            "title": "Transfer Task",
            "description": "A different problem, same concept.",
            "ai_assistance_mode": "RESTRICTED",
            "duration_minutes": 15,
        }
    )["task_id"]
    login_as_student(api, stud_email)
    transfer_stage_id = api.get_followup_assessments(source_task_id)[0]["stage_id"]
    transfer_session_id = api.start_stage(transfer_stage_id)["session_id"]
    api.run_code(transfer_session_id, {"main.py": "print('transfer solved')"})
    trials.append(Trial("S3.3", "transfer_task_clean_run", 1.0, transfer_session_id, provider))
    _ = transfer_task_id

    # A real Transfer task, but the run fails -> 0.0, not None (evidence
    # exists, it's just negative evidence).
    api, provider, prof_email, stud_email, source_task_id = _new_lab(session_factory)
    login_as_professor(api, prof_email)
    api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "TRANSFER",
            "title": "Transfer Task",
            "description": "A different problem, same concept.",
            "ai_assistance_mode": "RESTRICTED",
            "duration_minutes": 15,
        }
    )
    login_as_student(api, stud_email)
    transfer_stage_id = api.get_followup_assessments(source_task_id)[0]["stage_id"]
    transfer_session_id = api.start_stage(transfer_stage_id)["session_id"]
    api.run_code(transfer_session_id, {"main.py": "raise ValueError('nope')"})
    trials.append(Trial("S3.3", "transfer_task_failed_run", 0.0, transfer_session_id, provider))

    return trials


def build_retention_recall_gating(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    trials = []

    # Not a Retention task -> None.
    api, provider, _prof_email, _stud_email, source_task_id = _new_lab(session_factory)
    stages = api.get_stages(source_task_id)["stages"]
    ordinary_session_id = api.start_stage(stages[2]["id"])["session_id"]
    api.run_code(ordinary_session_id, {"main.py": "print('ok')"})
    trials.append(
        Trial("S3.4", "ordinary_lab_session_not_applicable", None, ordinary_session_id, provider)
    )

    def _retention_setup() -> tuple[Any, Any, int, int]:
        """Returns (api, provider, source_task_id, original_session_id) with
        a Retention Check already spun off and the student logged back in,
        ready to ``start_stage`` the retention stage."""
        api, provider, prof_email, stud_email, source_task_id = _new_lab(session_factory)
        login_as_student(api, stud_email)
        original_stages = api.get_stages(source_task_id)["stages"]
        original_session_id = api.start_stage(original_stages[2]["id"])["session_id"]
        login_as_professor(api, prof_email)
        api.create_followup_assessment(
            {
                "source_task_id": source_task_id,
                "kind": "RETENTION",
                "title": "Retention Check",
                "description": "Re-implement it now, without AI help.",
                "ai_assistance_mode": "NONE",
                "duration_minutes": 15,
            }
        )
        login_as_student(api, stud_email)
        return api, provider, source_task_id, original_session_id

    # Attempted immediately (0h delay) -> None, with a reason naming the
    # 24h minimum — the exact "gaming" case a naive "different task = auto
    # credit" implementation would miss entirely.
    api, provider, source_task_id, _original_session_id = _retention_setup()
    retention_stage_id = api.get_followup_assessments(source_task_id)[0]["stage_id"]
    retention_session_id = api.start_stage(retention_stage_id)["session_id"]
    api.run_code(retention_session_id, {"main.py": "print('too soon')"})
    trials.append(
        Trial(
            "S3.4",
            "attempted_immediately_zero_delay",
            None,
            retention_session_id,
            provider,
            notes="expects None with a reason naming the 24h minimum delay",
        )
    )

    # Attempted after a genuine (backdated) delay, clean run -> 1.0. No
    # real test can wait 24h, so the *original* session's ``started_at``
    # is backdated instead of the retention session's — the measured
    # delay is exactly what a real 24h-later attempt would produce.
    api, provider, source_task_id, original_session_id = _retention_setup()
    with session_factory() as db_session:
        original_session = db_session.get(SessionModel, original_session_id)
        assert original_session is not None
        original_session.started_at = datetime.now(UTC) - timedelta(hours=30)
        db_session.commit()
    retention_stage_id = api.get_followup_assessments(source_task_id)[0]["stage_id"]
    retention_session_id = api.start_stage(retention_stage_id)["session_id"]
    api.run_code(retention_session_id, {"main.py": "print('recalled it')"})
    trials.append(
        Trial(
            "S3.4",
            "attempted_after_30h_delay_clean_run",
            1.0,
            retention_session_id,
            provider,
            notes="30h backdated delay, well past the 24h minimum",
        )
    )

    # Just under the boundary (23h) -> still None; precisely exercises the
    # threshold rather than an obviously-short or obviously-long gap.
    api, provider, source_task_id, original_session_id = _retention_setup()
    with session_factory() as db_session:
        original_session = db_session.get(SessionModel, original_session_id)
        assert original_session is not None
        original_session.started_at = datetime.now(UTC) - timedelta(hours=23)
        db_session.commit()
    retention_stage_id = api.get_followup_assessments(source_task_id)[0]["stage_id"]
    retention_session_id = api.start_stage(retention_stage_id)["session_id"]
    api.run_code(retention_session_id, {"main.py": "print('almost')"})
    trials.append(
        Trial(
            "S3.4",
            "attempted_at_23h_just_under_threshold",
            None,
            retention_session_id,
            provider,
            notes="23h backdated delay, 1h short of the 24h minimum",
        )
    )

    return trials


def build_all(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    return [
        *build_conceptual_understanding_plumbing(session_factory),
        *build_conceptual_understanding_no_response(session_factory),
        *build_knowledge_application_grid(session_factory),
        *build_knowledge_application_no_execution(session_factory),
        *build_knowledge_transfer_gating(session_factory),
        *build_retention_recall_gating(session_factory),
    ]
