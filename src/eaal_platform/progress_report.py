"""Long-run learning summary for one student, built from their scored sessions.

Pure functions over plain dicts so the aggregation is testable without a
database; ``CavyApi.get_my_progress`` gathers the rows and calls
``build_progress``. As elsewhere, the overall number is a provisional
equal-weight average of whichever signals have a value, not a validated
score.
"""

from __future__ import annotations

from typing import Any, TypedDict

PILLAR_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("P1 · AI Utilization", ("S1.1", "S1.2", "S1.3", "S1.4", "S1.5", "S1.6")),
    ("P2 · Cognitive Engagement", ("S2.1", "S2.2", "S2.3", "S2.4")),
    ("P3 · Learning & Knowledge Development", ("S3.1", "S3.2", "S3.3", "S3.4")),
)

_TREND_WINDOW = 3
_TREND_DEADBAND = 3.0  # points; smaller moves are noise, reported as "steady"
_MAX_HIGHLIGHTS = 3


class SessionRow(TypedDict):
    session_id: int
    title: str
    kind: str  # "lab" | "practice" | "followup"
    stage: str | None
    submitted_at: str
    minutes: int | None
    ai_interactions: int
    signals: dict[str, float | None]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _score(signals: dict[str, float | None], keys: tuple[str, ...] | None = None) -> float | None:
    chosen = signals if keys is None else {k: signals.get(k) for k in keys}
    values = [v for v in chosen.values() if v is not None]
    return round(_mean(values) * 100, 1) if values else None


def _trend(scores: list[float]) -> dict[str, Any]:
    """Compare the latest few scores with the few before them."""
    if len(scores) < 2:
        return {"direction": "not_enough_data", "change": None}
    recent = scores[-_TREND_WINDOW:]
    earlier = scores[: len(scores) - len(recent)][-_TREND_WINDOW:]
    if not earlier:
        # Only a handful of sessions: compare the last with the first.
        earlier, recent = scores[:1], scores[-1:]
    change = round(_mean(recent) - _mean(earlier), 1)
    if change > _TREND_DEADBAND:
        direction = "up"
    elif change < -_TREND_DEADBAND:
        direction = "down"
    else:
        direction = "steady"
    return {"direction": direction, "change": change}


def build_progress(rows: list[SessionRow]) -> dict[str, Any]:
    """Summarise a student's submitted sessions for the dashboard."""
    rows = sorted(rows, key=lambda r: r["submitted_at"])  # oldest first
    scores = [_score(row["signals"]) for row in rows]
    history = [
        {
            "session_id": row["session_id"],
            "title": row["title"],
            "kind": row["kind"],
            "stage": row["stage"],
            "submitted_at": row["submitted_at"],
            "minutes": row["minutes"],
            "ai_interactions": row["ai_interactions"],
            "score": score,
        }
        for row, score in zip(rows, scores, strict=True)
    ]
    scored = [s for s in scores if s is not None]

    pillars = []
    for heading, keys in PILLAR_SIGNALS:
        per_session = [_score(row["signals"], keys) for row in rows]
        series = [v for v in per_session if v is not None]
        pillars.append(
            {
                "heading": heading,
                "latest": series[-1] if series else None,
                "average": round(_mean(series), 1) if series else None,
                "series": series,
                **_trend(series),
            }
        )

    signal_means: dict[str, float] = {}
    for _, keys in PILLAR_SIGNALS:
        for key in keys:
            values = [v for row in rows if (v := row["signals"].get(key)) is not None]
            if values:
                signal_means[key] = round(_mean(values) * 100, 1)
    ranked = sorted(signal_means.items(), key=lambda item: item[1], reverse=True)
    strengths = [{"key": k, "score": v} for k, v in ranked[:_MAX_HIGHLIGHTS]]
    # Don't list the same signal as both a strength and a growth area.
    strength_keys = {s["key"] for s in strengths}
    growth = [{"key": k, "score": v} for k, v in reversed(ranked) if k not in strength_keys][
        :_MAX_HIGHLIGHTS
    ]

    minutes = [row["minutes"] for row in rows if row["minutes"] is not None]
    return {
        "totals": {
            "sessions": len(rows),
            "labs": len({row["title"] for row in rows if row["kind"] == "lab"}),
            "practice": sum(1 for row in rows if row["kind"] == "practice"),
            "minutes": sum(minutes),
            "average_score": round(_mean(scored), 1) if scored else None,
            "latest_score": scored[-1] if scored else None,
        },
        "overall": {"series": scored, **_trend(scored)},
        "pillars": pillars,
        "strengths": strengths,
        "growth_areas": growth,
        "history": list(reversed(history)),  # newest first for the table
    }
