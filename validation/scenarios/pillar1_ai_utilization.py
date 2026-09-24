"""Synthetic scenarios for Pillar 1 (AI Utilization): S1.1-S1.6.

Each ``build_*`` function drives the real ``CavyApi`` through a specific,
controlled event sequence and returns a list of "trial" records:
``{"signal", "scenario", "design": <the independent variable we set>,
"session_id", "provider"}``. ``run_synthetic_suite.py`` then computes all
signals for every trial and joins the result back onto ``design`` for
analysis — nothing here computes a signal value itself; that stays the
production code's job.

S1.1/S1.2 (LLM-rubric signals) get only a "does the plumbing work"
check here (parsing/clamping fixed JSON) — their actual construct
validity (does the model's *judgment* track a human's) is measured
separately in ``run_llm_reliability.py`` against the real local model,
since a scripted provider can't test whether the model's semantic
judgment is any good.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from validation.harness import (
    code_reply_provider,
    fixed_reply_provider,
    make_api,
    new_student,
    rubric_score_provider,
)

from eaal_platform.api.bridge import _bundle_files

_AI_CODE = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, n in enumerate(nums):\n"
    "        if target - n in seen:\n"
    "            return [seen[target - n], i]\n"
    "        seen[n] = i\n"
)

_UNRELATED_FILLER = "\n".join(str(i) for i in range(200)) + "\n"


@dataclass
class Trial:
    signal: str
    scenario: str
    design: float | str | None
    session_id: int
    provider: Any
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _measured_similarity(ai_code: str, edited_files: dict[str, str]) -> float:
    """The same textual-similarity metric ``S1.3``/``S2.3`` use internally
    (``difflib.SequenceMatcher`` over the AI's code vs. the bundled files),
    computed independently here so we have a precise, continuous
    ground-truth x-axis for the monotonicity analysis rather than a guess."""
    return difflib.SequenceMatcher(None, ai_code, _bundle_files(edited_files)).ratio()


def _corrupted_variant(base: str, filler: str, fraction: int) -> str:
    """``fraction`` is 0..10 tenths of ``base`` replaced by ``filler`` lines,
    producing a smooth, monotonic similarity gradient from ~1.0 to ~0.0."""
    base_lines = base.splitlines()
    n_replace = round(len(base_lines) * fraction / 10)
    filler_lines = (filler.splitlines() * ((n_replace // max(len(filler.splitlines()), 1)) + 1))[
        :n_replace
    ]
    kept = base_lines[: len(base_lines) - n_replace]
    return "\n".join(kept + filler_lines) + "\n"


def build_response_utilization_similarity_series(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    """S1.3 + S2.3: a smooth gradient from a verbatim-adopted AI answer to a
    completely unrelated follow-up edit. S1.3 (utilization) should step from
    1 to 0 near the 0.15 similarity threshold; S2.3 (agency) should rise
    ~linearly as similarity falls (agency = 1 - similarity, deterministic).
    """
    trials: list[Trial] = []
    for fraction in range(0, 11):
        provider = code_reply_provider(_AI_CODE)
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        api.send_ai_message(session_id, "how do I solve two-sum?", {"main.py": ""})
        edited = {"main.py": _corrupted_variant(_AI_CODE, _UNRELATED_FILLER, fraction)}
        api.log_code_edit(session_id, edited)
        measured_sim = _measured_similarity(_AI_CODE, edited)
        trials.append(
            Trial(
                "S1.3",
                "response_utilization_similarity_gradient",
                measured_sim,
                session_id,
                provider,
                notes=f"fraction_corrupted={fraction}/10",
            )
        )
        trials.append(
            Trial(
                "S2.3",
                "problem_solving_agency_similarity_gradient",
                measured_sim,
                session_id,
                provider,
                notes=f"fraction_corrupted={fraction}/10",
            )
        )
    return trials


def build_response_utilization_no_followup(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    """S1.3 known-answer: an AI reply with zero follow-up activity must
    score exactly 0.0 (not None — an interaction did happen, it just went
    unused)."""
    provider = code_reply_provider(_AI_CODE)
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    session_id = api.start_practice()["session_id"]
    api.send_ai_message(session_id, "give me the code", {"main.py": ""})
    return [Trial("S1.3", "no_followup_activity", 0.0, session_id, provider)]


def build_modification_verification_grid(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    """S1.4 known-answer: the formula is explicitly
    ``0.5*changed + 0.5*verified`` — a 2x2 grid over
    {changed, unchanged} x {verified (re-run), unverified} should produce
    exactly {1.0, 0.5, 0.5, 0.0}.

    The prompt is sent with the file empty (``""``) so "unchanged" can be
    tested precisely: a follow-up edit that also saves ``""`` is a real
    ``CODE_EDIT`` event (needed for "verified" to even be assessed — see
    ``_analyze_interaction``) but is not a *change* from the pre-prompt
    snapshot, exercising the "verified without changing" cell that a
    content-bearing edit could not isolate.
    """
    trials = []
    grid = [
        ("changed_and_verified", True, True, 1.0),
        ("changed_only", True, False, 0.5),
        ("verified_only_no_change", False, True, 0.5),
        ("neither", False, False, 0.0),
    ]
    for name, changed, verified, expected in grid:
        provider = code_reply_provider(_AI_CODE)
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        api.send_ai_message(session_id, "give me the code", {"main.py": ""})
        edit_content = _AI_CODE if changed else ""
        if changed or verified:
            # Even the "verified only" cell needs a real CODE_EDIT event
            # (a no-op save of the same content) for `_analyze_interaction`
            # to look for a subsequent run at all.
            api.log_code_edit(session_id, {"main.py": edit_content})
        if verified:
            api.run_code(session_id, {"main.py": edit_content})
        trials.append(
            Trial(
                "S1.4",
                name,
                expected,
                session_id,
                provider,
                notes="expected value is the known closed-form answer",
            )
        )
    return trials


def build_followup_engagement_series(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    """S1.5 known-answer: with N AI interactions, the score is exactly
    ``followups / (N - 1)`` where a "followup" is trying something (edit or
    run) between two consecutive prompts. Test N=3 with 0, 1, and 2 of the
    2 possible gaps filled."""
    trials = []
    for n_followups in (0, 1, 2):
        provider = fixed_reply_provider()
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        api.send_ai_message(session_id, "prompt 1", {"main.py": ""})
        if n_followups >= 1:
            api.log_code_edit(session_id, {"main.py": "x = 1"})
        api.send_ai_message(session_id, "prompt 2", {"main.py": "x = 1"})
        if n_followups >= 2:
            api.log_code_edit(session_id, {"main.py": "x = 2"})
        api.send_ai_message(session_id, "prompt 3", {"main.py": "x = 2"})
        expected = n_followups / 2
        trials.append(
            Trial(
                "S1.5",
                f"followups_{n_followups}_of_2",
                expected,
                session_id,
                provider,
                notes="expected value is the known closed-form answer",
            )
        )
    return trials


def build_adaptive_ai_use_history(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    """S1.6 known-answer: score = clamp(0.5 + (current_ratio - historical_average)).
    Build one student with 2 prior sessions at verification ratio 0.0 (never
    changed/verified) and then a current session at ratio 1.0 (changed AND
    verified) -> expected clamp(0.5 + (1.0 - 0.0)) = 1.0. A second student
    with prior ratio 1.0 and current ratio 0.0 -> expected clamp(0.5 + (0.0 - 1.0)) = 0.0.
    """
    trials = []
    for label, prior_ratio, current_ratio, expected in (
        ("improved_vs_history", 0.0, 1.0, 1.0),
        ("regressed_vs_history", 1.0, 0.0, 0.0),
    ):
        provider = code_reply_provider(_AI_CODE)
        api = make_api(session_factory, provider)
        new_student(session_factory, api)

        def _one_session(ratio: float, api: Any = api) -> int:
            sid = api.start_practice()["session_id"]
            api.send_ai_message(sid, "help", {"main.py": ""})
            if ratio > 0:
                api.log_code_edit(sid, {"main.py": _AI_CODE + "\n# changed\n"})
                api.run_code(sid, {"main.py": _AI_CODE + "\n# changed\n"})
            else:
                pass  # no edit, no run -> ratio 0.0
            return sid

        _one_session(prior_ratio)
        _one_session(prior_ratio)
        current_session_id = _one_session(current_ratio)
        trials.append(
            Trial(
                "S1.6",
                label,
                expected,
                current_session_id,
                provider,
                notes="expected value is the known closed-form answer",
            )
        )
    return trials


def build_adaptive_ai_use_insufficient_history(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    session_id = api.start_practice()["session_id"]
    api.send_ai_message(session_id, "help", {"main.py": ""})
    return [Trial("S1.6", "no_prior_sessions", None, session_id, provider)]


def build_llm_rubric_plumbing(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    """S1.1/S1.2: with a scripted provider, confirm the rubric plumbing
    (JSON parse, clamping to [0,1], evidence recorded) is correct — NOT a
    test of the model's judgment (see ``run_llm_reliability.py`` for that).
    """
    trials = []
    for label, scores in (
        ("mid_range_scores", {"help_seeking_calibration": 0.65, "student_grounding": 0.4}),
        ("clamped_above_1", {"help_seeking_calibration": 1.4, "student_grounding": -0.3}),
    ):
        provider = rubric_score_provider(scores)
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        api.send_ai_message(session_id, "I tried X and got error Y, what next?", {"main.py": "x=1"})
        expected_calibration = max(0.0, min(1.0, scores["help_seeking_calibration"]))
        expected_grounding = max(0.0, min(1.0, scores["student_grounding"]))
        trials.append(
            Trial(
                "S1.1",
                label,
                expected_calibration,
                session_id,
                provider,
                notes="expected value is the clamped scripted score",
            )
        )
        trials.append(
            Trial(
                "S1.2",
                label,
                expected_grounding,
                session_id,
                provider,
                notes="expected value is the clamped scripted score",
            )
        )
    return trials


def build_all(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    return [
        *build_response_utilization_similarity_series(session_factory),
        *build_response_utilization_no_followup(session_factory),
        *build_modification_verification_grid(session_factory),
        *build_followup_engagement_series(session_factory),
        *build_adaptive_ai_use_history(session_factory),
        *build_adaptive_ai_use_insufficient_history(session_factory),
        *build_llm_rubric_plumbing(session_factory),
    ]
