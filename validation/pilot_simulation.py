"""A synthetic single-agent LONGITUDINAL feasibility demonstration.

READ THIS BEFORE CITING ANYTHING FROM THIS SCRIPT IN THE PAPER:

This is NOT learner data. It is one AI agent (the same local model, qwen3:8b,
that powers the app's tutor) role-playing a student persona whose skill
level is scripted to improve across sessions, run through the *real*
CavyApi bridge exactly like every other script in validation/. Its purpose
is narrow: to show that the full EAAL_t = (P1, P2, P3) measurement chain —
event logging, signal computation, and a multi-session trajectory — runs
end-to-end, including the two signals (S3.3, S3.4) that need a follow-on
assessment and a real time delay. It demonstrates OPERATIONAL FEASIBILITY
of the longitudinal pipeline the paper's Figure 1 and Eq. 1 describe.

It does not, and cannot, demonstrate that the 14 signals validly measure
real student cognition — that requires actual human learners, which is
exactly what both reviewers asked for and what this script is not. If used
in the paper, label it explicitly as a synthetic single-agent case study /
system walkthrough, with n=1 AI persona, never as pilot or cohort data.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validation.harness import (
    DATA_DIR,
    fresh_session_factory,
    make_api,
    new_professor,
    new_student,
)

from eaal_platform.ai.ollama_provider import OllamaProvider
from eaal_platform.db.models import Session as SessionModel

PROVIDER = OllamaProvider()

# The persona-generation calls below go straight to Ollama (not through the
# app's OllamaProvider, which hardcodes a 60s timeout tuned for a real
# chat reply) because qwen3's "thinking" mode can occasionally run well
# past a minute on an open-ended free-form prompt. The app-facing calls
# (everything routed through `api.*`) still go through the real,
# unmodified OllamaProvider, so production behavior is untouched.
_PERSONA_HTTP_TIMEOUT_SECONDS = 360.0
_PERSONA_CLIENT = httpx.Client(
    base_url="http://localhost:11434", timeout=_PERSONA_HTTP_TIMEOUT_SECONDS
)

_FACTORIAL_LAB = {
    "title": "Recursive Factorial",
    "course": "CS101",
    "division": "A",
    "batch": "2026",
    "topic": "Recursion",
    "description": "Implement a recursive function factorial(n) that returns n! for a non-negative integer n, and print factorial(6).",
    "difficulty": "Easy",
    "stages": [
        {"duration_minutes": 15, "ai_assistance_mode": "FULL"},
        {"duration_minutes": 15, "ai_assistance_mode": "FULL"},
        {"duration_minutes": 20, "ai_assistance_mode": "FULL"},
    ],
}

_BINARY_SEARCH_LAB = {
    "title": "Binary Search",
    "course": "CS101",
    "division": "A",
    "batch": "2026",
    "topic": "Searching",
    "description": "Implement binary_search(arr, target) returning the index of target in a sorted list arr, or -1 if absent. Print binary_search([1,3,5,7,9,11], 7).",
    "difficulty": "Medium",
    "stages": [
        {"duration_minutes": 15, "ai_assistance_mode": "FULL"},
        {"duration_minutes": 15, "ai_assistance_mode": "FULL"},
        {"duration_minutes": 20, "ai_assistance_mode": "FULL"},
    ],
}


def _persona_text(instruction: str, retries: int = 3) -> str:
    """One free-form generation call in-character as the persona, with a
    generous timeout and a couple of retries — qwen3's "thinking" mode can
    occasionally run long on an open-ended prompt."""
    last_error: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            response = _PERSONA_CLIENT.post(
                "/api/generate",
                json={"model": "qwen3:8b", "prompt": instruction, "stream": False},
            )
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except httpx.HTTPError as exc:
            last_error = exc
    raise RuntimeError(f"Ollama unavailable after retries: {last_error}")


def _extract_code(text: str) -> str:
    import re

    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def session_1_over_reliant(api, task_id: str) -> dict:
    """Persona: asks for the complete solution immediately, adopts it
    verbatim, offers no real explanation. Expect: weak P1 calibration/
    grounding, weak P3 conceptual understanding."""
    stages = api.get_stages(task_id)["stages"]
    session_id = api.start_stage(stages[2]["id"])["session_id"]

    prompt = (
        "Can you just write the whole factorial function for me? I don't want to think about it."
    )
    reply = api.send_ai_message(session_id, prompt, {"main.py": ""})
    code = (
        _extract_code(reply["text"])
        if reply["available"]
        else "def factorial(n):\n    return 1 if n <= 1 else n * factorial(n - 1)\nprint(factorial(6))"
    )
    if "factorial(6)" not in code:
        code += "\nprint(factorial(6))"

    api.log_code_edit(session_id, {"main.py": code})
    api.run_code(session_id, {"main.py": code})

    explanation = _persona_text(
        "You are a first-year programming student. In one short sentence, "
        "explain why your factorial function works, but be honest that you "
        "don't really understand it and just used what the AI gave you."
    )
    api.submit_concept_check(session_id, explanation)
    api.submit_session(session_id, {"main.py": code})
    return {"session_id": session_id, "label": "Session 1 (Day 0): over-reliant delegation"}


def session_2_developing(api, task_id: str) -> dict:
    """Persona: attempts first, asks a targeted grounded question, modifies
    the suggestion, verifies. Expect: mid-range P1/P2."""
    stages = api.get_stages(task_id)["stages"]
    session_id = api.start_stage(stages[2]["id"])["session_id"]

    partial_attempt = _persona_text(
        "You are a first-year programming student attempting binary search "
        "on your own before asking for help. Write ONLY a Python attempt "
        "(in a ```python code block) that is a genuine but incomplete or "
        "slightly buggy try at binary_search(arr, target) - do not solve it "
        "perfectly."
    )
    partial_code = _extract_code(partial_attempt)
    api.log_code_edit(session_id, {"main.py": partial_code})
    api.run_code(session_id, {"main.py": partial_code})

    prompt = (
        "I wrote this so far:\n" + partial_code + "\nI think my loop condition or "
        "midpoint update might be wrong and it's not finding the target correctly. "
        "What's off?"
    )
    reply = api.send_ai_message(session_id, prompt, {"main.py": partial_code})
    ai_code = _extract_code(reply["text"]) if reply["available"] else partial_code

    final_code = (
        "def binary_search(arr, target):\n"
        "    lo, hi = 0, len(arr) - 1\n"
        "    while lo <= hi:\n"
        "        mid = (lo + hi) // 2\n"
        "        if arr[mid] == target:\n"
        "            return mid\n"
        "        elif arr[mid] < target:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid - 1\n"
        "    return -1\n"
        "print(binary_search([1, 3, 5, 7, 9, 11], 7))"
    )
    api.log_code_edit(session_id, {"main.py": final_code})
    api.run_code(session_id, {"main.py": final_code})
    _ = ai_code

    explanation = _persona_text(
        "You are a first-year programming student. In 2-3 sentences, explain "
        "why binary search works and why you update the midpoint the way you "
        "do - show real but not expert-level understanding."
    )
    api.submit_concept_check(session_id, explanation)
    api.submit_session(session_id, {"main.py": final_code})
    return {"session_id": session_id, "label": "Session 2 (Day 3): developing calibration"}


def session_3_transfer(api, source_task_id: str, professor_email: str, student_email: str) -> dict:
    """Transfer Task off the factorial Lab: recursive digit-sum instead of
    recursive factorial - same underlying concept (base case + recursive
    reduction), different surface problem. Persona: mostly independent."""
    from validation.harness import login_as_professor, login_as_student

    login_as_professor(api, professor_email)
    transfer_task_id = api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "TRANSFER",
            "title": "Recursive Factorial - Transfer Task",
            "description": "Implement a recursive function digit_sum(n) that returns the sum of the digits of a non-negative integer n, and print digit_sum(12345).",
            "ai_assistance_mode": "RESTRICTED",
            "duration_minutes": 15,
        }
    )["task_id"]
    login_as_student(api, student_email)

    followups = api.get_followup_assessments(source_task_id)
    stage_id = next(f["stage_id"] for f in followups if f["assessment_kind"] == "TRANSFER")
    session_id = api.start_stage(stage_id)["session_id"]

    attempt = _persona_text(
        "You are a first-year programming student who has gotten noticeably "
        "better at recursion. Write ONLY a correct Python solution (in a "
        "```python code block) for digit_sum(n): sum of digits of a "
        "non-negative integer n, using recursion, and end with "
        "print(digit_sum(12345))."
    )
    code = _extract_code(attempt)
    if "digit_sum(12345)" not in code:
        code += "\nprint(digit_sum(12345))"
    api.log_code_edit(session_id, {"main.py": code})
    api.run_code(session_id, {"main.py": code})

    explanation = _persona_text(
        "You are a first-year programming student. In 2-3 sentences, explain "
        "the general recursive pattern (base case plus reducing the problem) "
        "that both factorial and digit-sum share, showing you understand the "
        "underlying idea, not just this one function."
    )
    api.submit_concept_check(session_id, explanation)
    api.submit_session(session_id, {"main.py": code})
    _ = transfer_task_id
    return {
        "session_id": session_id,
        "label": "Session 3 (Day 7): transfer task, mostly independent",
    }


def session_4_retention(
    api, source_task_id: str, professor_email: str, student_email: str, session_factory
) -> dict:
    """Retention Check, AI mode NONE, backdated 35 days past the original
    factorial session so the real >=24h delay gate is satisfied with a
    generous margin."""
    from validation.harness import login_as_professor, login_as_student

    login_as_professor(api, professor_email)
    api.create_followup_assessment(
        {
            "source_task_id": source_task_id,
            "kind": "RETENTION",
            "title": "Recursive Factorial - Retention Check",
            "description": "Without AI help, re-implement factorial(n) recursively and print factorial(6).",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 15,
        }
    )
    login_as_student(api, student_email)
    followups = api.get_followup_assessments(source_task_id)
    stage_id = next(f["stage_id"] for f in followups if f["assessment_kind"] == "RETENTION")
    session_id = api.start_stage(stage_id)["session_id"]

    attempt = _persona_text(
        "You are a first-year programming student, weeks later, recalling "
        "recursion without any AI help. Write ONLY a correct Python solution "
        "(in a ```python code block) for factorial(n) recursively, ending "
        "with print(factorial(6))."
    )
    code = _extract_code(attempt)
    if "factorial(6)" not in code:
        code += "\nprint(factorial(6))"
    api.log_code_edit(session_id, {"main.py": code})
    api.run_code(session_id, {"main.py": code})

    explanation = _persona_text(
        "You are a first-year programming student. In 2-3 sentences, "
        "confidently explain why your recursive factorial function works, "
        "weeks after first learning it, without referencing any AI help."
    )
    api.submit_concept_check(session_id, explanation)
    api.submit_session(session_id, {"main.py": code})

    return {"session_id": session_id, "label": "Session 4 (Day 35): retention check, unaided"}


def pillar_means(results: dict) -> dict[str, float | None]:
    pillars = {
        "P1": ["S1.1", "S1.2", "S1.3", "S1.4", "S1.5", "S1.6"],
        "P2": ["S2.1", "S2.2", "S2.3", "S2.4"],
        "P3": ["S3.1", "S3.2", "S3.3", "S3.4"],
    }
    out: dict[str, float | None] = {}
    for pillar, keys in pillars.items():
        values = [results[k].value for k in keys if results[k].value is not None]
        out[pillar] = sum(values) / len(values) if values else None
    return out


def main() -> None:
    if not PROVIDER.ping():
        print("Ollama is not reachable - aborting pilot simulation.")
        sys.exit(1)

    session_factory = fresh_session_factory()
    api = make_api(session_factory, PROVIDER)
    professor_email = new_professor(session_factory, api)
    factorial_task_id = api.create_lab(_FACTORIAL_LAB)["task_id"]
    binary_search_task_id = api.create_lab(_BINARY_SEARCH_LAB)["task_id"]
    student_email = new_student(session_factory, api)

    from eaal_platform.signals.compute import compute_all_signals

    timeline = []

    print("Session 1: over-reliant delegation ...")
    s1 = session_1_over_reliant(api, factorial_task_id)
    timeline.append(s1)

    print("Session 2: developing calibration ...")
    s2 = session_2_developing(api, binary_search_task_id)
    timeline.append(s2)

    print("Session 3: transfer task ...")
    s3 = session_3_transfer(api, factorial_task_id, professor_email, student_email)
    timeline.append(s3)

    print("Session 4: retention check (backdating original session 35 days) ...")
    with session_factory() as db_session:
        original = db_session.get(SessionModel, s1["session_id"])
        assert original is not None
        original.started_at = datetime.now(UTC) - timedelta(days=35)
        db_session.commit()
    s4 = session_4_retention(
        api, factorial_task_id, professor_email, student_email, session_factory
    )
    timeline.append(s4)

    print("\nComputing signals for every session ...")
    trajectory = []
    for point in timeline:
        results = compute_all_signals(session_factory, point["session_id"], PROVIDER)
        means = pillar_means(results)
        trajectory.append(
            {
                "label": point["label"],
                "session_id": point["session_id"],
                "pillar_means": means,
                "signals": {k: {"value": v.value, "reason": v.reason} for k, v in results.items()},
            }
        )
        print(f"  {point['label']}: P1={means['P1']}, P2={means['P2']}, P3={means['P3']}")

    import json

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "pilot_simulation_trajectory.json").write_text(json.dumps(trajectory, indent=2))
    print(f"\nWrote {DATA_DIR / 'pilot_simulation_trajectory.json'}")


if __name__ == "__main__":
    main()
