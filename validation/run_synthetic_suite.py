"""Runs every synthetic scenario, computes all 14 signals for each
resulting session, and checks determinism (same session recomputed N
times must give bit-identical results for every non-LLM signal).

Writes ``data/synthetic_trials.json`` — one row per (trial, signal-of-interest)
— for ``analyze.py`` to consume. Run this before ``analyze.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validation.harness import cleanup_scratch_db, compute, fresh_session_factory, save_json
from validation.scenarios import pillar1_ai_utilization as p1
from validation.scenarios import pillar2_cognitive_engagement as p2
from validation.scenarios import pillar3_learning_development as p3

_DETERMINISM_REPEATS = 5


def main() -> None:
    session_factory = fresh_session_factory()

    trials = [
        *p1.build_all(session_factory),
        *p2.build_all(session_factory),
        *p3.build_all(session_factory),
    ]
    print(f"Built {len(trials)} synthetic trials across all three pillars.")

    rows: list[dict] = []
    for trial in trials:
        repeats = [
            compute(session_factory, trial.session_id, trial.provider)
            for _ in range(_DETERMINISM_REPEATS)
        ]
        first = repeats[0][trial.signal]
        deterministic = all(
            (r[trial.signal].value == first.value) and (r[trial.signal].reason == first.reason)
            for r in repeats[1:]
        )
        result = first
        rows.append(
            {
                "signal": trial.signal,
                "scenario": trial.scenario,
                "design": trial.design,
                "session_id": trial.session_id,
                "computed_value": result.value,
                "computed_reason": result.reason,
                "computed_evidence": result.evidence,
                "notes": trial.notes,
                "deterministic_across_5_runs": deterministic,
            }
        )

    non_deterministic = [r for r in rows if not r["deterministic_across_5_runs"]]
    path = save_json("synthetic_trials.json", rows)
    print(f"Wrote {len(rows)} rows to {path}")
    if non_deterministic:
        print(f"WARNING: {len(non_deterministic)} trials were NOT deterministic across repeats:")
        for r in non_deterministic:
            print(f"  {r['signal']} / {r['scenario']}")
    else:
        print("All trials were perfectly deterministic across 5 repeated computations.")

    cleanup_scratch_db()


if __name__ == "__main__":
    main()
