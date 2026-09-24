"""Real-model validation for the three LLM-rubric signals (S1.1, S1.2, S3.1).

Everything else in this suite uses a scripted provider to isolate "does
the surrounding code do what it claims" from "is the model's judgment any
good" — this script is the one place that actually calls the live local
model (Ollama / qwen3:8b, the app's real configured provider) with the
app's real rubric prompts, to measure two things a scripted provider
structurally cannot:

1. Test-retest reliability: call the exact same prompt N times and look
   at the spread of scores. The app pins temperature=0.1 for rubric
   scoring specifically to keep this low (see ``ollama_provider.py``) —
   this measures whether that actually holds.
2. Face validity: does the model's score track a human reader's judgment
   of the same rubric, on a small hand-labeled example set (see
   ``labeled_examples.py``)? This is the one part of the whole suite that
   bears on whether the *rubric signals* measure what they claim to —
   everything else validates the code around them.

Slow (real inference, ~5s/call): budget several minutes to run.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scipy.stats import spearmanr
from validation.harness import DATA_DIR
from validation.labeled_examples import (
    CONCEPT_UNDERSTANDING_EXAMPLES,
    HELP_SEEKING_EXAMPLES,
)

from eaal_platform.ai.ollama_provider import OllamaProvider
from eaal_platform.ai.provider import GenerationContext, Purpose
from eaal_platform.signals.compute import (
    _CONCEPT_CHECK_RUBRIC_INSTRUCTION,
    _RUBRIC_INSTRUCTION,
    _clamp01,
)

N_REPEATS = 5


def _call_rubric(provider: OllamaProvider, prompt: str, context: GenerationContext) -> dict | None:
    result = provider.generate(prompt, context, Purpose.RUBRIC_SCORING)
    if not result.available:
        return None
    try:
        return json.loads(result.text)
    except json.JSONDecodeError:
        return None


def run_help_seeking(provider: OllamaProvider) -> list[dict]:
    rows = []
    for i, example in enumerate(HELP_SEEKING_EXAMPLES):
        prompt = _RUBRIC_INSTRUCTION.format(message=example.message)
        calibration_scores: list[float] = []
        grounding_scores: list[float] = []
        parse_failures = 0
        total = len(HELP_SEEKING_EXAMPLES)
        for r in range(N_REPEATS):
            print(f"[{i + 1}/{total}] {example.label} repeat {r + 1}/{N_REPEATS}", flush=True)
            data = _call_rubric(provider, prompt, example.context)
            if (
                data is None
                or "help_seeking_calibration" not in data
                or "student_grounding" not in data
            ):
                parse_failures += 1
                continue
            try:
                calibration_scores.append(_clamp01(float(data["help_seeking_calibration"])))
                grounding_scores.append(_clamp01(float(data["student_grounding"])))
            except (TypeError, ValueError):
                parse_failures += 1
        rows.append(
            {
                "label": example.label,
                "message": example.message,
                "expected_calibration_rank": example.expected_calibration_rank,
                "expected_grounding_rank": example.expected_grounding_rank,
                "rationale": example.rationale,
                "calibration_scores": calibration_scores,
                "grounding_scores": grounding_scores,
                "parse_failures": parse_failures,
            }
        )
    return rows


def run_concept_understanding(provider: OllamaProvider) -> list[dict]:
    rows = []
    for i, example in enumerate(CONCEPT_UNDERSTANDING_EXAMPLES):
        prompt = _CONCEPT_CHECK_RUBRIC_INSTRUCTION.format(
            task_description=example.task_description, response=example.response_text
        )
        context = GenerationContext(task_description=example.task_description)
        scores: list[float] = []
        parse_failures = 0
        total = len(CONCEPT_UNDERSTANDING_EXAMPLES)
        for r in range(N_REPEATS):
            print(f"[{i + 1}/{total}] {example.label} repeat {r + 1}/{N_REPEATS}", flush=True)
            data = _call_rubric(provider, prompt, context)
            if data is None or "conceptual_understanding" not in data:
                parse_failures += 1
                continue
            try:
                scores.append(_clamp01(float(data["conceptual_understanding"])))
            except (TypeError, ValueError):
                parse_failures += 1
        rows.append(
            {
                "label": example.label,
                "response_text": example.response_text,
                "expected_rank": example.expected_rank,
                "rationale": example.rationale,
                "scores": scores,
                "parse_failures": parse_failures,
            }
        )
    return rows


def _reliability_stats(all_score_lists: list[list[float]]) -> dict:
    sds = [statistics.pstdev(s) for s in all_score_lists if len(s) > 1]
    ranges = [max(s) - min(s) for s in all_score_lists if s]
    return {
        "mean_stdev_across_examples": statistics.mean(sds) if sds else None,
        "max_stdev_across_examples": max(sds) if sds else None,
        "mean_range_across_examples": statistics.mean(ranges) if ranges else None,
        "max_range_across_examples": max(ranges) if ranges else None,
        "n_examples_with_repeats": len(sds),
    }


def _face_validity(expected_ranks: list[int], mean_scores: list[float]) -> dict:
    if len(set(expected_ranks)) < 2 or all(s == mean_scores[0] for s in mean_scores):
        return {"spearman_r": None, "p_value": None, "note": "insufficient variance to compute"}
    rho, p = spearmanr(expected_ranks, mean_scores)
    return {"spearman_r": float(rho), "p_value": float(p)}


def main() -> None:
    provider = OllamaProvider()
    if not provider.ping():
        print("Ollama is not reachable at localhost:11434 - aborting LLM validation.")
        sys.exit(1)
    print(f"Using live provider: {provider.provider_name} / {provider.model_name}")

    help_seeking_rows = run_help_seeking(provider)
    concept_rows = run_concept_understanding(provider)

    calibration_means = [
        statistics.mean(r["calibration_scores"])
        for r in help_seeking_rows
        if r["calibration_scores"]
    ]
    grounding_means = [
        statistics.mean(r["grounding_scores"]) for r in help_seeking_rows if r["grounding_scores"]
    ]
    concept_means = [statistics.mean(r["scores"]) for r in concept_rows if r["scores"]]

    calibration_ranks = [
        r["expected_calibration_rank"] for r in help_seeking_rows if r["calibration_scores"]
    ]
    grounding_ranks = [
        r["expected_grounding_rank"] for r in help_seeking_rows if r["grounding_scores"]
    ]
    concept_ranks = [r["expected_rank"] for r in concept_rows if r["scores"]]

    summary = {
        "n_repeats_per_example": N_REPEATS,
        "model": provider.model_name,
        "help_seeking_calibration": {
            "reliability": _reliability_stats([r["calibration_scores"] for r in help_seeking_rows]),
            "face_validity": _face_validity(calibration_ranks, calibration_means),
            "total_parse_failures": sum(r["parse_failures"] for r in help_seeking_rows),
            "total_calls": len(help_seeking_rows) * N_REPEATS,
        },
        "student_grounding": {
            "reliability": _reliability_stats([r["grounding_scores"] for r in help_seeking_rows]),
            "face_validity": _face_validity(grounding_ranks, grounding_means),
            "total_parse_failures": sum(r["parse_failures"] for r in help_seeking_rows),
            "total_calls": len(help_seeking_rows) * N_REPEATS,
        },
        "conceptual_understanding": {
            "reliability": _reliability_stats([r["scores"] for r in concept_rows]),
            "face_validity": _face_validity(concept_ranks, concept_means),
            "total_parse_failures": sum(r["parse_failures"] for r in concept_rows),
            "total_calls": len(concept_rows) * N_REPEATS,
        },
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "llm_help_seeking_raw.json").write_text(json.dumps(help_seeking_rows, indent=2))
    (DATA_DIR / "llm_concept_understanding_raw.json").write_text(json.dumps(concept_rows, indent=2))
    (DATA_DIR / "llm_reliability_summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print("\nWrote llm_help_seeking_raw.json, llm_concept_understanding_raw.json,")
    print("llm_reliability_summary.json")


if __name__ == "__main__":
    main()
