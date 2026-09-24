"""Synthetic scenarios for Pillar 2 (Cognitive Engagement): S2.1-S2.4."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from validation.harness import fixed_reply_provider, make_api, new_student


@dataclass
class Trial:
    signal: str
    scenario: str
    design: float | str | None
    session_id: int
    provider: Any
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def build_independent_initiation_grid(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    """S2.1 known-answer: ``0.5*min(edits/3,1) + 0.5*min(runs/2,1)``, all
    activity happening before the (one, final) AI prompt.

    This weighted formula only applies to sessions that *do* eventually
    call the AI — ``_signal_independent_initiation`` has a separate branch
    for a session with no AI interaction at all (any edit there grants
    full credit outright, since the whole session was independent), which
    is covered separately in ``build_independent_initiation_no_ai_used``.
    A session must end with an AI prompt for this weighted-formula branch
    to be the one under test; sweep edits 0..4 and runs 0..3 to also
    confirm the cap at 3 edits / 2 runs.
    """
    trials = []
    for edits in range(0, 5):
        for runs in range(0, 4):
            if edits == 0 and runs == 0:
                continue  # covered separately as the "no activity" None case
            provider = fixed_reply_provider()
            api = make_api(session_factory, provider)
            new_student(session_factory, api)
            session_id = api.start_practice()["session_id"]
            for i in range(edits):
                api.log_code_edit(session_id, {"main.py": f"x = {i}"})
            for i in range(runs):
                api.run_code(session_id, {"main.py": f"x = {i}"})
            # Land in the weighted-formula branch: the session must
            # contain at least one AI prompt, after all the pre-AI
            # activity above.
            api.send_ai_message(session_id, "a bit stuck now, any tips?", {"main.py": "x = 0"})
            expected = 0.5 * min(edits / 3, 1.0) + 0.5 * min(runs / 2, 1.0)
            trials.append(
                Trial(
                    "S2.1",
                    f"edits={edits}_runs={runs}",
                    expected,
                    session_id,
                    provider,
                    notes="expected value is the known closed-form answer",
                )
            )
    return trials


def build_independent_initiation_no_ai_used(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    """S2.1's other branch: a session that never calls the AI at all. Any
    edit grants full credit (1.0) regardless of run count; zero edits (even
    with runs) has no evidence to judge independence from, so ``None``."""
    trials = []
    for label, edits, runs, expected in (
        ("no_ai_one_edit_no_runs", 1, 0, 1.0),
        ("no_ai_one_edit_many_runs", 1, 3, 1.0),
        ("no_ai_runs_but_no_edits", 0, 2, None),
    ):
        provider = fixed_reply_provider()
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        for i in range(edits):
            api.log_code_edit(session_id, {"main.py": f"x = {i}"})
        for i in range(runs):
            api.run_code(session_id, {"main.py": f"x = {i}"})
        trials.append(
            Trial(
                "S2.1",
                label,
                expected,
                session_id,
                provider,
                notes="expected value is the known closed-form answer (no-AI branch)",
            )
        )
    return trials


def build_independent_initiation_no_activity(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    session_id = api.start_practice()["session_id"]
    return [Trial("S2.1", "no_activity_at_all", None, session_id, provider)]


def build_reasoning_continuity_series(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    """S2.2 known-answer: ``continued_after / len(windows)`` across N
    interactions where a controlled number are followed by any activity."""
    trials = []
    for n_interactions, n_continued in ((1, 0), (1, 1), (3, 0), (3, 1), (3, 2), (3, 3)):
        provider = fixed_reply_provider()
        api = make_api(session_factory, provider)
        new_student(session_factory, api)
        session_id = api.start_practice()["session_id"]
        for i in range(n_interactions):
            api.send_ai_message(session_id, f"prompt {i}", {"main.py": ""})
            if i < n_continued:
                api.log_code_edit(session_id, {"main.py": f"x = {i}"})
        expected = n_continued / n_interactions
        trials.append(
            Trial(
                "S2.2",
                f"n={n_interactions}_continued={n_continued}",
                expected,
                session_id,
                provider,
                notes="expected value is the known closed-form answer",
            )
        )
    return trials


def build_error_recovery_canonical_cases(
    session_factory: sessionmaker[OrmSession],
) -> list[Trial]:
    """S2.4 known-answer: the four scenarios that distinguish the fixed
    "requires real modification + verification" logic from the old
    (buggy) "didn't immediately ask AI" proxy. See the audit that found
    the original implementation scored the first two cases as 1.0."""
    trials = []

    _note = "expected value is the known closed-form answer"

    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    sid = api.start_practice()["session_id"]
    api.run_code(sid, {"main.py": "raise ValueError('bad')"})
    trials.append(Trial("S2.4", "error_then_nothing", 0.0, sid, provider, notes=_note))

    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    sid = api.start_practice()["session_id"]
    api.run_code(sid, {"main.py": "raise ValueError('bad')"})
    api.run_code(sid, {"main.py": "raise ValueError('bad')"})
    trials.append(Trial("S2.4", "error_then_identical_rerun", 0.0, sid, provider, notes=_note))

    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    sid = api.start_practice()["session_id"]
    api.run_code(sid, {"main.py": "raise ValueError('bad')"})
    api.log_code_edit(sid, {"main.py": "print('fixed but never reran')"})
    trials.append(Trial("S2.4", "error_then_edit_never_verified", 0.0, sid, provider, notes=_note))

    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    sid = api.start_practice()["session_id"]
    api.run_code(sid, {"main.py": "raise ValueError('bad')"})
    api.log_code_edit(sid, {"main.py": "print('fixed')"})
    api.run_code(sid, {"main.py": "print('fixed')"})
    trials.append(Trial("S2.4", "error_then_fix_and_verify", 1.0, sid, provider, notes=_note))

    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    sid = api.start_practice()["session_id"]
    api.run_code(sid, {"main.py": "raise ValueError('one')"})
    api.log_code_edit(sid, {"main.py": "print('fixed one')"})
    api.run_code(sid, {"main.py": "print('fixed one')"})
    api.run_code(sid, {"main.py": "raise ValueError('two')"})
    trials.append(
        Trial("S2.4", "two_errors_one_recovered_one_not", 0.5, sid, provider, notes=_note)
    )

    return trials


def build_error_recovery_no_errors(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    provider = fixed_reply_provider()
    api = make_api(session_factory, provider)
    new_student(session_factory, api)
    sid = api.start_practice()["session_id"]
    api.run_code(sid, {"main.py": "print('all clean')"})
    return [Trial("S2.4", "no_errors_encountered", None, sid, provider)]


def build_all(session_factory: sessionmaker[OrmSession]) -> list[Trial]:
    return [
        *build_independent_initiation_grid(session_factory),
        *build_independent_initiation_no_ai_used(session_factory),
        *build_independent_initiation_no_activity(session_factory),
        *build_reasoning_continuity_series(session_factory),
        *build_error_recovery_canonical_cases(session_factory),
        *build_error_recovery_no_errors(session_factory),
    ]
