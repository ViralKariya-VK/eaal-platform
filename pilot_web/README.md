# CIQ Pilot Survey (web)

A small, shareable web page for collecting real (if small-n) human-learner
data for the CIQ/EAAL signal validation — the thing no amount of synthetic
or AI-persona testing can substitute for. See
`validation/report/VALIDATION_REPORT.md` §6 for why that distinction
matters and what this pilot can and can't establish on its own.

Each participant: reads a consent screen, supplies their **own** Groq API
key (free tier is enough), solves two short tasks (the second is a
Transfer variant of the first) with an AI assistant available, sees a
plain-language summary of their computed signals, and answers one
self-rating question ("was that score about what you expected?").

No name, email, or other PII is collected. No API key is ever written to
disk or logged — it lives only in that request's in-memory `CavyApi`
instance for the duration of the session.

## Why this reuses CavyApi directly

Every route in `server.py` is a thin JSON wrapper around an existing,
already-validated `CavyApi` method (`start_stage`, `log_code_edit`,
`run_code`, `send_ai_message`, `submit_session`, `get_ciq_score`,
`create_followup_assessment`) — one `CavyApi` instance per participant,
exactly the "one identity per window" model it already had, just addressed
by an HTTP token instead of a pywebview window. No signal-computation,
event-logging, or sandbox logic is reimplemented here.

## Running it

```bash
pip install -e ".[pilot]"
uvicorn pilot_web.server:app --host 0.0.0.0 --port 8420
```

Data lands in `pilot_web/data/pilot.db` (gitignored) — the same
`eaal_platform` schema the desktop app uses, plus one small extra table
(`pilot_responses`, in `pilot_models.py`) for consent timestamps and the
self-rating.

Override the database location (e.g. to point at a mounted volume, or for
tests) with `PILOT_DB_PATH=/path/to/pilot.db`.

## Sharing it with real participants

This only runs as long as the process above is running and reachable.
Two practical options, in order of effort:

1. **Quick, temporary pilot window**: run it locally and expose it with a
   tunnel — e.g. `ngrok http 8420` — and share the resulting URL. Free,
   takes a minute, but only works while your machine and the tunnel stay
   up.
2. **A real deployment**: push this to a small hosting provider (Render,
   Railway, Fly.io all have workable free/cheap tiers for a FastAPI app).
   Point `PILOT_DB_PATH` at a persistent volume if the platform doesn't
   keep local disk between deploys, or the pilot data won't survive a
   restart.

Neither of these is something this session can do on your behalf (no
cloud account access) — this README just lays out the options.

## Reading the results

```python
from eaal_platform.db.engine import create_db_engine, create_session_factory
from pilot_web.pilot_models import PilotResponse

engine = create_db_engine("pilot_web/data/pilot.db")
session_factory = create_session_factory(engine)
with session_factory() as db:
    for r in db.query(PilotResponse).all():
        print(r.student_id, r.self_rating, r.ciq_summary_json)
```

Each `PilotResponse.ciq_summary_json` is the same plain-language summary
the participant saw; `task1_session_id`/`task2_session_id` let you pull
the full 14-signal breakdown for that participant via
`eaal_platform.signals.compute.compute_all_signals` — the same function
`validation/` uses, so a real pilot run's data can go through the exact
same analysis pipeline (`validation/analyze.py`,
`validation/advanced_statistics.py`) with minor adaptation once there's
more than a couple of participants.

## Task design and its limits

Two ~10-minute tasks (palindrome check on a string, then on a list — a
deliberate Transfer pair: different surface, same underlying idea) give
real evidence for S1.1–S1.4, S2.1–S2.3, S3.1, S3.2, and S3.3 (Knowledge
Transfer, via task 2). They **cannot** give evidence for S1.5/S1.6 (need
more interactions/session history than a single sitting provides) or S3.4
(Retention — structurally requires a real ≥24h gap; see
`db/models.py`'s `AssessmentKind` and the retention-delay tests in
`validation/`). A follow-up contact days later would be a separate,
harder ask of participants and isn't built here.
