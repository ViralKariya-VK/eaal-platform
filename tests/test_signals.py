"""Tests for the EAAL signal-computation engine (``signals/compute.py``).

Each test drives the engine through the same ``CavyApi`` calls the frontend
would make (rather than poking the database directly), so a test failure
here means the *actual* event log the app produces doesn't yield the
expected signal — the thing that matters.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose
from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import create_student_account, seed_demo_content
from eaal_platform.events.logger import EventLogger
from eaal_platform.signals.compute import compute_all_signals


class _ScriptedProvider(AIProvider):
    """A fake provider whose reply (and rubric scores) are set per-test."""

    def __init__(self, *, reply: str = "Try using a loop.", available: bool = True) -> None:
        self._reply = reply
        self._available = available
        self.rubric_response = json.dumps(
            {"help_seeking_calibration": 0.8, "student_grounding": 0.6}
        )
        self.concept_rubric_response = json.dumps({"conceptual_understanding": 0.75})
        self.rubric_contexts: list[GenerationContext] = []

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        if not self._available:
            return GenerationResult(text="", available=False, error="unreachable")
        if purpose is Purpose.RUBRIC_SCORING:
            self.rubric_contexts.append(context)
            if "conceptual_understanding" in prompt:
                return GenerationResult(text=self.concept_rubric_response, available=True)
            return GenerationResult(text=self.rubric_response, available=True)
        return GenerationResult(text=self._reply, available=True)

    def ping(self) -> bool:
        return self._available

    @property
    def provider_name(self) -> str:
        return "scripted"

    @property
    def model_name(self) -> str | None:
        return "scripted-model"


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
    logger = EventLogger(db_session_factory, batch_size=1, flush_interval=0.05)
    logger.start()
    api = CavyApi(db_session_factory, logger, ai_provider=provider)
    assert api.login("student", "student@example.com", "hunter2")["ok"] is True
    return api, logger


def _files(code: str) -> dict[str, str]:
    return {"main.py": code}


def _signals(
    db_session_factory: sessionmaker[OrmSession], session_id: int, provider: AIProvider | None
) -> dict[str, object]:
    return compute_all_signals(db_session_factory, session_id, provider)


def test_independent_initiation_full_credit_with_no_ai_use(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.log_code_edit(session_id, _files("x = 1"))
    api.run_code(session_id, _files("x = 1"))

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S2.1"].value == 1.0


def test_independent_initiation_none_without_any_activity(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S2.1"].value is None
    assert results["S2.1"].reason


def test_independent_initiation_partial_credit_before_first_ai_prompt(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.log_code_edit(session_id, _files("x = 1"))
    api.run_code(session_id, _files("x = 1"))
    api.send_ai_message(session_id, "how do I do this?", _files("x = 1"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert 0.0 < results["S2.1"].value < 1.0


def test_response_utilization_and_modification_when_student_edits_after_reply(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider(reply="```python\nprint('hi')\n```")
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "give me code", _files(""))
    api.log_code_edit(session_id, _files("print('hi')  # adopted and tweaked"))
    api.run_code(session_id, _files("print('hi')  # adopted and tweaked"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.3"].value == 1.0  # did something with code afterward
    assert results["S1.4"].value == 1.0  # changed it AND verified with a run


def test_response_utilization_zero_when_nothing_happens_after_reply(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider(reply="```python\nprint('hi')\n```")
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "give me code", _files(""))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.3"].value == 0.0
    assert results["S1.4"].value == 0.0


def test_problem_solving_agency_high_when_ai_gives_no_code(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider(reply="Think about the base case of your recursion.")
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "I'm stuck", _files("def f(): pass"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S2.3"].value == 1.0


def test_problem_solving_agency_low_when_code_adopted_verbatim(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    ai_code = "def solve():\n    return 42\n"
    provider = _ScriptedProvider(reply=f"```python\n{ai_code}```")
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "solve this for me", _files(""))
    api.log_code_edit(session_id, _files(ai_code))  # pasted verbatim, unmodified

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    # Not near-zero: the snapshot bundles all of the session's files together
    # (see `_bundle_files`), so an exact-match paste still has a bit of
    # framing text around it that keeps the similarity ratio just under 1.0.
    assert results["S2.3"].value < 0.25


def test_followup_engagement_requires_at_least_two_interactions(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]
    api.send_ai_message(session_id, "hello", _files(""))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.5"].value is None


def test_followup_engagement_scores_activity_between_prompts(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "how do I start?", _files(""))
    api.log_code_edit(session_id, _files("x = 1"))  # tried something before asking again
    api.send_ai_message(session_id, "does this look right?", _files("x = 1"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.5"].value == 1.0


def test_evidence_based_error_recovery_credits_investigation_over_delegation(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.run_code(session_id, _files("raise ValueError('bad')"))  # an error
    api.log_code_edit(session_id, _files("print('fixed')"))  # investigated on their own
    api.run_code(session_id, _files("print('fixed')"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S2.4"].value == 1.0


def test_evidence_based_error_recovery_zero_on_immediate_delegation(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.run_code(session_id, _files("raise ValueError('bad')"))  # an error
    api.send_ai_message(session_id, "fix this for me", _files("raise ValueError('bad')"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S2.4"].value == 0.0


def test_knowledge_application_reflects_final_run_correctness(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.run_code(session_id, _files("raise ValueError('bad')"))
    api.run_code(session_id, _files("print('ok')"))

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S3.2"].value == 1.0


def test_not_yet_implemented_signals_always_return_none_with_a_reason(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    for key in ("S3.1", "S3.3", "S3.4"):
        assert results[key].value is None
        assert results[key].reason


def test_llm_rubric_signals_use_the_scripted_provider(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]
    api.send_ai_message(session_id, "how do I approach this?", _files(""))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.1"].value == 0.8
    assert results["S1.2"].value == 0.6


def test_llm_rubric_signals_none_when_provider_unavailable(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider(available=False)
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]
    api.send_ai_message(session_id, "hello", _files(""))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.1"].value is None
    assert results["S1.2"].value is None


def test_adaptive_ai_use_needs_session_history(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider(reply="```python\nprint(1)\n```")
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]
    api.send_ai_message(session_id, "help", _files(""))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.6"].value is None
    assert "history" in (results["S1.6"].reason or "")


def test_adaptive_ai_use_compares_against_prior_sessions(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider(reply="```python\nprint(1)\n```")
    api, logger = _make_api(db_session_factory, provider)

    first_session = api.start_practice()["session_id"]
    api.send_ai_message(first_session, "give me the answer", _files(""))  # never modified/verified

    second_session = api.start_practice()["session_id"]
    api.send_ai_message(second_session, "give me the answer", _files(""))
    api.log_code_edit(second_session, _files("print(1)  # changed it"))
    api.run_code(second_session, _files("print(1)  # changed it"))

    logger.stop()
    results = _signals(db_session_factory, second_session, provider)
    assert results["S1.6"].value is not None
    assert results["S1.6"].value > 0.5  # improved verification ratio vs. the first session


# -- Regression tests for the audit's confirmed-wrong / gameable cases ------


def test_error_recovery_zero_when_student_does_nothing_after_error(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    """An error followed by no further activity is not recovery.

    The old check only looked at whether the *next* event was an AI prompt,
    so an error the student never came back to scored a perfect 1.0.
    """
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.run_code(session_id, _files("raise ValueError('bad')"))

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S2.4"].value == 0.0


def test_error_recovery_zero_when_rerunning_identical_broken_code(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    """Rerunning the exact same failing code is not recovery either."""
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.run_code(session_id, _files("raise ValueError('bad')"))
    api.run_code(session_id, _files("raise ValueError('bad')"))

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S2.4"].value == 0.0


def test_error_recovery_credits_a_real_fix_verified_by_a_clean_run(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.run_code(session_id, _files("raise ValueError('bad')"))
    api.log_code_edit(session_id, _files("print('fixed')"))
    api.run_code(session_id, _files("print('fixed')"))

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S2.4"].value == 1.0


def test_knowledge_application_zero_for_a_no_op_program(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    """A `pass`-only program exits cleanly but does nothing — it must not
    score identically to a program that actually produced output."""
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.run_code(session_id, _files("pass"))

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S3.2"].value == 0.0


def test_response_utilization_zero_when_followup_edit_is_unrelated_to_ai_code(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    """An edit that happens to occur after an AI reply but ignores it
    entirely must not count as "utilizing" that reply."""
    provider = _ScriptedProvider(reply="```python\ndef solve():\n    return 42\n```")
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "give me code", _files(""))
    api.log_code_edit(
        session_id,
        _files("qzx_totally_unrelated_variable_name = 'nothing to do with the suggestion here'"),
    )

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S1.3"].value == 0.0


def test_llm_rubric_context_includes_current_code_and_recent_error(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    """The rubric prompt claims to judge appropriateness given the
    student's code and any recent error — the context passed to the
    provider must actually carry that, not just the bare message."""
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.run_code(session_id, _files("raise ValueError('bad')"))
    api.send_ai_message(session_id, "why did this fail?", _files("raise ValueError('bad')"))

    logger.stop()
    _signals(db_session_factory, session_id, provider)
    assert len(provider.rubric_contexts) == 1
    context = provider.rubric_contexts[0]
    assert context.current_code is not None
    assert "raise ValueError" in context.current_code
    assert context.recent_stderr


def test_reasoning_continuity_full_credit_when_student_acts_after_every_reply(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "how do I start?", _files(""))
    api.log_code_edit(session_id, _files("x = 1"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S2.2"].value == 1.0


def test_reasoning_continuity_zero_when_student_never_acts_after_reply(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]

    api.send_ai_message(session_id, "how do I start?", _files(""))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S2.2"].value == 0.0


def test_reasoning_continuity_none_without_any_ai_interactions(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    api, logger = _make_api(db_session_factory)
    session_id = api.start_practice()["session_id"]
    api.log_code_edit(session_id, _files("x = 1"))

    logger.stop()
    results = _signals(db_session_factory, session_id, None)
    assert results["S2.2"].value is None
    assert results["S2.2"].reason


def test_conceptual_understanding_none_without_a_concept_check_response(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]
    api.run_code(session_id, _files("print('ok')"))

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S3.1"].value is None
    assert results["S3.1"].reason


def test_conceptual_understanding_scores_a_submitted_explanation(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider()
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]
    api.submit_concept_check(
        session_id, "It works because recursion breaks the problem into a base case."
    )

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S3.1"].value == 0.75


def test_conceptual_understanding_none_when_ai_evaluator_unavailable(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    provider = _ScriptedProvider(available=False)
    api, logger = _make_api(db_session_factory, provider)
    session_id = api.start_practice()["session_id"]
    api.submit_concept_check(session_id, "Because loops repeat until a condition is false.")

    logger.stop()
    results = _signals(db_session_factory, session_id, provider)
    assert results["S3.1"].value is None
