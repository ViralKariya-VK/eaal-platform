"""Choosing an AI provider: the student's own key, the class assistant, and session privacy."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose
from eaal_platform.api.bridge import CavyApi
from eaal_platform.client.api import ClientApi
from eaal_platform.client.remote import RemoteBackend
from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    seed_demo_content,
    start_practice_session,
    start_stage_session,
)
from eaal_platform.db.models import AIInteraction, Stage
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.app import ServerState, create_app

_PW = "hunter2-hunter2"
_KEY = "sk-secret-student-key-123"
Factory = sessionmaker[OrmSession]


class FakeAI:
    """Stands in for OpenAI: lists models and answers chat."""

    def __init__(self, reply: str = "Try range(1, 6).", valid_key: str = _KEY) -> None:
        self.reply = reply
        self.valid_key = valid_key
        self.chats: list[dict[str, Any]] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.valid_key}":
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]})
        self.chats.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply}}]})


# -- the server's own methods ------------------------------------------------------------------


@pytest.fixture
def api(db_session_factory: Factory) -> tuple[CavyApi, FakeAI]:
    seed_demo_content(db_session_factory)
    create_student_account(db_session_factory, display_name="A", email="a@x.com", password=_PW)
    fake = FakeAI()
    cavy = CavyApi(db_session_factory, EventLogger(db_session_factory), ai_http=fake.client())
    cavy.login("student", "a@x.com", _PW)
    return cavy, fake


def test_a_cloud_provider_can_be_switched_to_and_is_checked_first(
    api: tuple[CavyApi, FakeAI],
) -> None:
    cavy, _ = api
    bad = cavy.set_ai_provider("openai", "wrong-key")
    assert bad["ok"] is False
    assert "rejected the API key" in bad["error"]
    assert cavy.get_ai_settings()["provider"] != "openai"  # unchanged by the failed attempt

    wrong_model = cavy.set_ai_provider("openai", _KEY, "gpt-imaginary")
    assert wrong_model == {"ok": False, "error": "That model isn't available to this key."}

    good = cavy.set_ai_provider("openai", _KEY)
    assert good["ok"] is True
    assert (good["provider"], good["model"]) == ("openai", "gpt-4o-mini")  # the recommended default
    assert cavy.set_ai_provider("anthropic", "x")["ok"] is False
    with pytest.raises(ValueError, match="Unknown AI provider"):
        cavy.set_ai_provider("bard", _KEY)


def test_the_assistant_is_asked_in_two_steps_and_both_are_recorded(
    api: tuple[CavyApi, FakeAI], db_session_factory: Factory
) -> None:
    cavy, _ = api
    session_id = start_practice_session(db_session_factory, 1)

    prepared = cavy.prepare_ai_message(session_id, "Why?", {"main.py": "print(1)"})
    assert prepared["allowed"] is True
    assert "print(1)" in prepared["context"]["current_code"]

    recorded = cavy.record_ai_message(
        session_id,
        prepared["snapshot_id"],
        "Why?",
        {"available": True, "text": "Because."},
        "openai",
        "gpt-4o-mini",
    )
    assert recorded == {"available": True, "text": "Because."}
    with db_session_factory() as db:
        row = db.query(AIInteraction).one()
    assert (row.prompt, row.response, row.provider, row.model) == (
        "Why?",
        "Because.",
        "openai",
        "gpt-4o-mini",
    )
    failed = cavy.record_ai_message(
        session_id,
        prepared["snapshot_id"],
        "Again?",
        {"available": False, "error": "no internet"},
        "openai",
        "gpt-4o-mini",
    )
    assert failed == {"available": False, "text": "no internet"}


def test_restricted_stages_refuse_before_any_ai_is_asked(
    api: tuple[CavyApi, FakeAI], db_session_factory: Factory
) -> None:
    cavy, _ = api
    with db_session_factory() as db:
        restricted = next(
            s for s in db.query(Stage).all() if s.ai_assistance_mode.value in ("RESTRICTED", "NONE")
        )
        stage_id = restricted.id
    session_id = start_stage_session(db_session_factory, 1, stage_id)
    reply = cavy.prepare_ai_message(session_id, "help", {"main.py": ""})
    assert reply == {"allowed": False, "error": "AI assistance is restricted during this stage."}


def test_students_cannot_touch_each_others_sessions(db_session_factory: Factory) -> None:
    seed_demo_content(db_session_factory)
    a = create_student_account(db_session_factory, display_name="A", email="a@x.com", password=_PW)
    create_student_account(db_session_factory, display_name="B", email="b@x.com", password=_PW)
    theirs = start_practice_session(db_session_factory, a)
    cavy = CavyApi(db_session_factory, EventLogger(db_session_factory))
    cavy.login("student", "b@x.com", _PW)

    files = {"main.py": "print(1)"}
    attempts: list[Callable[[], object]] = [
        lambda: cavy.log_code_edit(theirs, files),
        lambda: cavy.prepare_run(theirs, files),
        lambda: cavy.record_run(theirs, 1, "main.py", {"stdout": "x"}),
        lambda: cavy.run_code(theirs, files),
        lambda: cavy.submit_session(theirs, files),
        lambda: cavy.submit_concept_check(theirs, "text"),
        lambda: cavy.send_ai_message(theirs, "hi", files),
        lambda: cavy.prepare_ai_message(theirs, "hi", files),
        lambda: cavy.record_ai_message(theirs, 1, "hi", {"available": True}, "x", "y"),
        lambda: cavy.get_submission_summary(theirs),
        lambda: cavy.get_ciq_score(theirs),
    ]
    for attempt in attempts:
        with pytest.raises(ValueError, match="isn't yours"):
            attempt()
    with pytest.raises(ValueError, match="isn't yours"):
        cavy.log_code_edit(99999, files)  # nor can you invent session numbers


# -- the app on a server ------------------------------------------------------------------------


class Classroom:
    """A server, a teacher, and students on their own 'computers'."""

    def __init__(self, http: TestClient, state: ServerState, factory: Factory) -> None:
        self.http, self.state, self.factory = http, state, factory
        self.sent: list[str] = []  # everything the apps sent to the server
        original = http.post

        def spying_post(url: Any, **kwargs: Any) -> Any:
            self.sent.append(json.dumps(kwargs.get("json", {})))
            return original(url, **kwargs)

        http.post = spying_post  # type: ignore[method-assign]
        create_professor_account(factory, display_name="Prof", email="p@x.com", password=_PW)
        for name in ("a", "b"):
            create_student_account(
                factory, display_name=name.upper(), email=f"{name}@x.com", password=_PW
            )

    def computer(self, fake: FakeAI | None = None) -> ClientApi:
        local_engine_factory = self.factory  # the local database is unused in server mode
        app = ClientApi(
            CavyApi(local_engine_factory, EventLogger(local_engine_factory)),
            None,
            ai_http=(fake or FakeAI()).client(),
        )
        app._use_server(RemoteBackend("http://testserver", http=self.http))
        return app


@pytest.fixture
def room(db_engine: Engine, db_session_factory: Factory) -> Iterator[Classroom]:
    seed_demo_content(db_session_factory)
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state)) as http:
        yield Classroom(http, state, db_session_factory)


def _student_session(app: ClientApi) -> int:
    return int(app.start_practice()["session_id"])


def test_students_see_the_four_choices_and_teachers_see_all(room: Classroom) -> None:
    student, teacher = room.computer(), room.computer()
    student.login("student", "a@x.com", _PW)
    teacher.login("professor", "p@x.com", _PW)

    offered = student.get_ai_providers()
    assert offered["personal"] is True
    assert [p["key"] for p in offered["providers"]] == ["gemini", "anthropic", "openai", "xai"]
    assert all(p["needs_key"] for p in offered["providers"])
    everything = teacher.get_ai_providers()
    assert everything["personal"] is False
    assert {p["key"] for p in everything["providers"]} == {
        "openai", "anthropic", "gemini", "xai", "groq", "ollama",
    }  # fmt: skip


def test_the_students_key_stays_on_their_computer(room: Classroom) -> None:
    fake = FakeAI(reply="Use range.")
    student = room.computer(fake)
    student.login("student", "a@x.com", _PW)
    session_id = _student_session(student)

    assert student.check_ai_key("openai", _KEY)["models"][0] == "gpt-4o-mini"
    assert student.set_ai_provider("openai", _KEY, "gpt-4o")["ok"] is True
    settings = student.get_ai_settings()
    assert (settings["scope"], settings["provider"], settings["model"]) == (
        "personal",
        "openai",
        "gpt-4o",
    )

    answer = student.send_ai_message(session_id, "Why range(1, 6)?", {"main.py": "x = 1"})

    assert answer == {"available": True, "text": "Use range."}
    assert fake.chats[0]["model"] == "gpt-4o"  # the student's own choice was used
    with room.factory() as db:
        row = db.query(AIInteraction).one()  # and the server recorded it
    assert (row.provider, row.model, row.response) == ("openai", "gpt-4o", "Use range.")
    # The key was used from this computer, and nothing the app sent to the server contained it.
    assert _KEY not in " ".join(room.sent)
    assert room.state.ai_holder.provider.provider_name != "openai"


def test_a_wrong_key_is_refused_and_nothing_is_stored(room: Classroom) -> None:
    student = room.computer(FakeAI())
    student.login("student", "a@x.com", _PW)
    bad = student.set_ai_provider("openai", "not-the-key")
    assert bad["ok"] is False
    assert "rejected the API key" in bad["error"]
    assert student.get_ai_settings()["scope"] == "class"  # still on the class assistant
    assert (
        student.set_ai_provider("groq", "gsk_x")["ok"] is False
    )  # not one of the student's choices


def test_the_key_is_forgotten_on_sign_out_and_never_inherited(room: Classroom) -> None:
    shared_pc = room.computer(FakeAI())
    shared_pc.login("student", "a@x.com", _PW)
    shared_pc.set_ai_provider("openai", _KEY)
    assert shared_pc.get_ai_settings()["scope"] == "personal"

    shared_pc.logout()
    shared_pc.login("student", "b@x.com", _PW)  # the next student on the same computer
    assert shared_pc.get_ai_settings()["scope"] == "class"

    shared_pc.set_ai_provider("openai", _KEY)
    assert shared_pc.forget_my_ai_key() == {"ok": True}
    assert shared_pc.get_ai_settings()["scope"] == "class"
    shared_pc.set_ai_provider("openai", _KEY)
    shared_pc.login("student", "a@x.com", _PW)  # even signing straight into another account
    assert shared_pc.get_ai_settings()["scope"] == "class"


def test_without_their_own_key_students_use_the_class_assistant(room: Classroom) -> None:
    class ClassAI(AIProvider):
        def generate(
            self, prompt: str, context: GenerationContext, purpose: Purpose
        ) -> GenerationResult:
            return GenerationResult(f"class says: {prompt}", True)

        def ping(self) -> bool:
            return True

        @property
        def provider_name(self) -> str:
            return "class-ai"

        @property
        def model_name(self) -> str | None:
            return "m1"

    room.state.ai_holder.provider = ClassAI()
    student = room.computer(FakeAI())
    student.login("student", "a@x.com", _PW)
    session_id = _student_session(student)
    answer = student.send_ai_message(session_id, "hello", {"main.py": ""})
    assert answer == {"available": True, "text": "class says: hello"}
    with room.factory() as db:
        assert db.query(AIInteraction).one().provider == "class-ai"


def test_a_teacher_sets_the_class_assistant_on_the_server(room: Classroom) -> None:
    fake = FakeAI()
    teacher = room.computer(fake)
    teacher.login("professor", "p@x.com", _PW)
    # Teachers connect the shared assistant: the key goes to the server (the class setting).
    # The server here talks to the real internet, so a bad key must be refused cleanly
    # without the test reaching out: empty key is rejected before any network call.
    refused = teacher.set_ai_provider("openai", "")
    assert refused["ok"] is False
    assert refused["error"] == "Paste your API key first."
    assert teacher.get_ai_settings()["scope"] == "class"


def test_the_restricted_stage_blocks_a_students_own_ai_too(
    room: Classroom, db_session_factory: Factory
) -> None:
    fake = FakeAI()
    student = room.computer(fake)
    student.login("student", "a@x.com", _PW)
    student.set_ai_provider("openai", _KEY)
    with db_session_factory() as db:
        stage_id = next(
            s.id
            for s in db.query(Stage).all()
            if s.ai_assistance_mode.value in ("RESTRICTED", "NONE")
        )
    session_id = int(student.start_stage(stage_id)["session_id"])
    answer = student.send_ai_message(session_id, "just tell me", {"main.py": ""})
    assert answer["available"] is False
    assert "restricted" in answer["error"]
    assert fake.chats == []  # the AI was never contacted


def test_standalone_apps_keep_a_single_assistant(db_session_factory: Factory) -> None:
    seed_demo_content(db_session_factory)
    fake = FakeAI()
    app = ClientApi(
        CavyApi(db_session_factory, EventLogger(db_session_factory), ai_http=fake.client()),
        None,
        ai_http=fake.client(),
    )
    create_student_account(db_session_factory, display_name="S", email="s@x.com", password=_PW)
    app.login("student", "s@x.com", _PW)
    assert app.get_ai_providers()["personal"] is False  # no server: this app's one assistant
    assert app.set_ai_provider("openai", _KEY)["ok"] is True
    assert app.get_ai_settings()["scope"] == "class"


# -- the administrator's class-wide assistant ---------------------------------------------------


def test_admin_can_check_a_key_and_set_the_class_assistant(
    db_engine: Engine, db_session_factory: Factory
) -> None:
    fake = FakeAI()
    state = ServerState(
        db_engine, db_session_factory, EventLogger(db_session_factory), ai_http=fake.client()
    )
    with TestClient(create_app(state)) as http:
        token = http.post(
            "/admin/api/setup", json={"name": "A", "email": "a@x.com", "password": _PW}
        ).json()["token"]
        admin = {"Authorization": f"Bearer {token}"}

        providers = http.get("/admin/api/ai/providers", headers=admin).json()
        assert [p["key"] for p in providers][:4] == ["openai", "anthropic", "gemini", "xai"]

        checked = http.post(
            "/admin/api/ai/check", json={"provider": "openai", "api_key": _KEY}, headers=admin
        ).json()
        assert checked["ok"] and checked["default"] == "gpt-4o-mini"
        wrong = http.post(
            "/admin/api/ai/check", json={"provider": "openai", "api_key": "nope"}, headers=admin
        ).json()
        assert wrong["ok"] is False
        assert (
            http.post(
                "/admin/api/ai/check", json={"provider": "ollama", "api_key": "x"}, headers=admin
            ).status_code
            == 400
        )

        saved = http.post(
            "/admin/api/ai",
            json={"provider": "openai", "api_key": _KEY, "model": "gpt-4o"},
            headers=admin,
        ).json()
        assert saved["ok"] is True
        assert state.ai_holder.provider.model_name == "gpt-4o"
        assert http.get("/admin/api/ai", headers=admin).json()["provider"] == "openai"
        actions = [r["action"] for r in http.get("/admin/api/audit", headers=admin).json()["rows"]]
        assert "set_ai_assistant" in actions
        assert _KEY not in http.get("/admin/api/audit", headers=admin).text  # never logged
