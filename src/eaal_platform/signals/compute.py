"""Computes the fourteen EAAL signals from a session's event log.

Mirrors ``docs/The EAAL Framework.pdf`` (see the project's "MAD" workspace)
section 6: each signal is estimated from platform evidence using the
approach the framework proposes for it — deterministic event analysis for
behavioral signals (S1.3-S1.5, S2.1-S2.4, S3.2), code-relationship analysis
for adoption/modification (S1.3, S1.4, S2.3), structured low-temperature LLM
rubric scoring for signals that depend on the *meaning* of what the student
wrote (S1.1, S1.2, S3.1) — never a bare deterministic proxy standing in for
language understanding — and, for S3.3/S3.4, the same deterministic
"clean, substantive final run" proxy as S3.2, scoped to sessions on a
professor-authored follow-on Task (see ``db.models.AssessmentKind``) that
the framework's own structure requires: a *different* problem for Transfer,
a *delayed* attempt for Retention. A session on an ordinary Lab has no such
Task to point at, so S3.3/S3.4 correctly return ``None`` for it — that's
"not applicable to this session," not "not implemented."

A ``SignalResult.value`` of ``None`` always means "not enough evidence" or
"not applicable to this session," never a hidden 0.0 — the CIQ Score screen
must be able to show that case differently from a genuinely low score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, GenerationContext, Purpose
from eaal_platform.db.models import (
    AIInteraction,
    AssessmentKind,
    CodeSnapshot,
    ConceptCheckResponse,
    Event,
    EventType,
    ExecutionResult,
    SignalScore,
)
from eaal_platform.db.models import Session as SessionModel

_CODE_BLOCK_RE = re.compile(r"```[a-zA-Z0-9]*\n(.*?)```", re.DOTALL)

# Each rubric pass is a real model inference call; a handful of a session's
# most recent AI messages is enough evidence without an unbounded wait on
# the CIQ Score screen.
_MAX_RUBRIC_INTERACTIONS = 3
_MAX_HISTORY_SESSIONS = 5

# S1.3 (Response Utilization): the minimum AI-code/edit similarity for a
# follow-up edit to count as "utilizing" the response, rather than being an
# unrelated edit that merely happened to occur afterward.
_UTILIZATION_SIMILARITY_THRESHOLD = 0.15

_RUBRIC_INSTRUCTION = (
    "You are scoring a student's help-request message to an AI coding "
    "assistant, using two measures from the EAAL research framework. "
    "Score each from 0.0 (poor) to 1.0 (excellent):\n"
    "- help_seeking_calibration: how appropriate is asking for help right "
    "now, given the task, the student's current code, and any recent "
    "error? 0 means an unnecessary or premature request, or a request for "
    "a complete solution with no attempt made; 1 means a well-timed, "
    "specifically targeted request.\n"
    "- student_grounding: does the message include relevant context — the "
    "student's own attempt, a specific difficulty, constraints, or the "
    "error encountered — rather than a bare, context-free question? 0 "
    "means no context given; 1 means clear, specific context given.\n\n"
    'The student\'s message:\n"""\n{message}\n"""\n\n'
    "Respond with ONLY a JSON object of the form "
    '{{"help_seeking_calibration": <float 0-1>, "student_grounding": <float 0-1>}}. '
    "No other text."
)

# S3.4 (Retention & Independent Recall): the framework requires "delayed
# assessment after the original AI-assisted activity" (§6.6) — a fixed
# minimum, rather than any positive gap, so a student re-attempting minutes
# later can't pass off ordinary re-practice as evidence of retention. A
# provisional policy choice, not an empirically validated threshold.
_RETENTION_MIN_DELAY_HOURS = 24

_CONCEPT_CHECK_RUBRIC_INSTRUCTION = (
    "You are scoring a student's written explanation of the concept behind "
    "their coding solution, using one measure from the EAAL research "
    "framework. Score from 0.0 (poor) to 1.0 (excellent):\n"
    "- conceptual_understanding: does the explanation demonstrate genuine "
    "understanding of the underlying concept and why the approach works — "
    "not just a restatement of what the code does line by line? 0 means no "
    "real understanding shown, or a description of surface mechanics only; "
    "1 means clear, accurate understanding of the underlying concept.\n\n"
    "Task:\n{task_description}\n\n"
    'The student\'s explanation:\n"""\n{response}\n"""\n\n'
    "Respond with ONLY a JSON object of the form "
    '{{"conceptual_understanding": <float 0-1>}}. No other text.'
)


@dataclass(frozen=True, slots=True)
class SignalResult:
    """One signal's computed value, or why it couldn't be computed."""

    value: float | None
    reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _InteractionOutcome:
    utilized: bool
    changed_from_before: bool
    verified: bool
    agency: float


@dataclass(frozen=True, slots=True)
class _SessionEvidence:
    events: list[Event]
    ai_interactions: list[AIInteraction]
    snapshots: dict[int, CodeSnapshot]
    ai_prompt_events: list[Event]
    ai_response_events: list[Event]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _extract_code_blocks(text: str) -> list[str]:
    return [block.strip() for block in _CODE_BLOCK_RE.findall(text) if block.strip()]


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _referenced_snapshot_ids(events: list[Event], ai_interactions: list[AIInteraction]) -> set[int]:
    snapshot_ids = {e.code_version_id for e in events if e.code_version_id is not None}
    snapshot_ids.update(
        sid
        for interaction in ai_interactions
        for sid in (interaction.code_version_before_id, interaction.code_version_after_id)
        if sid is not None
    )
    return snapshot_ids


def _load_evidence(db_session: OrmSession, session_id: int) -> _SessionEvidence:
    events = db_session.query(Event).filter_by(session_id=session_id).order_by(Event.id).all()
    ai_interactions = (
        db_session.query(AIInteraction)
        .filter_by(session_id=session_id)
        .order_by(AIInteraction.id)
        .all()
    )

    snapshot_ids = _referenced_snapshot_ids(events, ai_interactions)
    snapshots = {
        s.id: s
        for s in db_session.query(CodeSnapshot).filter(CodeSnapshot.id.in_(snapshot_ids)).all()
    }

    return _SessionEvidence(
        events=events,
        ai_interactions=ai_interactions,
        snapshots=snapshots,
        ai_prompt_events=[e for e in events if e.event_type == EventType.AI_PROMPT],
        ai_response_events=[e for e in events if e.event_type == EventType.AI_RESPONSE],
    )


def _slice_between(events: list[Event], start: Event | None, end: Event | None) -> list[Event]:
    start_idx = events.index(start) + 1 if start is not None else 0
    end_idx = events.index(end) if end is not None else len(events)
    return events[start_idx:end_idx]


def _pre_ai_window(ev: _SessionEvidence) -> list[Event]:
    if not ev.ai_prompt_events:
        return ev.events
    return _slice_between(ev.events, None, ev.ai_prompt_events[0])


def _interaction_windows(ev: _SessionEvidence) -> list[list[Event]]:
    """Events between each AI response and the *next* AI prompt (or session end)."""
    windows = []
    for i in range(len(ev.ai_interactions)):
        start = ev.ai_response_events[i] if i < len(ev.ai_response_events) else None
        end = ev.ai_prompt_events[i + 1] if i + 1 < len(ev.ai_prompt_events) else None
        windows.append(_slice_between(ev.events, start, end))
    return windows


@dataclass(frozen=True, slots=True)
class _PostResponseActivity:
    changed_from_before: bool
    verified: bool
    sim_to_ai: float | None


def _activity_after_edit(
    ev: _SessionEvidence,
    window: list[Event],
    next_edit: Event,
    before_content: str,
    code_blocks: list[str],
) -> _PostResponseActivity:
    after = ev.snapshots.get(next_edit.code_version_id) if next_edit.code_version_id else None
    after_content = after.content if after else ""
    sim_to_ai = _similarity("\n".join(code_blocks), after_content) if code_blocks else None
    next_edit_idx = window.index(next_edit)
    verified = any(
        e.event_type in (EventType.CODE_RUN, EventType.EXECUTION_RESULT)
        for e in window[next_edit_idx + 1 :]
    )
    return _PostResponseActivity(
        changed_from_before=after_content.strip() != before_content.strip(),
        verified=verified,
        sim_to_ai=sim_to_ai,
    )


def _agency_for(code_blocks: list[str], sim_to_ai: float | None) -> float:
    if not code_blocks:
        # The response was conceptual (no code to blindly copy), so any
        # follow-up work is necessarily the student's own construction.
        return 1.0
    if sim_to_ai is not None:
        return _clamp01(1.0 - sim_to_ai)
    return 0.0  # AI gave code and the student never touched it again.


def _analyze_interaction(
    ev: _SessionEvidence, interaction: AIInteraction, window: list[Event]
) -> _InteractionOutcome:
    next_edit = next((e for e in window if e.event_type == EventType.CODE_EDIT), None)
    before = ev.snapshots.get(interaction.code_version_before_id or -1)
    before_content = before.content if before else ""
    code_blocks = _extract_code_blocks(interaction.response or "")

    activity = None
    if next_edit is not None:
        activity = _activity_after_edit(ev, window, next_edit, before_content, code_blocks)

    return _InteractionOutcome(
        utilized=_is_utilized(code_blocks, activity),
        changed_from_before=activity.changed_from_before if activity else False,
        verified=activity.verified if activity else False,
        agency=_agency_for(code_blocks, activity.sim_to_ai if activity else None),
    )


def _is_utilized(code_blocks: list[str], activity: _PostResponseActivity | None) -> bool:
    if activity is None:
        return False
    if not code_blocks:
        # No code to compare a follow-up edit against — a conceptual
        # response's uptake can't be judged from a text diff, so any
        # follow-up edit is the best available proxy (the LLM rubrics
        # S1.1/S1.2 are what judge meaning-level uptake).
        return True
    # The AI gave code: only count it as "utilized" if the follow-up edit
    # actually resembles that code, rather than crediting utilization for
    # any edit that happens to occur afterward regardless of content.
    return (activity.sim_to_ai or 0.0) >= _UTILIZATION_SIMILARITY_THRESHOLD


def _verification_ratio(outcomes: list[_InteractionOutcome]) -> float | None:
    if not outcomes:
        return None
    per_interaction = [
        0.5 * (1.0 if o.changed_from_before else 0.0) + 0.5 * (1.0 if o.verified else 0.0)
        for o in outcomes
    ]
    return sum(per_interaction) / len(per_interaction)


# -- Pillar 1: AI Utilization -------------------------------------------------


def _signal_response_utilization(outcomes: list[_InteractionOutcome]) -> SignalResult:
    if not outcomes:
        return SignalResult(None, "no AI interactions in this session")
    score = sum(1 for o in outcomes if o.utilized) / len(outcomes)
    return SignalResult(score, evidence={"interactions": len(outcomes)})


def _signal_modification_verification(outcomes: list[_InteractionOutcome]) -> SignalResult:
    ratio = _verification_ratio(outcomes)
    if ratio is None:
        return SignalResult(None, "no AI interactions in this session")
    return SignalResult(ratio, evidence={"interactions": len(outcomes)})


def _signal_followup_engagement(ev: _SessionEvidence, windows: list[list[Event]]) -> SignalResult:
    n = len(ev.ai_interactions)
    if n < 2:
        return SignalResult(None, "fewer than two AI interactions to compare")
    followups = 0
    for i in range(1, n):
        tried_something = any(
            e.event_type in (EventType.CODE_EDIT, EventType.CODE_RUN) for e in windows[i - 1]
        )
        prompt_event = ev.ai_prompt_events[i] if i < len(ev.ai_prompt_events) else None
        had_recent_error = bool(
            prompt_event
            and prompt_event.payload_json
            and prompt_event.payload_json.get("had_recent_error")
        )
        if tried_something or had_recent_error:
            followups += 1
    score = followups / (n - 1)
    return SignalResult(score, evidence={"opportunities": n - 1, "followups": followups})


def _last_execution_before(
    db_session: OrmSession, session_id: int, before: Any
) -> ExecutionResult | None:
    return (
        db_session.query(ExecutionResult)
        .filter(ExecutionResult.session_id == session_id, ExecutionResult.timestamp <= before)
        .order_by(ExecutionResult.timestamp.desc())
        .first()
    )


def _rubric_score_interaction(
    ai_provider: AIProvider,
    db_session: OrmSession,
    ev: _SessionEvidence,
    interaction: AIInteraction,
    task_description: str | None,
) -> dict[str, float] | None:
    # The rubric asks the model to judge appropriateness "given the task,
    # the student's current code, and any recent error" — so it needs the
    # same code/stdout/stderr context `_build_ai_context` gives the live
    # chat call, not just the bare prompt string.
    before_snapshot = ev.snapshots.get(interaction.code_version_before_id or -1)
    last_result = _last_execution_before(db_session, interaction.session_id, interaction.timestamp)
    context = GenerationContext(
        task_description=task_description,
        current_code=before_snapshot.content if before_snapshot else None,
        recent_stdout=last_result.stdout if last_result else None,
        recent_stderr=last_result.stderr if last_result else None,
    )
    prompt = _RUBRIC_INSTRUCTION.format(message=interaction.prompt)
    result = ai_provider.generate(prompt, context, Purpose.RUBRIC_SCORING)
    if not result.available:
        return None
    try:
        data = json.loads(result.text)
        return {
            "help_seeking_calibration": _clamp01(float(data["help_seeking_calibration"])),
            "student_grounding": _clamp01(float(data["student_grounding"])),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _signal_llm_rubrics(
    ai_provider: AIProvider | None,
    db_session: OrmSession,
    ev: _SessionEvidence,
    task_description: str | None,
) -> tuple[SignalResult, SignalResult]:
    if not ev.ai_interactions:
        no_evidence = SignalResult(None, "no AI interactions in this session")
        return no_evidence, no_evidence
    if ai_provider is None or not ai_provider.ping():
        unavailable = SignalResult(None, "the AI evaluator is not reachable right now")
        return unavailable, unavailable

    sample = ev.ai_interactions[-_MAX_RUBRIC_INTERACTIONS:]
    calibration_scores: list[float] = []
    grounding_scores: list[float] = []
    for interaction in sample:
        scores = _rubric_score_interaction(
            ai_provider, db_session, ev, interaction, task_description
        )
        if scores is None:
            continue
        calibration_scores.append(scores["help_seeking_calibration"])
        grounding_scores.append(scores["student_grounding"])

    if not calibration_scores:
        failed = SignalResult(None, "the AI evaluator did not return a usable score")
        return failed, failed

    evidence = {"rated_interactions": len(calibration_scores)}
    return (
        SignalResult(sum(calibration_scores) / len(calibration_scores), evidence=evidence),
        SignalResult(sum(grounding_scores) / len(grounding_scores), evidence=evidence),
    )


def _signal_adaptive_ai_use(
    db_session: OrmSession, current_session: SessionModel, current_ratio: float | None
) -> SignalResult:
    if current_ratio is None:
        return SignalResult(None, "no AI interactions in this session")

    # Ordered by id, not ``started_at``: SQLite's ``CURRENT_TIMESTAMP``
    # only has second resolution, so two sessions started moments apart
    # (routine in a fast-moving lab, or in tests) can tie on timestamp —
    # id is monotonically assigned in creation order and never ties.
    prior_sessions = (
        db_session.query(SessionModel)
        .filter(
            SessionModel.student_id == current_session.student_id,
            SessionModel.id < current_session.id,
        )
        .order_by(SessionModel.id.desc())
        .limit(_MAX_HISTORY_SESSIONS)
        .all()
    )

    historical_ratios: list[float] = []
    for prior in prior_sessions:
        prior_evidence = _load_evidence(db_session, prior.id)
        prior_windows = _interaction_windows(prior_evidence)
        prior_outcomes = [
            _analyze_interaction(prior_evidence, interaction, window)
            for interaction, window in zip(
                prior_evidence.ai_interactions, prior_windows, strict=True
            )
        ]
        ratio = _verification_ratio(prior_outcomes)
        if ratio is not None:
            historical_ratios.append(ratio)

    if not historical_ratios:
        return SignalResult(None, "insufficient session history for a longitudinal comparison")

    historical_average = sum(historical_ratios) / len(historical_ratios)
    score = _clamp01(0.5 + (current_ratio - historical_average))
    return SignalResult(
        score,
        evidence={
            "current_ratio": current_ratio,
            "historical_average": historical_average,
            "sessions_compared": len(historical_ratios),
        },
    )


# -- Pillar 2: Cognitive Engagement -------------------------------------------


def _signal_independent_initiation(ev: _SessionEvidence) -> SignalResult:
    window = _pre_ai_window(ev)
    edits = sum(1 for e in window if e.event_type == EventType.CODE_EDIT)
    runs = sum(1 for e in window if e.event_type == EventType.CODE_RUN)

    if not ev.ai_prompt_events:
        if edits == 0:
            return SignalResult(None, "no coding activity recorded yet")
        return SignalResult(1.0, evidence={"edits_before_ai": edits, "runs_before_ai": runs})

    if edits == 0 and runs == 0:
        return SignalResult(0.0, evidence={"edits_before_ai": 0, "runs_before_ai": 0})

    score = _clamp01(0.5 * min(edits / 3, 1.0) + 0.5 * min(runs / 2, 1.0))
    return SignalResult(score, evidence={"edits_before_ai": edits, "runs_before_ai": runs})


def _signal_reasoning_continuity(ev: _SessionEvidence, windows: list[list[Event]]) -> SignalResult:
    if not ev.ai_interactions:
        return SignalResult(None, "no AI interactions in this session")
    continued = sum(
        1
        for window in windows
        if any(e.event_type in (EventType.CODE_EDIT, EventType.CODE_RUN) for e in window)
    )
    score = continued / len(windows)
    evidence = {"interactions": len(windows), "continued_after": continued}
    return SignalResult(score, evidence=evidence)


def _signal_problem_solving_agency(outcomes: list[_InteractionOutcome]) -> SignalResult:
    if not outcomes:
        return SignalResult(None, "no AI interactions in this session")
    score = sum(o.agency for o in outcomes) / len(outcomes)
    return SignalResult(score, evidence={"interactions": len(outcomes)})


def _is_error_result(event: Event) -> bool:
    if event.event_type != EventType.EXECUTION_RESULT or event.payload_json is None:
        return False
    return event.payload_json.get("exit_status") not in (0,) or bool(
        event.payload_json.get("timed_out")
    )


def _last_modifying_edit_index(
    ev: _SessionEvidence, window: list[Event], before_content: str
) -> int | None:
    last_index: int | None = None
    for i, e in enumerate(window):
        if e.event_type != EventType.CODE_EDIT:
            continue
        after = ev.snapshots.get(e.code_version_id or -1)
        if after and after.content.strip() != before_content.strip():
            last_index = i
    return last_index


def _recovered_from_error(
    ev: _SessionEvidence, error_event: Event, next_error: Event | None
) -> bool:
    """True if, between this error and the next one (or session end), the
    student actually changed the code and then verified a clean run.

    Not "the very next event wasn't an AI prompt" (the old check) — that
    credited a student who did nothing at all, or who reran the exact same
    broken code, with full recovery. Recovery requires real evidence:
    Modification (the code actually changed) and Verification (a
    subsequent clean execution), matching the framework's own
    Error -> Investigation -> Action -> Modification -> Verification chain
    more closely than "didn't immediately ask the AI" ever did.
    """
    start_idx = ev.events.index(error_event) + 1
    end_idx = ev.events.index(next_error) if next_error is not None else len(ev.events)
    window = ev.events[start_idx:end_idx]

    error_snapshot = ev.snapshots.get(error_event.code_version_id or -1)
    before_content = error_snapshot.content if error_snapshot else ""
    last_modifying_edit_idx = _last_modifying_edit_index(ev, window, before_content)
    if last_modifying_edit_idx is None:
        return False  # code was never actually changed after the error

    return any(
        e.event_type == EventType.EXECUTION_RESULT and not _is_error_result(e)
        for e in window[last_modifying_edit_idx + 1 :]
    )


def _signal_error_recovery(ev: _SessionEvidence) -> SignalResult:
    error_events = [e for e in ev.events if _is_error_result(e)]
    if not error_events:
        return SignalResult(None, "no execution errors encountered")

    recovered = sum(
        1
        for i, err in enumerate(error_events)
        if _recovered_from_error(
            ev, err, error_events[i + 1] if i + 1 < len(error_events) else None
        )
    )
    score = recovered / len(error_events)
    return SignalResult(score, evidence={"errors": len(error_events), "recovered": recovered})


# -- Pillar 3: Learning & Knowledge Development -------------------------------


def _final_run_quality(ev: _SessionEvidence) -> tuple[float, dict[str, Any]] | None:
    """Shared by S3.2/S3.3/S3.4: did the session's last run exit cleanly and
    actually produce output, rather than a no-op?

    This remains a coarse proxy pending real assessment infrastructure
    (grading against expected output/tests, per the framework's own
    Task Performance != Knowledge Development distinction, §4.5) — but it
    must not score a no-op program identically to a substantive one.
    Requiring the run to have actually produced output rules out the
    `pass`-only case, which a bare exit-code check let through. Returns
    ``None`` (rather than a result) when no code was executed at all.
    """
    execution_events = [e for e in ev.events if e.event_type == EventType.EXECUTION_RESULT]
    if not execution_events:
        return None
    payload = execution_events[-1].payload_json or {}
    clean = (
        payload.get("exit_status") == 0
        and not payload.get("timed_out")
        and bool(payload.get("has_output"))
    )
    evidence = {
        "final_exit_status": payload.get("exit_status"),
        "timed_out": payload.get("timed_out"),
        "has_output": payload.get("has_output"),
    }
    return (1.0 if clean else 0.0), evidence


def _signal_knowledge_application(ev: _SessionEvidence) -> SignalResult:
    result = _final_run_quality(ev)
    if result is None:
        return SignalResult(None, "no code was executed in this session")
    value, evidence = result
    return SignalResult(value, evidence=evidence)


def _signal_knowledge_transfer(session: SessionModel, ev: _SessionEvidence) -> SignalResult:
    if session.task.assessment_kind != AssessmentKind.TRANSFER:
        return SignalResult(None, "this session is not a Knowledge Transfer assessment for any Lab")
    result = _final_run_quality(ev)
    if result is None:
        return SignalResult(None, "no code was executed in this transfer assessment")
    value, evidence = result
    return SignalResult(value, evidence=evidence)


def _signal_retention_recall(
    db_session: OrmSession, session: SessionModel, ev: _SessionEvidence
) -> SignalResult:
    task = session.task
    if task.assessment_kind != AssessmentKind.RETENTION or task.linked_task_id is None:
        return SignalResult(
            None, "this session is not a Retention & Independent Recall assessment for any Lab"
        )

    earliest_original_start = (
        db_session.query(func.min(SessionModel.started_at))
        .filter(
            SessionModel.task_id == task.linked_task_id,
            SessionModel.student_id == session.student_id,
        )
        .scalar()
    )
    if earliest_original_start is None:
        return SignalResult(None, "no original-lab activity found to measure a delay against")

    delay = session.started_at - earliest_original_start
    if delay < timedelta(hours=_RETENTION_MIN_DELAY_HOURS):
        delay_hours = round(delay.total_seconds() / 3600, 1)
        return SignalResult(
            None,
            f"attempted only {delay_hours}h after the original lab activity — needs at "
            f"least {_RETENTION_MIN_DELAY_HOURS}h to count as a delayed retention check",
        )

    result = _final_run_quality(ev)
    if result is None:
        return SignalResult(None, "no code was executed in this retention check")
    value, evidence = result
    evidence["delay_hours"] = round(delay.total_seconds() / 3600, 1)
    return SignalResult(value, evidence=evidence)


def _signal_conceptual_understanding(
    ai_provider: AIProvider | None,
    db_session: OrmSession,
    session_id: int,
    task_description: str | None,
) -> SignalResult:
    response = db_session.query(ConceptCheckResponse).filter_by(session_id=session_id).first()
    if response is None:
        return SignalResult(None, "no concept-check explanation submitted for this session")
    if ai_provider is None or not ai_provider.ping():
        return SignalResult(None, "the AI evaluator is not reachable right now")

    prompt = _CONCEPT_CHECK_RUBRIC_INSTRUCTION.format(
        task_description=task_description or "(no task description available)",
        response=response.response_text,
    )
    context = GenerationContext(task_description=task_description)
    result = ai_provider.generate(prompt, context, Purpose.RUBRIC_SCORING)
    if not result.available:
        return SignalResult(None, "the AI evaluator did not return a usable score")
    try:
        data = json.loads(result.text)
        value = _clamp01(float(data["conceptual_understanding"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return SignalResult(None, "the AI evaluator did not return a usable score")
    return SignalResult(value, evidence={"response_length": len(response.response_text)})


def compute_all_signals(
    session_factory: sessionmaker[OrmSession],
    session_id: int,
    ai_provider: AIProvider | None = None,
) -> dict[str, SignalResult]:
    """Compute all fourteen EAAL signals for one session, fresh, every call.

    Deliberately not cached — a session's event log only grows, and these
    computations are cheap except for the (bounded, capped) LLM rubric
    calls, so recomputing on demand is simpler than invalidating a cache.
    """
    with session_factory() as db_session:
        session = db_session.get(SessionModel, session_id)
        if session is None:
            raise ValueError(f"No session with id {session_id}")
        task_description = session.task.description

        ev = _load_evidence(db_session, session_id)
        windows = _interaction_windows(ev)
        outcomes = [
            _analyze_interaction(ev, interaction, window)
            for interaction, window in zip(ev.ai_interactions, windows, strict=True)
        ]

        s1_1, s1_2 = _signal_llm_rubrics(ai_provider, db_session, ev, task_description)
        current_ratio = _verification_ratio(outcomes)

        return {
            "S1.1": s1_1,
            "S1.2": s1_2,
            "S1.3": _signal_response_utilization(outcomes),
            "S1.4": _signal_modification_verification(outcomes),
            "S1.5": _signal_followup_engagement(ev, windows),
            "S1.6": _signal_adaptive_ai_use(db_session, session, current_ratio),
            "S2.1": _signal_independent_initiation(ev),
            "S2.2": _signal_reasoning_continuity(ev, windows),
            "S2.3": _signal_problem_solving_agency(outcomes),
            "S2.4": _signal_error_recovery(ev),
            "S3.1": _signal_conceptual_understanding(
                ai_provider, db_session, session_id, task_description
            ),
            "S3.2": _signal_knowledge_application(ev),
            "S3.3": _signal_knowledge_transfer(session, ev),
            "S3.4": _signal_retention_recall(db_session, session, ev),
        }


def persist_signal_scores(
    session_factory: sessionmaker[OrmSession],
    session_id: int,
    results: dict[str, SignalResult],
) -> None:
    """Append one ``SignalScore`` row per signal (Layer 3), never updating past rows."""
    with session_factory() as db_session:
        for key, result in results.items():
            evidence_json: dict[str, Any] | None = None
            if result.reason or result.evidence:
                evidence_json = {**result.evidence}
                if result.reason:
                    evidence_json["reason"] = result.reason
            db_session.add(
                SignalScore(
                    session_id=session_id,
                    signal_key=key,
                    value=result.value,
                    evidence_json=evidence_json,
                )
            )
        db_session.commit()
