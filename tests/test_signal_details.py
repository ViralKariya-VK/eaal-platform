"""Exact-value tests for the signal engine's building blocks.

The broader tests in ``test_signals.py`` run whole sessions. These pin down the individual
rules (what each signal returns, the reason it gives when it can't be computed, and the
evidence it reports) so that a changed number, comparison or message is caught; mutation
testing (mutmut) showed those were the places the suite was too forgiving.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session as OrmSession

from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose
from eaal_platform.db.models import EventType
from eaal_platform.signals import compute as c

EDIT, RUN, RESULT = EventType.CODE_EDIT, EventType.CODE_RUN, EventType.EXECUTION_RESULT


def event(kind: EventType, snapshot: int | None = None, **payload: Any) -> Any:
    return SimpleNamespace(event_type=kind, payload_json=payload or None, code_version_id=snapshot)


def evidence(
    events: list[Any],
    prompts: list[Any] | None = None,
    interactions: int = 0,
    snapshots: dict[int, Any] | None = None,
) -> c._SessionEvidence:
    return c._SessionEvidence(
        events=events,
        ai_interactions=[SimpleNamespace(prompt="p") for _ in range(interactions)],  # type: ignore[misc]
        snapshots=snapshots or {},
        ai_prompt_events=prompts or [],
        ai_response_events=[],
    )


def snap(content: str) -> Any:
    return SimpleNamespace(content=content)


# -- S2.1 independent initiation --------------------------------------------------------


def test_initiation_without_any_ai_use() -> None:
    nothing = c._signal_independent_initiation(evidence([]))
    assert (nothing.value, nothing.reason) == (None, "no coding activity recorded yet")
    worked = c._signal_independent_initiation(evidence([event(EDIT), event(EDIT), event(RUN)]))
    assert worked.value == 1.0
    assert worked.evidence == {"edits_before_ai": 2, "runs_before_ai": 1}


def test_initiation_before_the_first_ai_prompt() -> None:
    ask = event(EventType.AI_PROMPT)
    none_first = c._signal_independent_initiation(evidence([ask, event(EDIT)], [ask]))
    assert none_first.value == 0.0
    assert none_first.evidence == {"edits_before_ai": 0, "runs_before_ai": 0}

    one_each = c._signal_independent_initiation(evidence([event(EDIT), event(RUN), ask], [ask]))
    assert one_each.value == pytest.approx(0.5 * (1 / 3) + 0.5 * (1 / 2))
    assert one_each.evidence == {"edits_before_ai": 1, "runs_before_ai": 1}

    plenty = c._signal_independent_initiation(
        evidence([event(EDIT)] * 4 + [event(RUN)] * 3 + [ask], [ask])
    )
    assert plenty.value == 1.0 and plenty.evidence == {"edits_before_ai": 4, "runs_before_ai": 3}

    only_edits = c._signal_independent_initiation(evidence([event(EDIT)] * 3 + [ask], [ask]))
    assert only_edits.value == 0.5 and only_edits.evidence["runs_before_ai"] == 0
    only_runs = c._signal_independent_initiation(evidence([event(RUN)] * 2 + [ask], [ask]))
    assert only_runs.value == 0.5 and only_runs.evidence["edits_before_ai"] == 0


# -- S1.5 follow-up engagement and S2.2 reasoning continuity ------------------------------


def test_followup_engagement() -> None:
    short = c._signal_followup_engagement(evidence([], interactions=1), [[]])
    assert (short.value, short.reason) == (None, "fewer than two AI interactions to compare")

    prompts = [event(EventType.AI_PROMPT), event(EventType.AI_PROMPT, had_recent_error=True)]
    ev = evidence([], prompts=prompts, interactions=2)
    worked = c._signal_followup_engagement(ev, [[event(EDIT)], []])
    assert worked.value == 1.0 and worked.evidence == {"opportunities": 1, "followups": 1}
    # Nothing tried in between, but the second question came right after an error: still counts.
    after_error = c._signal_followup_engagement(ev, [[], []])
    assert after_error.value == 1.0
    plain = evidence([], prompts=[event(EventType.AI_PROMPT)] * 2, interactions=2)
    idle = c._signal_followup_engagement(plain, [[event(EventType.AI_PROMPT)], []])
    assert idle.value == 0.0 and idle.evidence == {"opportunities": 1, "followups": 0}
    # Runs count as trying something, like edits do.
    assert c._signal_followup_engagement(plain, [[event(RUN)], []]).value == 1.0


def test_reasoning_continuity() -> None:
    none = c._signal_reasoning_continuity(evidence([], interactions=0), [])
    assert (none.value, none.reason) == (None, "no AI interactions in this session")
    result = c._signal_reasoning_continuity(
        evidence([], interactions=3), [[event(EDIT)], [], [event(RUN)]]
    )
    assert result.value == pytest.approx(2 / 3)
    assert result.evidence == {"interactions": 3, "continued_after": 2}


# -- S1.x from interaction outcomes ----------------------------------------------------------


def outcome(utilized: bool, verified: bool, agency: float, changed: bool = True) -> Any:
    return c._InteractionOutcome(utilized, changed, verified, agency)


def test_outcome_based_signals() -> None:
    reason = "no AI interactions in this session"
    for fn in (
        c._signal_response_utilization,
        c._signal_modification_verification,
        c._signal_problem_solving_agency,
    ):
        empty = fn([])
        assert (empty.value, empty.reason) == (None, reason)

    outcomes = [outcome(True, True, 0.8), outcome(False, False, 0.4), outcome(True, False, 0.6)]
    assert c._signal_response_utilization(outcomes).value == pytest.approx(2 / 3)
    assert c._signal_response_utilization(outcomes).evidence == {"interactions": 3}
    assert c._signal_problem_solving_agency(outcomes).value == pytest.approx(0.6)
    assert c._signal_problem_solving_agency(outcomes).evidence == {"interactions": 3}
    verified = c._signal_modification_verification(
        [outcome(True, True, 1, changed=True), outcome(True, False, 1, changed=True)]
    )
    assert verified.value == 0.75 and verified.evidence == {"interactions": 2}


# -- errors and recovery -------------------------------------------------------------------


def test_what_counts_as_an_error_result() -> None:
    assert c._is_error_result(event(RESULT, exit_status=1))
    assert c._is_error_result(event(RESULT, exit_status=0, timed_out=True))
    assert c._is_error_result(event(RESULT, exit_status=None))
    assert not c._is_error_result(event(RESULT, exit_status=0))
    assert not c._is_error_result(event(RESULT))  # no payload at all
    assert not c._is_error_result(event(RUN, exit_status=1))  # not a result


def test_the_last_edit_that_really_changed_the_code() -> None:
    ev = evidence([], snapshots={1: snap("a = 1"), 2: snap("a = 1  "), 3: snap("a = 2")})
    window = [event(EDIT, 1), event(RUN), event(EDIT, 3), event(EDIT, 2)]
    assert c._last_modifying_edit_index(ev, window, "a = 1") == 2  # whitespace-only is no change
    assert c._last_modifying_edit_index(ev, [event(RUN)], "a = 1") is None
    assert c._last_modifying_edit_index(ev, [event(EDIT, 2)], "a = 1") is None


def test_recovering_from_an_error_needs_a_change_and_a_clean_run() -> None:
    bad = event(RESULT, 1, exit_status=1)
    fixed = event(EDIT, 2)
    good = event(RESULT, exit_status=0)
    snapshots = {1: snap("broken"), 2: snap("working")}

    def recovered(*events: Any, nxt: Any = None) -> bool:
        return c._recovered_from_error(evidence([bad, *events], snapshots=snapshots), bad, nxt)

    assert recovered(fixed, good)
    assert not recovered(good)  # reran without changing anything
    assert not recovered(fixed)  # changed it but never ran it cleanly
    assert not recovered(good, fixed)  # the clean run came before the change
    again = event(RESULT, exit_status=1)
    assert not recovered(fixed, again, good, nxt=again)  # the window ends at the next error


def test_error_recovery_signal() -> None:
    none = c._signal_error_recovery(evidence([event(RESULT, exit_status=0)]))
    assert (none.value, none.reason) == (None, "no execution errors encountered")

    bad1, bad2 = event(RESULT, 1, exit_status=1), event(RESULT, 1, exit_status=1)
    events = [bad1, event(EDIT, 2), event(RESULT, exit_status=0), bad2]
    result = c._signal_error_recovery(
        evidence(events, snapshots={1: snap("broken"), 2: snap("working")})
    )
    assert result.value == 0.5 and result.evidence == {"errors": 2, "recovered": 1}


# -- the last run (S3.2) ----------------------------------------------------------------------


def test_final_run_quality() -> None:
    assert c._final_run_quality(evidence([event(EDIT)])) is None
    clean = c._final_run_quality(
        evidence([event(RESULT, exit_status=1), event(RESULT, exit_status=0, has_output=True)])
    )
    assert clean == (
        1.0,
        {"final_exit_status": 0, "timed_out": None, "has_output": True},
    )
    for payload in (
        {"exit_status": 0, "has_output": False},  # did nothing
        {"exit_status": 1, "has_output": True},
        {"exit_status": 0, "has_output": True, "timed_out": True},
    ):
        value, shown = c._final_run_quality(evidence([event(RESULT, **payload)])) or (-1, {})
        assert value == 0.0
        assert shown["final_exit_status"] == payload["exit_status"]
    # Only the LAST run counts.
    last = c._final_run_quality(
        evidence([event(RESULT, exit_status=0, has_output=True), event(RESULT, exit_status=2)])
    )
    assert last is not None and last[0] == 0.0

    none = c._signal_knowledge_application(evidence([]))
    assert (none.value, none.reason) == (None, "no code was executed in this session")
    ok = c._signal_knowledge_application(evidence([event(RESULT, exit_status=0, has_output=True)]))
    assert ok.value == 1.0 and ok.evidence["has_output"] is True


# -- the helpers -----------------------------------------------------------------------------


def test_small_helpers() -> None:
    assert (c._clamp01(-0.5), c._clamp01(0.4), c._clamp01(1.7)) == (0.0, 0.4, 1.0)
    assert c._similarity("", "") == 1.0
    assert c._similarity("abc", "abc") == 1.0
    assert c._similarity("abc", "") == 0.0
    assert 0 < c._similarity("abcd", "abxd") < 1
    assert c._extract_code_blocks("x ```py\nprint(1)\n``` y ```\n```") == ["print(1)"]
    assert c._agency_for([], None) == pytest.approx(c._agency_for([], None))  # no crash


def test_concept_score_bands() -> None:
    base = {"conceptual_understanding": 0.9}
    assert c._concept_score_from_model_json(base) == (0.9, False)
    assert c._concept_score_from_model_json({"conceptual_understanding": 7}) == (1.0, False)
    narrates = {**base, "only_narrates_steps": True, "explains_why": False}
    assert c._concept_score_from_model_json(narrates) == (c._NARRATION_ONLY_MAX_SCORE, True)
    # Narrating but also explaining why is not capped.
    assert c._concept_score_from_model_json({**narrates, "explains_why": True}) == (0.9, False)
    wrong = {**base, "states_something_incorrect": True}
    assert c._concept_score_from_model_json(wrong) == (c._FACTUALLY_WRONG_MAX_SCORE, True)
    low = {"conceptual_understanding": 0.05, "states_something_incorrect": True}
    assert c._concept_score_from_model_json(low) == (0.05, False)  # a cap never raises a score
    with pytest.raises(KeyError):
        c._concept_score_from_model_json({})


# -- the AI-scored signals ------------------------------------------------------------------


class Scripted(AIProvider):
    def __init__(self, text: str = "", available: bool = True, reachable: bool = True) -> None:
        self.text, self.available, self.reachable = text, available, reachable
        self.calls: list[tuple[str, GenerationContext, Purpose]] = []

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        self.calls.append((prompt, context, purpose))
        return GenerationResult(self.text, self.available)

    def ping(self) -> bool:
        return self.reachable

    @property
    def provider_name(self) -> str:
        return "scripted"

    @property
    def model_name(self) -> str | None:
        return "m"


def interaction(prompt: str = "how?") -> Any:
    return SimpleNamespace(
        prompt=prompt, session_id=1, timestamp=datetime(2026, 1, 1), code_version_before_id=None
    )


def test_rubric_signals_explain_why_they_are_empty(db_session: OrmSession) -> None:
    ev = evidence([], interactions=0)
    both = c._signal_llm_rubrics(Scripted(), db_session, ev, "task")
    assert both[0].reason == both[1].reason == "no AI interactions in this session"

    ev = c._SessionEvidence([], [interaction()], {}, [], [])
    for provider in (None, Scripted(reachable=False)):
        pair = c._signal_llm_rubrics(provider, db_session, ev, "task")
        assert pair[0].reason == pair[1].reason == "the AI evaluator is not reachable right now"
        assert pair[0].value is None

    for text in ("not json", json.dumps({"help_seeking_calibration": 1}), "[]"):
        failed = c._signal_llm_rubrics(Scripted(text), db_session, ev, "task")
        assert failed[0].reason == "the AI evaluator did not return a usable score"
    unavailable = c._signal_llm_rubrics(Scripted(available=False), db_session, ev, "task")
    assert unavailable[1].reason == "the AI evaluator did not return a usable score"


def test_rubric_signals_average_what_the_model_says(db_session: OrmSession) -> None:
    ev = c._SessionEvidence([], [interaction("one"), interaction("two")], {}, [], [])
    scores = {"help_seeking_calibration": 0.8, "student_grounding": 1.6}  # 1.6 is clamped to 1
    provider = Scripted(json.dumps(scores))
    calibration, grounding = c._signal_llm_rubrics(provider, db_session, ev, "write a loop")
    assert calibration.value == pytest.approx(0.8)
    assert grounding.value == 1.0
    assert calibration.evidence == grounding.evidence == {"rated_interactions": 2}
    assert len(provider.calls) == 2
    prompt, context, purpose = provider.calls[0]
    assert "one" in prompt and purpose is Purpose.RUBRIC_SCORING
    assert context.task_description == "write a loop"

    # Only the most recent interactions are rated.
    many = c._SessionEvidence(
        [], [interaction(str(i)) for i in range(c._MAX_RUBRIC_INTERACTIONS + 3)], {}, [], []
    )
    sampled = Scripted(json.dumps(scores))
    c._signal_llm_rubrics(sampled, db_session, many, None)
    assert len(sampled.calls) == c._MAX_RUBRIC_INTERACTIONS
    assert str(c._MAX_RUBRIC_INTERACTIONS + 2) in sampled.calls[-1][0]


def test_adaptive_use_needs_a_current_ratio(db_session: OrmSession) -> None:
    session = SimpleNamespace(student_id=1, id=1)
    none = c._signal_adaptive_ai_use(db_session, session, None)  # type: ignore[arg-type]
    assert (none.value, none.reason) == (None, "no AI interactions in this session")
    first = c._signal_adaptive_ai_use(db_session, session, 0.7)  # type: ignore[arg-type]
    assert first.value is None
    assert first.reason == "insufficient session history for a longitudinal comparison"
