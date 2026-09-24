# CIQ Signal Validation Suite

Validates that the 14 EAAL signals in `src/eaal_platform/signals/compute.py`
compute what they claim to compute — reliably, deterministically, and
correctly against controlled evidence — using the *real* `CavyApi` bridge
against a throwaway database, plus real calls to the app's actual local
model (Ollama `qwen3:8b`) for the three LLM-rubric signals.

This is a separate concern from `tests/` (which gates every commit with
fast pass/fail unit tests). This suite produces a larger evidence base and
a formal written report — see `report/VALIDATION_REPORT.md` /
`report/VALIDATION_REPORT.pdf` for the full write-up, methodology, results,
and — importantly — an explicit statement of what this suite does **not**
establish (real construct validity against actual student learning
outcomes, which needs the human-subject study the EAAL framework's own §9
calls for).

## Layout

```
validation/
├── harness.py                    # shared plumbing: fresh test DB, scripted AI providers,
│                                  # student/professor account helpers
├── labeled_examples.py           # hand-labeled examples for the LLM face-validity check
├── scenarios/
│   ├── pillar1_ai_utilization.py       # S1.1-S1.6 synthetic scenarios
│   ├── pillar2_cognitive_engagement.py # S2.1-S2.4 synthetic scenarios
│   └── pillar3_learning_development.py # S3.1-S3.4 synthetic scenarios
├── run_synthetic_suite.py        # builds & computes every synthetic trial, checks determinism
├── run_llm_reliability.py        # real-model calls: reliability + face validity (S1.1/S1.2/S3.1)
├── analyze.py                    # statistics + figures from the collected data
├── generate_report.py            # renders report/VALIDATION_REPORT.md from the analysis data
├── data/                         # raw + analyzed results (JSON)
├── figures/                      # generated charts (PNG)
└── report/
    ├── VALIDATION_REPORT.md
    ├── VALIDATION_REPORT.pdf
    └── report_style.css
```

## Running it

```bash
# from eaal-platform/, with the project's conda env active
python3 validation/run_synthetic_suite.py      # ~seconds
python3 validation/run_llm_reliability.py      # ~5-10 min; needs `ollama serve` running qwen3:8b
python3 validation/analyze.py
python3 validation/generate_report.py

# optional: render the PDF (needs pandoc + weasyprint; on macOS with
# Homebrew's pango installed, weasyprint needs
# DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib set)
cd validation/report
pandoc VALIDATION_REPORT.md -o VALIDATION_REPORT.html --standalone \
  --embed-resources --resource-path=. -c report_style.css
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python3 -c \
  "from weasyprint import HTML; HTML('VALIDATION_REPORT.html').write_pdf('VALIDATION_REPORT.pdf')"
```

`run_synthetic_suite.py` and `analyze.py` have no external dependencies
beyond what's already needed; `run_llm_reliability.py` needs a running
local Ollama server. Everything writes into `data/`/`figures/`/`report/`
so reruns simply overwrite prior results — nothing here is meant to be
hand-edited after generation, including the report itself (change
`generate_report.py`, not the rendered Markdown, if the report needs to
change).

## Adding a new scenario

Add a `build_*(session_factory) -> list[Trial]` function to the relevant
`scenarios/pillar*.py` module (mirroring the existing ones), include it in
that module's `build_all()`, then rerun the pipeline above. Give every
known-answer trial a `notes="expected value is the known closed-form
answer"` (or "...clamped scripted score") string — `analyze.py` and
`generate_report.py` use that exact substring to find known-answer trials
for the accuracy tables, so a new trial without it will silently be
excluded from that count.
