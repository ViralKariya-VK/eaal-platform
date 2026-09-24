"""A thin HTTP wrapper around the same ``CavyApi``/signals engine the
desktop app uses, for collecting real (if small-n) human-learner data via
a shareable link instead of a packaged desktop install.

Deliberately reuses ``CavyApi`` as-is — one instance per participant,
exactly the "one identity per window" model it was already built for, just
addressed by an HTTP token instead of a pywebview window. Every route here
is a thin translation from a JSON request to an existing, already-tested
``CavyApi`` method call; no signal-computation or event-logging logic is
reimplemented.

Run with: ``uvicorn pilot_web.server:app --reload`` (needs the ``pilot``
extra: ``pip install -e ".[pilot]"``).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eaal_platform.ai.groq_provider import GroqProvider
from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import (
    create_followup_assessment,
    create_student_account,
)
from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db
from eaal_platform.db.models import (
    AIAssistanceMode,
    AssessmentKind,
    Stage,
    StageType,
    Task,
)
from eaal_platform.events.logger import EventLogger
from pilot_web.pilot_models import PilotBase, PilotResponse

DATA_DIR = Path(__file__).resolve().parent / "data"
# Overridable so tests (and deployments that want the DB on a mounted
# volume) don't have to touch the real pilot data on disk.
DB_PATH = Path(os.environ.get("PILOT_DB_PATH", str(DATA_DIR / "pilot.db")))
STATIC_DIR = Path(__file__).resolve().parent / "static"

_TASK1_TITLE = "Pilot: Palindrome Check"
_TASK1_DESCRIPTION = (
    "Write a function `is_palindrome(s)` that returns True if the string `s` "
    "reads the same forwards and backwards (ignore case), and False otherwise. "
    "Print the result for is_palindrome('Racecar')."
)
_TASK2_TITLE = "Pilot: List Palindrome Check"
_TASK2_DESCRIPTION = (
    "Write a function `is_palindrome_list(items)` that returns True if the "
    "list `items` reads the same forwards and backwards, and False otherwise. "
    "Print the result for is_palindrome_list([1, 2, 3, 2, 1])."
)

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_engine = create_db_engine(DB_PATH)
init_db(_engine)
PilotBase.metadata.create_all(_engine)
_session_factory = create_session_factory(_engine)


def _ensure_pilot_tasks() -> tuple[int, int]:
    """Create the two pilot tasks once; reuse them on every later launch."""
    with _session_factory() as db_session:
        existing = db_session.query(Task).filter_by(title=_TASK1_TITLE).first()
        if existing is not None:
            task2 = db_session.query(Task).filter_by(linked_task_id=existing.id).first()
            if task2 is not None:
                return existing.id, task2.id
            task1_id = existing.id
        else:
            task1 = Task(
                title=_TASK1_TITLE,
                description=_TASK1_DESCRIPTION,
                learning_objective="Recognizing a palindrome by comparing a value to its reverse.",
                difficulty="Easy",
            )
            db_session.add(task1)
            db_session.flush()
            db_session.add(
                Stage(
                    task_id=task1.id,
                    stage_type=StageType.ASSESSMENT,
                    ai_assistance_mode=AIAssistanceMode.FULL,
                    duration_minutes=15,
                    order_index=0,
                )
            )
            db_session.commit()
            task1_id = task1.id

    task2_id = create_followup_assessment(
        _session_factory,
        source_task_id=task1_id,
        kind=AssessmentKind.TRANSFER,
        title=_TASK2_TITLE,
        description=_TASK2_DESCRIPTION,
        ai_assistance_mode=AIAssistanceMode.FULL,
        duration_minutes=15,
    )
    return task1_id, task2_id


_TASK1_ID, _TASK2_ID = _ensure_pilot_tasks()

# token -> {"api": CavyApi, "student_id": int, "task1_session_id": int | None,
#           "task2_session_id": int | None}. In-memory only: the participant's
# Groq key lives inside that CavyApi's GroqProvider and is never written to
# disk or logged, and disappears the moment the server process restarts.
_PARTICIPANTS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="EAAL CIQ Pilot Survey")


def _participant(token: str) -> dict[str, Any]:
    session = _PARTICIPANTS.get(token)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown or expired session token.")
    return session


class StartRequest(BaseModel):
    groq_api_key: str


class CodeRequest(BaseModel):
    token: str
    files: dict[str, str]
    active_filename: str | None = None


class ChatRequest(BaseModel):
    token: str
    message: str
    files: dict[str, str]


class SubmitRequest(BaseModel):
    token: str
    files: dict[str, str]


class SelfRatingRequest(BaseModel):
    token: str
    self_rating: int
    self_rating_comment: str = ""


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/session/start")
def start_session(req: StartRequest) -> dict[str, Any]:
    provider = GroqProvider(api_key=req.groq_api_key)
    if not provider.ping():
        raise HTTPException(
            status_code=400,
            detail="Couldn't reach Groq with that API key. Double-check it and try again.",
        )

    email = f"pilot-{uuid.uuid4()}@anon.local"
    password = secrets.token_urlsafe(24)
    student_id = create_student_account(
        _session_factory, display_name="Pilot Participant", email=email, password=password
    )

    event_logger = EventLogger(_session_factory)
    event_logger.start()
    api = CavyApi(_session_factory, event_logger, ai_provider=provider)
    login_result = api.login("student", email, password)
    if not login_result["ok"]:
        raise HTTPException(status_code=500, detail="Could not create a participant session.")

    token = secrets.token_urlsafe(32)
    _PARTICIPANTS[token] = {
        "api": api,
        "event_logger": event_logger,
        "student_id": student_id,
        "task1_session_id": None,
        "task2_session_id": None,
    }

    with _session_factory() as db_session:
        db_session.add(PilotResponse(student_id=student_id))
        db_session.commit()

    stages = api.get_stages(_TASK1_ID)["stages"]
    info = api.start_stage(stages[0]["id"])
    _PARTICIPANTS[token]["task1_session_id"] = info["session_id"]

    return {"token": token, "task": info}


@app.post("/api/code/edit")
def code_edit(req: CodeRequest) -> dict[str, Any]:
    session = _participant(req.token)
    session_id = session["task2_session_id"] or session["task1_session_id"]
    return session["api"].log_code_edit(session_id, req.files, req.active_filename)


@app.post("/api/code/run")
def code_run(req: CodeRequest) -> dict[str, Any]:
    session = _participant(req.token)
    session_id = session["task2_session_id"] or session["task1_session_id"]
    return session["api"].run_code(session_id, req.files)


@app.post("/api/chat/send")
def chat_send(req: ChatRequest) -> dict[str, Any]:
    session = _participant(req.token)
    session_id = session["task2_session_id"] or session["task1_session_id"]
    return session["api"].send_ai_message(session_id, req.message, req.files)


@app.post("/api/task/submit")
def submit_task(req: SubmitRequest) -> dict[str, Any]:
    session = _participant(req.token)
    api: CavyApi = session["api"]

    if session["task2_session_id"] is None:
        api.submit_session(session["task1_session_id"], req.files)
        followups = api.get_followup_assessments(_TASK1_ID)
        stage_id = next(f["stage_id"] for f in followups if f["assessment_kind"] == "TRANSFER")
        info = api.start_stage(stage_id)
        session["task2_session_id"] = info["session_id"]
        return {"next": "task2", "task": info}

    api.submit_session(session["task2_session_id"], req.files)
    summary = _score_summary(api, session["task1_session_id"], session["task2_session_id"])

    with _session_factory() as db_session:
        record = db_session.query(PilotResponse).filter_by(student_id=session["student_id"]).first()
        if record is not None:
            record.task1_session_id = session["task1_session_id"]
            record.task2_session_id = session["task2_session_id"]
            record.ciq_summary_json = json.dumps(summary)
            db_session.commit()

    return {"next": "score", "summary": summary}


def _score_summary(api: CavyApi, task1_session_id: int, task2_session_id: int) -> dict[str, Any]:
    """A participant-friendly summary: plain pillar labels, not signal
    codes, averaged across whichever of the two sessions has evidence for
    each pillar (Transfer evidence only exists on the second session)."""
    score1 = api.get_ciq_score(task1_session_id)
    score2 = api.get_ciq_score(task2_session_id)

    pillar_labels = {
        "P1 · AI Utilization": "How effectively you used AI help",
        "P2 · Cognitive Engagement": "How actively you reasoned through the problem",
        "P3 · Learning & Knowledge Development": "Evidence your understanding transferred",
    }
    combined: dict[str, list[float]] = {label: [] for label in pillar_labels.values()}
    for score in (score1, score2):
        for pillar in score["pillars"]:
            label = pillar_labels.get(pillar["heading"])
            if label is None:
                continue
            values = [s["value"] for s in pillar["signals"] if s["value"] is not None]
            combined[label].extend(values)

    return {
        label: round(sum(values) / len(values) * 100, 0) if values else None
        for label, values in combined.items()
    }


@app.post("/api/self_rating")
def self_rating(req: SelfRatingRequest) -> dict[str, Any]:
    session = _participant(req.token)
    with _session_factory() as db_session:
        record = db_session.query(PilotResponse).filter_by(student_id=session["student_id"]).first()
        if record is None:
            raise HTTPException(status_code=404, detail="No pilot record for this session.")
        record.self_rating = req.self_rating
        record.self_rating_comment = req.self_rating_comment
        record.completed_at = datetime.now(UTC)
        db_session.commit()
    session["event_logger"].stop()
    del _PARTICIPANTS[req.token]
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
