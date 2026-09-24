"""End-to-end smoke test for the pilot web server, against a fake Groq
provider (no real API key or network access needed) — walks the exact
sequence a real participant's browser would: start, edit, run, chat,
submit twice, self-rate.

Run with a fresh temp DB (set before the server module is imported, since
it opens the database at import time): see the ``client`` fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eaal_platform.ai.provider import GenerationContext, GenerationResult, Purpose


class _FakeGroqProvider:
    """Mimics GroqProvider's interface with canned, deterministic replies —
    the same role ``validation/harness.py``'s ``ScriptedProvider`` plays for
    the desktop app's tests."""

    def __init__(self, api_key: str, model: str = "fake-model") -> None:
        self._api_key = api_key
        self._model = model

    def ping(self) -> bool:
        return self._api_key != "bad-key"

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        if purpose is Purpose.RUBRIC_SCORING:
            return GenerationResult(text="{}", available=False, error="not scripted for this test")
        return GenerationResult(
            text="```python\ndef is_palindrome(s):\n    s = s.lower()\n    return s == s[::-1]\n\n"
            "print(is_palindrome('Racecar'))\n```",
            available=True,
        )

    @property
    def provider_name(self) -> str:
        return "fake-groq"

    @property
    def model_name(self) -> str | None:
        return self._model


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PILOT_DB_PATH", str(tmp_path / "pilot_test.db"))
    # Import here, after PILOT_DB_PATH is set, since the module opens its
    # database at import time.
    import importlib

    import pilot_web.server as server_module

    importlib.reload(server_module)
    monkeypatch.setattr(server_module, "GroqProvider", _FakeGroqProvider)

    from fastapi.testclient import TestClient

    with TestClient(server_module.app) as test_client:
        yield test_client


def test_full_pilot_flow(client) -> None:
    start = client.post("/api/session/start", json={"groq_api_key": "good-key"})
    assert start.status_code == 200, start.text
    body = start.json()
    token = body["token"]
    task1 = body["task"]
    assert task1["task_title"].startswith("Pilot: Palindrome Check")

    files = {"main.py": "def is_palindrome(s):\n    return s == s[::-1]\nprint(is_palindrome('x'))"}

    edit = client.post(
        "/api/code/edit", json={"token": token, "files": files, "active_filename": "main.py"}
    )
    assert edit.status_code == 200, edit.text

    run = client.post("/api/code/run", json={"token": token, "files": files})
    assert run.status_code == 200, run.text
    assert "exit_status" in run.json()

    chat = client.post(
        "/api/chat/send", json={"token": token, "message": "help me get started", "files": files}
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["available"] is True

    submit1 = client.post("/api/task/submit", json={"token": token, "files": files})
    assert submit1.status_code == 200, submit1.text
    submit1_body = submit1.json()
    assert submit1_body["next"] == "task2"
    assert submit1_body["task"]["task_title"].startswith("Pilot: List Palindrome Check")

    files2 = {
        "main.py": (
            "def is_palindrome_list(items):\n    return items == items[::-1]\n"
            "print(is_palindrome_list([1, 2, 3, 2, 1]))"
        )
    }
    run2 = client.post("/api/code/run", json={"token": token, "files": files2})
    assert run2.status_code == 200, run2.text

    submit2 = client.post("/api/task/submit", json={"token": token, "files": files2})
    assert submit2.status_code == 200, submit2.text
    submit2_body = submit2.json()
    assert submit2_body["next"] == "score"
    summary = submit2_body["summary"]
    assert set(summary.keys()) == {
        "How effectively you used AI help",
        "How actively you reasoned through the problem",
        "Evidence your understanding transferred",
    }

    rating = client.post(
        "/api/self_rating",
        json={"token": token, "self_rating": 3, "self_rating_comment": "about right"},
    )
    assert rating.status_code == 200, rating.text

    # The token is retired after self-rating; using it again must fail.
    stale = client.post("/api/code/run", json={"token": token, "files": files2})
    assert stale.status_code == 404


def test_bad_api_key_is_rejected(client) -> None:
    response = client.post("/api/session/start", json={"groq_api_key": "bad-key"})
    assert response.status_code == 400


def test_unknown_token_returns_404(client) -> None:
    response = client.post(
        "/api/code/run", json={"token": "not-a-real-token", "files": {"main.py": ""}}
    )
    assert response.status_code == 404
