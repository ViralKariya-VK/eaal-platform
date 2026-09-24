"""Statistical analysis of the synthetic-trial data (and, if present, the
real-LLM reliability data) — descriptive stats, known-answer accuracy,
gating-logic verification, monotonicity, and determinism — plus the
figures referenced by the formal report.

Run after ``run_synthetic_suite.py`` (and, ideally, ``run_llm_reliability.py``).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr
from validation.harness import DATA_DIR, FIGURES_DIR, clear_figures, save_json

# -- Palette (dataviz skill's validated default; blue/orange/aqua categorical,
# blue sequential ramp, blue<->red diverging with a gray midpoint) -----------
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
GRAY_MID = "#f0efec"
RED = "#e34948"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

PILLAR_COLOR = {"S1": BLUE, "S2": ORANGE, "S3": AQUA}

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": TEXT_SECONDARY,
        "axes.labelcolor": TEXT_PRIMARY,
        "text.color": TEXT_PRIMARY,
        "xtick.color": TEXT_SECONDARY,
        "ytick.color": TEXT_SECONDARY,
        "axes.grid": True,
        "grid.color": "#e5e3dc",
        "grid.linewidth": 0.6,
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def _pillar_of(signal: str) -> str:
    return signal[:2]


def load_synthetic() -> list[dict]:
    return json.loads((DATA_DIR / "synthetic_trials.json").read_text())


def descriptive_stats(rows: list[dict]) -> dict:
    by_signal: dict[str, list[dict]] = {}
    for r in rows:
        by_signal.setdefault(r["signal"], []).append(r)

    out = {}
    for signal, group in sorted(by_signal.items()):
        values = [r["computed_value"] for r in group if r["computed_value"] is not None]
        none_rows = [r for r in group if r["computed_value"] is None]
        reasons = sorted({r["computed_reason"] for r in none_rows if r["computed_reason"]})
        out[signal] = {
            "n_trials": len(group),
            "n_with_value": len(values),
            "n_none": len(none_rows),
            "mean": statistics.mean(values) if values else None,
            "stdev": statistics.pstdev(values) if len(values) > 1 else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "none_reasons_observed": reasons,
        }
    return out


def known_answer_accuracy(rows: list[dict]) -> dict:
    known = [
        r
        for r in rows
        if r["notes"]
        and ("known closed-form" in r["notes"] or "clamped scripted score" in r["notes"])
    ]
    mismatches = []
    for r in known:
        if r["design"] is None:
            ok = r["computed_value"] is None
        else:
            ok = r["computed_value"] is not None and abs(r["computed_value"] - r["design"]) < 1e-9
        if not ok:
            mismatches.append(r)
    return {
        "n_known_answer_trials": len(known),
        "n_exact_matches": len(known) - len(mismatches),
        "accuracy": (len(known) - len(mismatches)) / len(known) if known else None,
        "mismatches": mismatches,
    }


def gating_logic_check(rows: list[dict]) -> dict:
    """Every trial whose *design* is None represents a deliberately
    constructed "this signal should have no evidence / not apply" case —
    tabulate whether the implementation agreed, with its stated reason."""
    none_designed = [r for r in rows if r["design"] is None]
    wrong = [r for r in none_designed if r["computed_value"] is not None]
    return {
        "n_gating_trials": len(none_designed),
        "n_correctly_none": len(none_designed) - len(wrong),
        "incorrectly_non_none": wrong,
        "cases": [
            {"signal": r["signal"], "scenario": r["scenario"], "reason": r["computed_reason"]}
            for r in none_designed
        ],
    }


def determinism_summary(rows: list[dict]) -> dict:
    non_det = [r for r in rows if not r["deterministic_across_5_runs"]]
    return {
        "n_trials": len(rows),
        "n_deterministic": len(rows) - len(non_det),
        "fraction_deterministic": (len(rows) - len(non_det)) / len(rows),
        "non_deterministic_cases": [
            {"signal": r["signal"], "scenario": r["scenario"]} for r in non_det
        ],
    }


def monotonicity_gradient(rows: list[dict], signal: str) -> dict | None:
    subset = [r for r in rows if r["signal"] == signal and "gradient" in r["scenario"]]
    if len(subset) < 3:
        return None
    designs = [r["design"] for r in subset]
    values = [r["computed_value"] for r in subset]
    rho, p = spearmanr(designs, values)
    return {"signal": signal, "n": len(subset), "spearman_r": float(rho), "p_value": float(p)}


def threshold_accuracy(rows: list[dict], signal: str, threshold: float) -> dict:
    """For a step-function signal (S1.3 gates at similarity=0.15), Spearman
    correlation is the wrong tool — it penalizes a correct, flat plateau
    above the threshold as "no relationship." The right check is simply:
    does every point land on the correct side of the step?"""
    subset = [r for r in rows if r["signal"] == signal and "gradient" in r["scenario"]]
    correct = 0
    wrong = []
    for r in subset:
        expected_high = r["design"] >= threshold
        actual_high = r["computed_value"] == 1.0
        if expected_high == actual_high:
            correct += 1
        else:
            wrong.append(r)
    return {
        "signal": signal,
        "threshold": threshold,
        "n": len(subset),
        "n_correct_side_of_threshold": correct,
        "accuracy": correct / len(subset) if subset else None,
        "misclassified": wrong,
    }


# -- Figures ------------------------------------------------------------------


def fig_similarity_gradient(rows: list[dict]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, signal, color, title in (
        (axes[0], "S1.3", BLUE, "S1.3 Response Utilization"),
        (axes[1], "S2.3", ORANGE, "S2.3 Problem-Solving Agency"),
    ):
        subset = sorted(
            (r for r in rows if r["signal"] == signal and "gradient" in r["scenario"]),
            key=lambda r: r["design"],
        )
        x = [r["design"] for r in subset]
        y = [r["computed_value"] for r in subset]
        ax.plot(x, y, "o-", color=color, linewidth=2, markersize=6)
        if signal == "S1.3":
            ax.axvline(0.15, color=TEXT_SECONDARY, linestyle="--", linewidth=1)
            ax.text(0.16, 0.5, "threshold = 0.15", fontsize=9, color=TEXT_SECONDARY)
        ax.set_xlabel("measured text similarity (AI code vs. student's edit)")
        ax.set_ylabel("computed signal value")
        ax.set_title(title, fontsize=12, loc="left")
        ax.set_ylim(-0.05, 1.05)
    fig.suptitle(
        "Both signals respond monotonically to the same underlying similarity gradient",
        fontsize=11,
        color=TEXT_SECONDARY,
        y=1.03,
    )
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "similarity_gradient.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def fig_independent_initiation_heatmap(rows: list[dict]) -> None:
    subset = [r for r in rows if r["signal"] == "S2.1" and r["scenario"].startswith("edits=")]
    edits_vals = sorted({int(r["scenario"].split("_")[0].split("=")[1]) for r in subset})
    runs_vals = sorted({int(r["scenario"].split("_")[1].split("=")[1]) for r in subset})
    grid = np.full((len(edits_vals), len(runs_vals)), np.nan)
    for r in subset:
        e = int(r["scenario"].split("_")[0].split("=")[1])
        run = int(r["scenario"].split("_")[1].split("=")[1])
        grid[edits_vals.index(e), runs_vals.index(run)] = r["computed_value"]

    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    im = ax.imshow(grid, cmap=cmap, vmin=0, vmax=1, aspect="auto", origin="lower")
    ax.set_xticks(range(len(runs_vals)))
    ax.set_xticklabels(runs_vals)
    ax.set_yticks(range(len(edits_vals)))
    ax.set_yticklabels(edits_vals)
    ax.set_xlabel("runs before the AI prompt")
    ax.set_ylabel("edits before the AI prompt")
    ax.set_title("S2.1 Independent Initiation: score surface", fontsize=12, loc="left")
    ax.grid(False)
    for i in range(len(edits_vals)):
        for j in range(len(runs_vals)):
            v = grid[i, j]
            if not np.isnan(v):
                ax.text(
                    j,
                    i,
                    f"{v:.2f}",
                    ha="center",
                    va="center",
                    color="white" if v > 0.55 else TEXT_PRIMARY,
                    fontsize=9,
                )
    cbar = fig.colorbar(im, ax=ax, shrink=0.85)
    cbar.set_label("computed signal value")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "independent_initiation_heatmap.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def fig_signal_distributions(stats: dict) -> None:
    signals = [s for s, d in stats.items() if d["n_with_value"] > 0]
    means = [stats[s]["mean"] for s in signals]
    mins = [stats[s]["min"] for s in signals]
    maxs = [stats[s]["max"] for s in signals]
    colors = [PILLAR_COLOR[_pillar_of(s)] for s in signals]

    fig, ax = plt.subplots(figsize=(9, 5))
    y = np.arange(len(signals))
    for i, (mn, mx, mean, c) in enumerate(zip(mins, maxs, means, colors, strict=True)):
        ax.plot([mn, mx], [i, i], color=c, linewidth=3, solid_capstyle="round", alpha=0.55)
        ax.plot(mean, i, "o", color=c, markersize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(signals)
    ax.set_xlabel("computed signal value across all synthetic trials (min-max range, dot = mean)")
    ax.set_title(
        "Range and mean of each signal's computed value across its synthetic trials",
        fontsize=12,
        loc="left",
    )
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-1.2, len(signals) - 0.3)
    from matplotlib.lines import Line2D

    handles = [
        Line2D([0], [0], color=BLUE, lw=3, label="Pillar 1 - AI Utilization"),
        Line2D([0], [0], color=ORANGE, lw=3, label="Pillar 2 - Cognitive Engagement"),
        Line2D([0], [0], color=AQUA, lw=3, label="Pillar 3 - Learning & Knowledge Dev."),
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=3,
        frameon=False,
        fontsize=9,
    )
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "signal_value_ranges.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


_SHORT_LABELS = {
    "explicit_solution_request_no_attempt": "explicit solution\nrequest, no attempt",
    "bare_vague_request": "bare vague\nrequest",
    "preliminary_concept_question": "preliminary\nconcept question",
    "reasoning_check_no_code": "reasoning check\n(no code)",
    "specific_bug_with_code": "specific bug,\nwith code",
    "error_traceback_with_stderr": "real error\ntraceback",
    "premature_full_solution_with_no_code_shown": "premature solution\nrequest (v2)",
    "bare_greeting": "bare greeting",
    "rigorous_explanation": "rigorous\nexplanation",
    "core_idea_less_rigorous": "core idea,\nless rigorous",
    "surface_line_by_line_restatement": "surface line-by-line\nrestatement",
    "vague_gesture_at_mechanism": "vague gesture\nat mechanism",
    "no_understanding_admitted": "no understanding\nadmitted",
    "confidently_wrong": "confidently\nwrong",
}


def fig_llm_reliability(llm_raw_help: list[dict], llm_raw_concept: list[dict]) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5), gridspec_kw={"wspace": 0.75})
    specs = [
        (
            axes[0],
            llm_raw_help,
            "calibration_scores",
            "expected_calibration_rank",
            BLUE,
            "S1.1 Help-Seeking Calibration",
        ),
        (
            axes[1],
            llm_raw_help,
            "grounding_scores",
            "expected_grounding_rank",
            ORANGE,
            "S1.2 Student Grounding",
        ),
        (
            axes[2],
            llm_raw_concept,
            "scores",
            "expected_rank",
            AQUA,
            "S3.1 Conceptual Understanding",
        ),
    ]
    for ax, raw, score_key, rank_key, color, title in specs:
        ordered = sorted(raw, key=lambda r: r[rank_key])
        labels = [_SHORT_LABELS.get(r["label"], r["label"]) for r in ordered]
        for i, r in enumerate(ordered):
            scores = r[score_key]
            if not scores:
                continue
            ax.plot(
                [min(scores), max(scores)],
                [i, i],
                color=color,
                linewidth=3,
                alpha=0.4,
                solid_capstyle="round",
            )
            ax.plot(statistics.mean(scores), i, "o", color=color, markersize=7, zorder=3)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels, fontsize=8.5)
        ax.set_ylim(-0.7, len(labels) - 0.3)
        ax.set_xlim(-0.05, 1.05)
        ax.set_title(title, fontsize=11, loc="left", pad=10)
        ax.invert_yaxis()
        ax.tick_params(axis="y", length=0)
    fig.supxlabel(
        "model score on repeat calls (dot = mean of 5 repeats, bar = min-max range)",
        fontsize=10,
        color=TEXT_SECONDARY,
    )
    fig.suptitle(
        "Live model (qwen3:8b) vs. one researcher's expected ranking, top = expected lowest score",
        fontsize=11.5,
        color=TEXT_SECONDARY,
        y=1.02,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(FIGURES_DIR / "llm_reliability_face_validity.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def fig_case_study_trajectory(trajectory: list[dict]) -> None:
    """P1/P2/P3 pillar means across the synthetic single-agent case study's
    sessions — a break in a line means that pillar had no computable
    evidence in that session (e.g. P1 once the persona stops using AI at
    all), not a zero score."""
    labels = [p["label"].split(":")[0].strip() for p in trajectory]
    x = list(range(len(trajectory)))
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    for pillar, color in (("P1", BLUE), ("P2", ORANGE), ("P3", AQUA)):
        y = [p["pillar_means"][pillar] for p in trajectory]
        xs_present = [xi for xi, yi in zip(x, y, strict=True) if yi is not None]
        ys_present = [yi for yi in y if yi is not None]
        ax.plot(xs_present, ys_present, "o-", color=color, linewidth=2, markersize=7, label=pillar)
        for xi, yi in zip(x, y, strict=True):
            if yi is None:
                ax.plot(xi, 0.0, "x", color=color, markersize=8, alpha=0.35)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylim(-0.05, 1.05)
    ax.set_ylabel("pillar mean (of that session's computable signals)")
    ax.set_title(
        "Synthetic single-agent case study: EAAL_t trajectory (n=1 AI persona, not learner data)",
        fontsize=10.5,
        loc="left",
    )
    from matplotlib.lines import Line2D

    handles = [
        Line2D([0], [0], color=BLUE, marker="o", label="P1 - AI Utilization"),
        Line2D([0], [0], color=ORANGE, marker="o", label="P2 - Cognitive Engagement"),
        Line2D([0], [0], color=AQUA, marker="o", label="P3 - Learning & Knowledge Dev."),
        Line2D(
            [0],
            [0],
            color=TEXT_SECONDARY,
            marker="x",
            linestyle="none",
            label="no evidence this session",
        ),
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=2,
        frameon=False,
        fontsize=8.5,
    )
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "case_study_trajectory.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    rows = load_synthetic()
    clear_figures()

    stats = descriptive_stats(rows)
    accuracy = known_answer_accuracy(rows)
    gating = gating_logic_check(rows)
    determinism = determinism_summary(rows)
    monotonicity = [m for m in (monotonicity_gradient(rows, "S2.3"),) if m is not None]
    s13_threshold = threshold_accuracy(rows, "S1.3", 0.15)

    fig_similarity_gradient(rows)
    fig_independent_initiation_heatmap(rows)
    fig_signal_distributions(stats)

    llm_summary = None
    llm_help_raw: list[dict] = []
    llm_concept_raw: list[dict] = []
    if (DATA_DIR / "llm_reliability_summary.json").exists():
        llm_summary = json.loads((DATA_DIR / "llm_reliability_summary.json").read_text())
        llm_help_raw = json.loads((DATA_DIR / "llm_help_seeking_raw.json").read_text())
        llm_concept_raw = json.loads((DATA_DIR / "llm_concept_understanding_raw.json").read_text())
        fig_llm_reliability(llm_help_raw, llm_concept_raw)

    case_study_trajectory = None
    if (DATA_DIR / "pilot_simulation_trajectory.json").exists():
        case_study_trajectory = json.loads(
            (DATA_DIR / "pilot_simulation_trajectory.json").read_text()
        )
        fig_case_study_trajectory(case_study_trajectory)

    analysis = {
        "descriptive_stats": stats,
        "known_answer_accuracy": accuracy,
        "gating_logic_check": gating,
        "determinism": determinism,
        "monotonicity": monotonicity,
        "s1_3_threshold_accuracy": s13_threshold,
        "llm_reliability_and_face_validity": llm_summary,
    }
    path = save_json("analysis_summary.json", analysis)
    print(f"Wrote {path}")
    print(f"Figures written to {FIGURES_DIR}")

    print("\n--- Headline results ---")
    print(
        f"Known-answer accuracy: {accuracy['n_exact_matches']}/{accuracy['n_known_answer_trials']}"
    )
    print(f"Gating-logic correctness: {gating['n_correctly_none']}/{gating['n_gating_trials']}")
    print(f"Determinism: {determinism['n_deterministic']}/{determinism['n_trials']}")
    for m in monotonicity:
        print(
            f"Monotonicity {m['signal']}: Spearman r={m['spearman_r']:.4f}, "
            f"p={m['p_value']:.2e}, n={m['n']}"
        )
    n_correct = s13_threshold["n_correct_side_of_threshold"]
    print(f"S1.3 threshold accuracy: {n_correct}/{s13_threshold['n']}")
    if llm_summary:
        for key, label in (
            ("help_seeking_calibration", "S1.1"),
            ("student_grounding", "S1.2"),
            ("conceptual_understanding", "S3.1"),
        ):
            fv = llm_summary[key]["face_validity"]
            rel = llm_summary[key]["reliability"]
            print(
                f"{label}: face-validity Spearman r={fv['spearman_r']}, p={fv.get('p_value')}; "
                f"mean repeat-call stdev={rel['mean_stdev_across_examples']}"
            )


if __name__ == "__main__":
    main()
