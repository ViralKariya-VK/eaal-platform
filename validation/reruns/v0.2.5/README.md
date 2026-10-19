# Validation re-run on release v0.2.5 (2026-10-19)

Run from a clean checkout of tag `v0.2.5` with the local Ollama `qwen3:8b` (Ollama client 0.40.3).
The committed results in `../../data/` (the ones the paper reports) were **not** overwritten; this folder holds the re-run only.

## Reproduced exactly (deterministic signals)
Known-answer 53/53, gating 10/10, determinism 88/88 (five recomputations), S1.3 threshold 11/11,
S2.3 monotonicity Spearman r = -1.000 (n = 11). The pilot case study ran end to end
(Session 3 and 4: P1 = None, not 0).

## Live-model signals (S1.1, S1.2, S3.1): not identical to the paper's run
| | paper's committed run | this re-run |
|---|---|---|
| empty/unparseable replies | 0 | 10/40 help-seeking, 5/30 concept (tuning), 19/120 concept (all 24) |
| largest repeat range S1.1 / S1.2 / S3.1 | 0 / 0 / 0.056 | 1.0 / 0.30 / 0.30 |
| S3.1 score for the surface line-by-line restatement | 1.0 | 0.3 |
| Spearman, mean score vs expected rank: S1.1 / S1.2 | 0.866 / 0.843 | 0.957 / 0.853 |
| S3.1, the original 6 examples | 0.845 | 0.971 |
| S3.1, all 24 examples incl. 18 held-out | (not in the paper) | 0.952 (committed v2 data: 0.939) |

Cause of the parse failures: the model sometimes replies with an empty JSON object (`{}`) in JSON mode
(3 of 15 probe calls). Rank correlations stay high, but the live-model figures vary between runs.

`advanced_statistics.py` (ICC, Holm/BH) was not re-run: it needs balanced repeats, which the parse failures broke.
