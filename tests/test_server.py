"""The central server and the client that talks to it, wired together in-process."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.client.api import ClientApi
from eaal_platform.client.remote import RemoteBackend, ServerUnreachableError
from eaal_platform.db import approvals
from eaal_platform.db.bootstrap import seed_demo_content
from eaal_platform.db.models import ClassMember, ExecutionResult, Professor, Student
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.app import ServerState, callable_methods, create_app

_PASSWORD = "hunter2-hunter2"


@pytest.fixture
def server(
    db_engine: Engine, db_session_factory: sessionmaker[OrmSession]
) -> Iterator[tuple[TestClient, ServerState]]:
    seed_demo_content(db_session_factory)
    # The server only lets approved emails sign up; approve the ones these tests use.
    approvals.add_entry(db_session_factory, "student", name="Ada", email="ada@example.com")
    approvals.add_entry(db_session_factory, "professor", name="Dr. K", email="k@example.com")
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state)) as http:
        yield http, state


def _remote(http: TestClient) -> RemoteBackend:
    return RemoteBackend("http://testserver", http=http)


def _client_api(
    http: TestClient,
    local_factory: sessionmaker[OrmSession],
    dialog: Any = None,
) -> ClientApi:
    api = ClientApi(CavyApi(local_factory, EventLogger(local_factory)), dialog)
    api._use_server(_remote(http))
    return api


def test_health_identifies_the_service(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    assert _remote(http).health()["service"] == "cavy"


def test_blocked_methods_are_not_callable_remotely(
    server: tuple[TestClient, ServerState],
) -> None:
    assert {"run_code", "export_lab_report", "login", "logout"}.isdisjoint(callable_methods())
    assert {"prepare_run", "record_run", "get_lab_report_csv", "get_my_progress"} <= (
        callable_methods()
    )


def test_calls_require_a_token(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    assert http.post("/api/call/get_labs", json={"args": []}).status_code == 401
    bad = http.post(
        "/api/call/get_labs", json={"args": []}, headers={"Authorization": "Bearer nope"}
    )
    assert bad.status_code == 401


def test_wrong_password_is_rejected_without_a_token(
    server: tuple[TestClient, ServerState],
) -> None:
    http, _ = server
    backend = _remote(http)
    backend.create_account("student", "Ada", "ada@example.com", _PASSWORD)
    result = backend.login("student", "ada@example.com", "wrong-password")
    assert result["ok"] is False
    with pytest.raises(PermissionError):
        backend.call("get_labs")


def test_student_and_professor_share_one_database(
    server: tuple[TestClient, ServerState],
) -> None:
    """A lab made by a professor on one client is visible to a student on another."""
    http, state = server
    professor, student = _remote(http), _remote(http)
    professor.create_account("professor", "Dr. K", "k@example.com", _PASSWORD)
    student.create_account("student", "Ada", "ada@example.com", _PASSWORD)
    assert professor.login("professor", "k@example.com", _PASSWORD)["ok"]
    assert student.login("student", "ada@example.com", _PASSWORD)["ok"]
    # On a server a lab without a course goes to its professor's class.
    with state.session_factory() as db:
        ada = db.query(Student).filter_by(email="ada@example.com").one()
        db.add(
            ClassMember(
                student_id=ada.id,
                professor_id=db.query(Professor).filter_by(email="k@example.com").one().id,
            )
        )
        db.commit()

    before = {lab["title"] for lab in student.call("get_labs")}
    created = professor.call(
        "create_lab",
        {
            "title": "Server-made lab",
            "course": "CS",
            "division": "A",
            "batch": "1",
            "topic": "Loops",
            "description": "d",
            "difficulty": "Easy",
            "stages": [
                {"duration_minutes": 10, "ai_assistance_mode": "FULL"},
                {"duration_minutes": 10, "ai_assistance_mode": "FULL"},
                {"duration_minutes": 10, "ai_assistance_mode": "RESTRICTED"},
            ],
        },
    )
    assert "task_id" in created

    after = {lab["title"] for lab in student.call("get_labs")}
    assert after - before == {"Server-made lab"}


def test_roles_are_enforced_on_the_server(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    student = _remote(http)
    student.create_account("student", "Ada", "ada@example.com", _PASSWORD)
    student.login("student", "ada@example.com", _PASSWORD)
    with pytest.raises(ValueError, match="professor"):
        student.call("get_students")
    with pytest.raises(ValueError, match="professor"):
        student.call("set_ai_provider", "ollama", "")  # class-wide AI is professor-only


def test_unknown_method_is_404_and_run_code_is_not_reachable(
    server: tuple[TestClient, ServerState],
) -> None:
    http, _ = server
    backend = _remote(http)
    backend.create_account("student", "Ada", "ada@example.com", _PASSWORD)
    backend.login("student", "ada@example.com", _PASSWORD)
    with pytest.raises(ValueError, match="Unknown method"):
        backend.call("run_code", 1, {"main.py": "print(1)"})
    with pytest.raises(ValueError, match="Unknown method"):
        backend.call("definitely_not_a_method")


def test_logout_invalidates_the_token(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    backend = _remote(http)
    backend.create_account("student", "Ada", "ada@example.com", _PASSWORD)
    backend.login("student", "ada@example.com", _PASSWORD)
    assert [u["name"] for u in state.active_users()] == ["Ada"]
    old_token = backend._token
    backend.logout()
    assert state.active_users() == []
    reply = http.post(
        "/api/call/get_labs", json={"args": []}, headers={"Authorization": f"Bearer {old_token}"}
    )
    assert reply.status_code == 401


def test_idle_tokens_expire(
    db_engine: Engine, db_session_factory: sessionmaker[OrmSession]
) -> None:
    now = [0.0]
    state = ServerState(
        db_engine, db_session_factory, EventLogger(db_session_factory), clock=lambda: now[0]
    )
    token = state.add_client(state.new_api(), "student", "Ada")
    assert state.get_client(token) is not None
    now[0] += 13 * 60 * 60
    assert state.get_client(token) is None


def test_unreachable_server_gives_a_clear_error() -> None:
    backend = RemoteBackend("http://127.0.0.1:1")  # nothing listens on port 1
    with pytest.raises(ServerUnreachableError, match="Can't reach"):
        backend.health()
    with pytest.raises(ServerUnreachableError, match="Can't reach"):
        backend.login("student", "a@b.c", "x")


# -- client behaviour ---------------------------------------------------------------


def test_code_runs_locally_and_the_server_records_it(
    server: tuple[TestClient, ServerState],
    tmp_path: Any,
) -> None:
    http, state = server
    from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db

    local_engine = create_db_engine(tmp_path / "local.db")
    init_db(local_engine)
    local_factory = create_session_factory(local_engine)

    api = _client_api(http, local_factory)
    api.create_account("student", "Ada", "ada@example.com", _PASSWORD)
    assert api.login("student", "ada@example.com", _PASSWORD)["ok"]
    session_id = api.start_practice()["session_id"]

    result = api.run_code(session_id, {"main.py": "print(6 * 7)"})
    assert result["stdout"].strip() == "42"

    with state.session_factory() as db:
        rows = db.query(ExecutionResult).filter_by(session_id=session_id).all()
    assert [r.stdout.strip() for r in rows] == ["42"]  # recorded on the server
    with local_factory() as db:
        assert db.query(ExecutionResult).count() == 0  # not in the local database
    local_engine.dispose()


def test_export_asks_the_client_where_to_save(
    server: tuple[TestClient, ServerState], tmp_path: Any
) -> None:
    http, _ = server
    from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db

    local_engine = create_db_engine(tmp_path / "local.db")
    init_db(local_engine)
    target = tmp_path / "report.csv"
    api = _client_api(http, create_session_factory(local_engine), dialog=lambda _name: str(target))
    api.create_account("professor", "Dr. K", "k@example.com", _PASSWORD)
    api.login("professor", "k@example.com", _PASSWORD)
    task_id = api.get_professor_labs()[0]["id"]

    result = api.export_lab_report(task_id)

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8-sig").startswith("Student Name,")
    local_engine.dispose()


def test_set_server_checks_the_address_before_switching(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db

    monkeypatch.setenv("EAAL_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.delenv("EAAL_SERVER_URL", raising=False)
    engine = create_db_engine(tmp_path / "local.db")
    init_db(engine)
    factory = create_session_factory(engine)
    api = ClientApi(CavyApi(factory, EventLogger(factory)), None)
    assert api.get_server_settings() == {"mode": "local", "url": None}

    bad = api.set_server("127.0.0.1:1")
    assert bad["ok"] is False
    assert api.get_server_settings()["mode"] == "local"  # unchanged on failure
    engine.dispose()


def test_client_remembers_why_it_was_signed_out(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    backend = _remote(http)
    backend.create_account("student", "Ada", "ada@example.com", _PASSWORD)
    backend.login("student", "ada@example.com", _PASSWORD)
    state.revoke_user("student", state.active_users()[0]["user_id"], "Signed out by an admin.")

    with pytest.raises(PermissionError, match="Signed out by an admin"):
        backend.call("get_labs")
    # Later calls and the live-update poll give the same reason, without hitting the server.
    with pytest.raises(PermissionError, match="Signed out by an admin"):
        backend.call("get_labs")
    with pytest.raises(PermissionError, match="Signed out by an admin"):
        backend.updates({})

    backend.login("student", "ada@example.com", _PASSWORD)
    assert backend.call("get_labs") is not None  # signing in again clears it
