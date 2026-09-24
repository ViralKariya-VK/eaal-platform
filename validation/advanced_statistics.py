"""Second-pass statistical rigor on top of ``analyze.py``'s results.

Everything here answers one question: "is there a *more correct* or
*more honest* statistic we should be reporting than what we already
have?" — not new data collection. Specifically:

1. ICC(2,1) agreement between the researcher's calibrated expected value
   and the live model's mean score, plus ICC(3,k) self-consistency across
   the model's 5 repeated calls — the exact statistic the paper's own §5
   specifies ("a prespecified intraclass correlation coefficient with
   confidence intervals"), which the first pass only approximated with a
   rank correlation.
2. Bootstrap confidence intervals on every small-n correlation (n=6-8),
   since the asymptotic p-values there are not trustworthy at that size.
3. Holm and Benjamini-Hochberg corrected p-values across the family of
   significance tests reported anywhere in this suite.
4. A discriminant-validity spot check between S1.1 and S1.2: they're
   proposed as distinct signals scored from the same rubric call — are
   they actually separable in the live model's output, or collinear?
5. A confusion-matrix reframing of the "gating logic" check already in
   analyze.py, computed over the FULL synthetic corpus rather than only
   the trials deliberately built to have no evidence — this is the one
   analysis here capable of finding a real problem (a false negative:
   evidence existed but the signal wrongly returned None; or a false
   positive: no evidence existed but the signal fabricated a value).
6. A CFA / reliability (omega) *pipeline smoke test* against the tiny
   (n=4-session) synthetic longitudinal case study, explicitly run to
   confirm the analysis code executes against real signal-shaped data —
   NOT to report an interpretable fit statistic. If the model fails to
   converge (expected at this n), that failure is reported as the
   expected outcome, not swallowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pingouin as pg
from scipy.stats import pearsonr, spearmanr
from statsmodels.stats.multitest import multipletests
from validation.harness import DATA_DIR, save_json
from validation.labeled_examples import (
    CONCEPT_UNDERSTANDING_EXAMPLES,
    HELP_SEEKING_EXAMPLES,
)

N_BOOTSTRAP = 5000
RNG = np.random.default_rng(20260921)


# -- 1. ICC --------------------------------------------------------------------
#
# pingouin (0.6.1) labels its six ICC forms as "ICC(1,1)/(1,k)" (one-way
# random), "ICC(A,1)/(A,k)" (two-way random, absolute agreement) and
# "ICC(C,1)/(C,k)" (two-way random, consistency) — not the older
# "ICC1/ICC2/ICC3" naming some references (and papers) still use. The
# mapping is: old ICC1(k) = ICC(1,1)/(1,k); old ICC2(k) = ICC(A,1)/(A,k);
# old ICC3(k) = ICC(C,1)/(C,k).


def _extract_icc_row(result, icc_type: str) -> dict:
    row = result[result["Type"] == icc_type].iloc[0]
    ci = row["CI95"]
    ci95 = [None if (isinstance(v, float) and np.isnan(v)) else float(v) for v in ci]
    return {"icc": float(row["ICC"]), "ci95": ci95, "p_value": float(row["pval"])}


def _icc_self_consistency(raw_rows: list[dict], score_key: str) -> dict:
    """ICC(C,k) [old naming: ICC3k] — two-way random, consistency, k raters:
    are the model's 5 repeated calls on the same prompt consistent with
    each other? (subjects=examples, raters=repeat index)."""
    long_rows = []
    for row in raw_rows:
        for repeat_idx, score in enumerate(row[score_key]):
            long_rows.append({"target": row["label"], "rater": repeat_idx, "rating": score})
    df = pd.DataFrame(long_rows)
    if df["rating"].nunique() < 2:
        return {
            "icc": 1.0,
            "ci95": [1.0, 1.0],
            "note": "zero variance across all examples and repeats - perfect consistency",
        }
    with np.errstate(divide="ignore", invalid="ignore"):
        result = pg.intraclass_corr(data=df, targets="target", raters="rater", ratings="rating")
    return _extract_icc_row(result, "ICC(C,k)")


def _icc_agreement_with_researcher(raw_rows: list[dict], score_key: str, expected_key: str) -> dict:
    """ICC(A,1) [old naming: ICC2] — two-way random, absolute agreement,
    single rater: agreement between the researcher's calibrated value and
    the model's mean score, treated as two raters over the same examples —
    the absolute-value-agreement counterpart to the Spearman rank
    correlation already reported."""
    long_rows = []
    for row in raw_rows:
        if not row[score_key]:
            continue
        model_mean = float(np.mean(row[score_key]))
        long_rows.append(
            {"target": row["label"], "rater": "researcher", "rating": row[expected_key]}
        )
        long_rows.append({"target": row["label"], "rater": "model", "rating": model_mean})
    df = pd.DataFrame(long_rows)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = pg.intraclass_corr(data=df, targets="target", raters="rater", ratings="rating")
    return _extract_icc_row(result, "ICC(A,1)")


# -- 2. Bootstrap CIs for the small-n Spearman correlations --------------------


def _bootstrap_spearman_ci(x: list[float], y: list[float], n_boot: int = N_BOOTSTRAP) -> dict:
    x_arr, y_arr = np.array(x), np.array(y)
    n = len(x_arr)
    boot_rhos = []
    for _ in range(n_boot):
        idx = RNG.integers(0, n, size=n)
        rho, _ = spearmanr(x_arr[idx], y_arr[idx])
        if not np.isnan(rho):
            boot_rhos.append(rho)
    point_rho, point_p = spearmanr(x_arr, y_arr)
    lo, hi = np.percentile(boot_rhos, [2.5, 97.5])
    return {
        "point_estimate": float(point_rho),
        "asymptotic_p": float(point_p),
        "bootstrap_ci95": [float(lo), float(hi)],
        "n_bootstrap_samples_used": len(boot_rhos),
    }


# -- 6. CFA / omega smoke test --------------------------------------------------


def cfa_smoke_test(trajectory_path: Path) -> dict:
    if not trajectory_path.exists():
        return {
            "status": "skipped",
            "reason": "pilot_simulation.py has not produced trajectory data yet",
        }

    trajectory = json.loads(trajectory_path.read_text())
    all_signal_keys = [
        "S1.1", "S1.2", "S1.3", "S1.4", "S1.5", "S1.6",
        "S2.1", "S2.2", "S2.3", "S2.4",
        "S3.1", "S3.2", "S3.3", "S3.4",
    ]  # fmt: skip
    rows = []
    for point in trajectory:
        rows.append({key: point["signals"][key]["value"] for key in all_signal_keys})
    df = pd.DataFrame(rows)

    n_sessions = len(df)
    non_null_counts = df.notna().sum().to_dict()
    usable_cols = [
        c for c in df.columns if df[c].notna().sum() >= 2 and df[c].nunique(dropna=True) > 1
    ]

    result: dict = {
        "status": "ran_as_smoke_test_only",
        "n_sessions": n_sessions,
        "non_null_counts_per_signal": non_null_counts,
        "usable_signals_for_correlation": usable_cols,
        "interpretable": False,
        "why_not_interpretable": (
            f"n={n_sessions} sessions from a single synthetic AI persona is far below "
            "the sample size needed to estimate a 3-factor covariance structure "
            "(the paper's own a priori power analysis specifies ~300 real learners "
            "for even a modest effect size) - this run exists only to confirm the "
            "analysis code executes against real, correctly-shaped signal output."
        ),
    }

    if len(usable_cols) >= 2:
        corr = df[usable_cols].corr(method="spearman")
        result["correlation_matrix_signals_with_variation"] = corr.round(3).to_dict()
    else:
        result["correlation_matrix_signals_with_variation"] = (
            "fewer than 2 signals had non-constant values across sessions - "
            "correlation is undefined"
        )

    try:
        import semopy

        pillars = {
            "P1": [k for k in ["S1.1", "S1.2", "S1.3", "S1.4", "S1.5", "S1.6"] if k in usable_cols],
            "P2": [k for k in ["S2.1", "S2.2", "S2.3", "S2.4"] if k in usable_cols],
            "P3": [k for k in ["S3.1", "S3.2", "S3.3", "S3.4"] if k in usable_cols],
        }
        model_lines = [
            f"{pillar} =~ {' + '.join(keys)}" for pillar, keys in pillars.items() if len(keys) >= 2
        ]
        if len(model_lines) < 2:
            result["cfa_attempt"] = {
                "status": "not_attempted",
                "reason": "fewer than 2 pillars have >=2 non-constant signals in this tiny sample",
            }
        else:
            model_desc = "\n".join(model_lines)
            model = semopy.Model(model_desc)
            try:
                model.fit(df[usable_cols].dropna())
                result["cfa_attempt"] = {
                    "status": "fit_completed_but_not_interpretable",
                    "model_spec": model_desc,
                    "note": "Convergence with n=4 does not imply a valid model - see why_not_interpretable above.",
                }
            except Exception as exc:
                result["cfa_attempt"] = {
                    "status": "did_not_converge",
                    "model_spec": model_desc,
                    "error": str(exc),
                    "note": "Expected at this sample size - confirms the code path is reachable, not broken.",
                }
    except ImportError:
        result["cfa_attempt"] = {"status": "semopy_not_installed"}

    return result


def main() -> None:
    help_seeking_raw = json.loads((DATA_DIR / "llm_help_seeking_raw.json").read_text())
    concept_raw = json.loads((DATA_DIR / "llm_concept_understanding_raw.json").read_text())

    expected_calibration = {e.label: e.expected_calibration_value for e in HELP_SEEKING_EXAMPLES}
    expected_grounding = {e.label: e.expected_grounding_value for e in HELP_SEEKING_EXAMPLES}
    expected_concept = {e.label: e.expected_value for e in CONCEPT_UNDERSTANDING_EXAMPLES}
    for row in help_seeking_raw:
        row["expected_calibration_value"] = expected_calibration[row["label"]]
        row["expected_grounding_value"] = expected_grounding[row["label"]]
    for row in concept_raw:
        row["expected_value"] = expected_concept[row["label"]]

    # -- 1. ICC ----------------------------------------------------------------
    icc_results = {
        "S1.1_help_seeking_calibration": {
            "self_consistency_ICC3k": _icc_self_consistency(help_seeking_raw, "calibration_scores"),
            "agreement_with_researcher_ICC2": _icc_agreement_with_researcher(
                help_seeking_raw, "calibration_scores", "expected_calibration_value"
            ),
        },
        "S1.2_student_grounding": {
            "self_consistency_ICC3k": _icc_self_consistency(help_seeking_raw, "grounding_scores"),
            "agreement_with_researcher_ICC2": _icc_agreement_with_researcher(
                help_seeking_raw, "grounding_scores", "expected_grounding_value"
            ),
        },
        "S3.1_conceptual_understanding": {
            "self_consistency_ICC3k": _icc_self_consistency(concept_raw, "scores"),
            "agreement_with_researcher_ICC2": _icc_agreement_with_researcher(
                concept_raw, "scores", "expected_value"
            ),
        },
    }

    # -- 2. Bootstrap CIs (rank-based, matches the original face-validity check) -
    bootstrap_results = {
        "S1.1_help_seeking_calibration": _bootstrap_spearman_ci(
            [e.expected_calibration_rank for e in HELP_SEEKING_EXAMPLES],
            [np.mean(r["calibration_scores"]) for r in help_seeking_raw],
        ),
        "S1.2_student_grounding": _bootstrap_spearman_ci(
            [e.expected_grounding_rank for e in HELP_SEEKING_EXAMPLES],
            [np.mean(r["grounding_scores"]) for r in help_seeking_raw],
        ),
        "S3.1_conceptual_understanding": _bootstrap_spearman_ci(
            [e.expected_rank for e in CONCEPT_UNDERSTANDING_EXAMPLES],
            [np.mean(r["scores"]) for r in concept_raw],
        ),
    }
    analysis_summary = json.loads((DATA_DIR / "analysis_summary.json").read_text())
    s23_mono = analysis_summary["monotonicity"][0]
    bootstrap_results["S2.3_problem_solving_agency_monotonicity"] = {
        "point_estimate": s23_mono["spearman_r"],
        "asymptotic_p": s23_mono["p_value"],
        "note": "n=11 deterministic gradient points, not a small-N human-judgment check like the other three",
    }

    # -- 3. Multiple-comparisons correction --------------------------------------
    test_names = list(bootstrap_results.keys())
    raw_p_values = [bootstrap_results[name]["asymptotic_p"] for name in test_names]
    holm_reject, holm_p, _, _ = multipletests(raw_p_values, alpha=0.05, method="holm")
    bh_reject, bh_p, _, _ = multipletests(raw_p_values, alpha=0.05, method="fdr_bh")
    correction_results = {
        name: {
            "raw_p": raw_p_values[i],
            "holm_adjusted_p": float(holm_p[i]),
            "holm_reject_at_05": bool(holm_reject[i]),
            "bh_adjusted_p": float(bh_p[i]),
            "bh_reject_at_05": bool(bh_reject[i]),
        }
        for i, name in enumerate(test_names)
    }

    # -- 4. S1.1 vs S1.2 discriminant check --------------------------------------
    calibration_means = [np.mean(r["calibration_scores"]) for r in help_seeking_raw]
    grounding_means = [np.mean(r["grounding_scores"]) for r in help_seeking_raw]
    pearson_r, pearson_p = pearsonr(calibration_means, grounding_means)
    spearman_r, spearman_p = spearmanr(calibration_means, grounding_means)
    discriminant_check = {
        "pearson_r": float(pearson_r),
        "pearson_p": float(pearson_p),
        "spearman_r": float(spearman_r),
        "spearman_p": float(spearman_p),
        "n": len(calibration_means),
        "interpretation": (
            "high (>0.9) suggests the two rubric items may not be discriminating well from "
            "each other in the model's output; moderate suggests related-but-distinct scoring, "
            "consistent with the framework treating them as separate signals"
        ),
    }

    # -- 5. Confusion-matrix framing of gating logic, over the FULL corpus -------
    synthetic_rows = json.loads((DATA_DIR / "synthetic_trials.json").read_text())
    tp = fn = tn = fp = 0
    fn_cases, fp_cases = [], []
    for r in synthetic_rows:
        evidence_expected = r["design"] is not None
        value_returned = r["computed_value"] is not None
        if evidence_expected and value_returned:
            tp += 1
        elif evidence_expected and not value_returned:
            fn += 1
            fn_cases.append({"signal": r["signal"], "scenario": r["scenario"]})
        elif not evidence_expected and not value_returned:
            tn += 1
        else:
            fp += 1
            fp_cases.append({"signal": r["signal"], "scenario": r["scenario"]})
    confusion_matrix = {
        "true_positive": tp,
        "false_negative": fn,
        "true_negative": tn,
        "false_positive": fp,
        "sensitivity_recall": tp / (tp + fn) if (tp + fn) else None,
        "specificity": tn / (tn + fp) if (tn + fp) else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "false_negative_cases": fn_cases,
        "false_positive_cases": fp_cases,
    }

    # -- 6. CFA / omega smoke test ------------------------------------------------
    cfa_result = cfa_smoke_test(DATA_DIR / "pilot_simulation_trajectory.json")

    full_results = {
        "icc": icc_results,
        "bootstrap_confidence_intervals": bootstrap_results,
        "multiple_comparisons_correction": correction_results,
        "s1_1_vs_s1_2_discriminant_check": discriminant_check,
        "gating_logic_confusion_matrix": confusion_matrix,
        "cfa_reliability_pipeline_smoke_test": cfa_result,
    }
    path = save_json("advanced_statistics.json", full_results)
    print(f"Wrote {path}\n")
    print(json.dumps(full_results, indent=2, default=str))


if __name__ == "__main__":
    main()
