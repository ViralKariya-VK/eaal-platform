"""The admin panel's API: sign-in, user management, and the database browser."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    seed_demo_content,
    start_practice_session,
)
from eaal_platform.db.models import Admin, EventType
from eaal_platform.events.logger import EventLogger, PendingEvent
from eaal_platform.server.app import ServerState, create_app

_PASSWORD = "hunter2-hunter2"


@pytest.fixture
def server(
    db_engine: Engine, db_session_factory: sessionmaker[OrmSession]
) -> Iterator[tuple[TestClient, ServerState]]:
    seed_demo_content(db_session_factory)
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state)) as http:
        yield http, state


def _setup(http: TestClient) -> dict[str, str]:
    reply = http.post(
        "/admin/api/setup",
        json={"name": "Root", "email": "Root@Example.com", "password": _PASSWORD},
    )
    assert reply.status_code == 200
    return {"Authorization": f"Bearer {reply.json()['token']}"}


def test_first_visit_needs_setup_then_never_again(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    assert http.get("/admin/api/status").json() == {"needs_setup": True}
    _setup(http)
    assert http.get("/admin/api/status").json() == {"needs_setup": False}
    again = http.post(
        "/admin/api/setup", json={"name": "Evil", "email": "e@x.com", "password": _PASSWORD}
    )
    assert again.status_code == 403


def test_setup_rejects_a_weak_password(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    reply = http.post("/admin/api/setup", json={"name": "A", "email": "a@x.com", "password": "x"})
    assert reply.status_code == 400
    assert http.get("/admin/api/status").json()["needs_setup"] is True


def test_admin_passwords_are_stored_hashed(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    _setup(http)
    with state.session_factory() as db:
        admin = db.query(Admin).one()
        assert admin.email == "root@example.com"
        assert _PASSWORD not in admin.password_hash


def test_every_admin_route_requires_sign_in(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    for path in ("/overview", "/users", "/labs", "/tables", "/tables/students", "/ai", "/backup"):
        assert http.get(f"/admin/api{path}").status_code == 401, path
    bad = {"Authorization": "Bearer forged"}
    assert http.get("/admin/api/users", headers=bad).status_code == 401


def test_an_app_login_token_does_not_open_the_admin_panel(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    create_professor_account(
        state.session_factory, display_name="P", email="p@x.com", password=_PASSWORD
    )
    token = http.post(
        "/api/login", json={"role": "professor", "email": "p@x.com", "password": _PASSWORD}
    ).json()["token"]
    reply = http.get("/admin/api/users", headers={"Authorization": f"Bearer {token}"})
    assert reply.status_code == 401


def test_login_and_lockout_after_repeated_failures(
    server: tuple[TestClient, ServerState],
) -> None:
    http, _ = server
    _setup(http)
    good = http.post("/admin/api/login", json={"email": "root@example.com", "password": _PASSWORD})
    assert good.status_code == 200
    for _ in range(5):
        wrong = http.post("/admin/api/login", json={"email": "root@example.com", "password": "no"})
        assert wrong.status_code == 401
    locked = http.post(
        "/admin/api/login", json={"email": "root@example.com", "password": _PASSWORD}
    )
    assert locked.status_code == 429


def test_admin_resets_a_professor_password(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    headers = _setup(http)
    professor_id = create_professor_account(
        state.session_factory, display_name="Dr K", email="k@x.com", password=_PASSWORD
    )

    reply = http.post(
        "/admin/api/users/reset-password",
        json={"role": "professor", "id": professor_id},
        headers=headers,
    )
    temporary = reply.json()["temporary_password"]

    old = http.post(
        "/api/login", json={"role": "professor", "email": "k@x.com", "password": _PASSWORD}
    )
    assert old.json()["ok"] is False
    new = http.post(
        "/api/login", json={"role": "professor", "email": "k@x.com", "password": temporary}
    ).json()
    assert new["ok"] is True
    assert new["must_change_password"] is True

    listed = http.get("/admin/api/users", headers=headers).json()
    assert listed["professors"][0]["must_change_password"] is True


def test_reset_unknown_user_is_404(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    headers = _setup(http)
    reply = http.post(
        "/admin/api/users/reset-password", json={"role": "student", "id": 999}, headers=headers
    )
    assert reply.status_code == 404
    odd = http.post(
        "/admin/api/users/reset-password", json={"role": "wizard", "id": 1}, headers=headers
    )
    assert odd.status_code == 400


def test_database_browser_hides_password_hashes(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    headers = _setup(http)
    create_student_account(
        state.session_factory, display_name="Ada", email="ada@x.com", password=_PASSWORD
    )
    listing = {t["name"]: t for t in http.get("/admin/api/tables", headers=headers).json()}
    assert "admins" in listing
    assert "password_hash" not in {c["name"] for c in listing["students"]["columns"]}

    rows = http.get("/admin/api/tables/students", headers=headers).json()
    assert "password_hash" not in {c["name"] for c in rows["columns"]}
    assert _PASSWORD not in str(rows)
    export = http.get("/admin/api/export/students.csv", headers=headers).text
    assert "Ada" in export
    assert "password_hash" not in export
    assert _PASSWORD not in export


def test_database_browser_search_sort_filter_and_unknown_inputs(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    headers = _setup(http)
    for name in ("Ada", "Bob", "Cy"):
        create_student_account(
            state.session_factory, display_name=name, email=f"{name}@x.com", password=_PASSWORD
        )
    base = "/admin/api/tables/students"

    found = http.get(f"{base}?q=bob", headers=headers).json()
    assert found["total"] == 1

    names = http.get(f"{base}?sort=display_name&desc=false", headers=headers).json()
    idx = [c["name"] for c in names["columns"]].index("display_name")
    assert [r[idx] for r in names["rows"] if r[idx] in ("Ada", "Bob", "Cy")] == ["Ada", "Bob", "Cy"]

    filtered = http.get(f"{base}?filter_column=id&filter_value=1", headers=headers).json()
    assert filtered["total"] == 1

    assert http.get(f"{base}?filter_column=nope&filter_value=1", headers=headers).status_code == 400
    assert http.get("/admin/api/tables/nope", headers=headers).status_code == 404
    # A sort column that doesn't exist falls back safely instead of reaching SQL.
    assert http.get(f"{base}?sort=1;drop table students", headers=headers).status_code == 200
    assert http.get("/admin/api/tables/students/9999", headers=headers).status_code == 404


def test_one_row_shows_full_untrimmed_text(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    headers = _setup(http)
    student_id = create_student_account(
        state.session_factory, display_name="Ada", email="ada@x.com", password=_PASSWORD
    )
    session_id = start_practice_session(state.session_factory, student_id)
    from eaal_platform.db.models import CodeSnapshot

    long_code = "x = 1\n" * 100
    with state.session_factory() as db:
        db.add(CodeSnapshot(session_id=session_id, content=long_code))
        db.commit()
    listing = http.get("/admin/api/tables/code_snapshots", headers=headers).json()
    content_idx = [c["name"] for c in listing["columns"]].index("content")
    assert listing["rows"][0][content_idx].endswith("…")  # trimmed in the list
    row = http.get("/admin/api/tables/code_snapshots/1", headers=headers).json()
    assert row["content"] == long_code  # complete in the detail view


def test_session_detail_collects_everything(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    headers = _setup(http)
    student_id = create_student_account(
        state.session_factory, display_name="Ada", email="ada@x.com", password=_PASSWORD
    )
    session_id = start_practice_session(state.session_factory, student_id)
    state.event_logger.log(PendingEvent(session_id=session_id, event_type=EventType.CODE_RUN))
    state.event_logger.stop()
    state.event_logger.start()

    detail = http.get(f"/admin/api/sessions/{session_id}", headers=headers).json()
    assert detail["session"]["student"] == "Ada"
    assert [e["type"] for e in detail["events"]] == ["CODE_RUN"]
    assert detail["ai_interactions"] == []
    assert http.get("/admin/api/sessions/9999", headers=headers).status_code == 404


def test_overview_and_labs_and_archiving(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    headers = _setup(http)
    create_student_account(
        state.session_factory, display_name="Ada", email="ada@x.com", password=_PASSWORD
    )
    overview = http.get("/admin/api/overview", headers=headers).json()
    assert overview["counts"]["students"] == 1
    assert overview["counts"]["labs"] >= 1
    assert overview["signed_in"] == []

    labs = http.get("/admin/api/labs", headers=headers).json()
    lab_id = labs[0]["id"]
    assert labs[0]["archived"] is False
    http.post(f"/admin/api/labs/{lab_id}/archive", json={"archived": True}, headers=headers)
    assert http.get("/admin/api/labs", headers=headers).json()[0]["archived"] is True
    assert (
        http.post(
            "/admin/api/labs/9999/archive", json={"archived": True}, headers=headers
        ).status_code
        == 404
    )


def test_ai_settings_can_be_read_and_validated(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    headers = _setup(http)
    assert "available" in http.get("/admin/api/ai", headers=headers).json()
    blank = http.post("/admin/api/ai", json={"provider": "groq", "api_key": ""}, headers=headers)
    assert blank.json()["ok"] is False
    unknown = http.post("/admin/api/ai", json={"provider": "magic"}, headers=headers)
    assert unknown.status_code == 400


def test_backup_is_a_valid_database_copy(
    server: tuple[TestClient, ServerState], tmp_path: Path
) -> None:
    http, state = server
    headers = _setup(http)
    create_student_account(
        state.session_factory, display_name="Ada", email="ada@x.com", password=_PASSWORD
    )
    reply = http.get("/admin/api/backup", headers=headers)
    assert reply.status_code == 200
    copy = tmp_path / "copy.db"
    copy.write_bytes(reply.content)
    with sqlite3.connect(copy) as conn:
        assert (
            conn.execute("select count(*) from students where email='ada@x.com'").fetchone()[0] == 1
        )


def test_admin_page_is_served(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    page = http.get("/admin")
    assert page.status_code == 200
    assert "CAVY Admin" in page.text
    assert http.get("/admin/ui/admin.js").status_code == 200
    root = http.get("/", follow_redirects=False)
    assert root.headers["location"] == "/admin"


# -- live updates, sign-out and account management ----------------------------------------


def _app_login(http: TestClient, role: str, email: str) -> dict[str, str]:
    reply = http.post("/api/login", json={"role": role, "email": email, "password": _PASSWORD})
    assert reply.json()["ok"] is True, reply.json()
    return {"Authorization": f"Bearer {reply.json()['token']}"}


def test_updates_wake_when_a_lab_is_created(server: tuple[TestClient, ServerState]) -> None:
    import threading

    http, state = server
    create_professor_account(
        state.session_factory, display_name="P", email="p@x.com", password=_PASSWORD
    )
    create_student_account(
        state.session_factory, display_name="S", email="s@x.com", password=_PASSWORD
    )
    professor = _app_login(http, "professor", "p@x.com")
    student = _app_login(http, "student", "s@x.com")

    start = http.post("/api/updates", json={"versions": {}}, headers=student).json()["versions"]
    got: dict[str, dict[str, int]] = {}

    def wait() -> None:
        got["v"] = http.post("/api/updates", json={"versions": start}, headers=student).json()[
            "versions"
        ]

    waiter = threading.Thread(target=wait)
    waiter.start()
    lab = {
        "title": "Live lab",
        "course": "CS",
        "division": "A",
        "batch": "1",
        "topic": "t",
        "description": "d",
        "difficulty": "Easy",
        "stages": [
            {"duration_minutes": 5, "ai_assistance_mode": "FULL"},
            {"duration_minutes": 5, "ai_assistance_mode": "FULL"},
            {"duration_minutes": 5, "ai_assistance_mode": "RESTRICTED"},
        ],
    }
    http.post("/api/call/create_lab", json={"args": [lab]}, headers=professor)
    waiter.join(timeout=5)
    assert not waiter.is_alive()
    assert got["v"]["labs"] > start.get("labs", 0)


def test_updates_return_quickly_when_something_is_already_newer(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    create_student_account(
        state.session_factory, display_name="S", email="s@x.com", password=_PASSWORD
    )
    student = _app_login(http, "student", "s@x.com")
    state.bump("labs")
    reply = http.post("/api/updates", json={"versions": {"labs": 0}}, headers=student)
    assert reply.json()["versions"]["labs"] >= 1


def test_updates_require_a_valid_token(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    assert http.post("/api/updates", json={"versions": {}}).status_code == 401


def test_admin_sign_out_ends_the_session_with_a_reason(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    admin = _setup(http)
    create_student_account(
        state.session_factory, display_name="S", email="s@x.com", password=_PASSWORD
    )
    student = _app_login(http, "student", "s@x.com")
    who = http.get("/admin/api/live", headers=admin).json()["signed_in"]
    assert [u["name"] for u in who] == ["S"]

    out = http.post(f"/admin/api/signed-in/{who[0]['client_id']}/sign-out", headers=admin)
    assert out.status_code == 200

    after = http.post("/api/call/get_labs", json={"args": []}, headers=student)
    assert after.status_code == 401
    assert "signed out by an administrator" in after.json()["detail"]
    assert http.post("/api/updates", json={"versions": {}}, headers=student).status_code == 401
    assert http.get("/admin/api/live", headers=admin).json()["signed_in"] == []
    again = http.post(f"/admin/api/signed-in/{who[0]['client_id']}/sign-out", headers=admin)
    assert again.status_code == 404


def test_sign_out_everyone(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    admin = _setup(http)
    for name in ("a", "b"):
        create_student_account(
            state.session_factory, display_name=name, email=f"{name}@x.com", password=_PASSWORD
        )
        _app_login(http, "student", f"{name}@x.com")
    result = http.post("/admin/api/signed-in/sign-out-all", headers=admin).json()
    assert result["signed_out"] == 2
    assert http.get("/admin/api/live", headers=admin).json()["signed_in"] == []


def test_disabling_blocks_login_and_signs_the_person_out(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    admin = _setup(http)
    student_id = create_student_account(
        state.session_factory, display_name="S", email="s@x.com", password=_PASSWORD
    )
    token = _app_login(http, "student", "s@x.com")

    http.post(
        "/admin/api/users/disable",
        json={"role": "student", "id": student_id, "disabled": True},
        headers=admin,
    )

    assert http.post("/api/call/get_labs", json={"args": []}, headers=token).status_code == 401
    blocked = http.post(
        "/api/login", json={"role": "student", "email": "s@x.com", "password": _PASSWORD}
    ).json()
    assert blocked["ok"] is False
    assert "disabled" in blocked["error"]
    # A wrong password must not reveal that the account is disabled.
    wrong = http.post(
        "/api/login", json={"role": "student", "email": "s@x.com", "password": "nope-nope-1"}
    ).json()
    assert "disabled" not in wrong["error"]

    http.post(
        "/admin/api/users/disable",
        json={"role": "student", "id": student_id, "disabled": False},
        headers=admin,
    )
    _app_login(http, "student", "s@x.com")


def test_admin_edits_credentials(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    admin = _setup(http)
    student_id = create_student_account(
        state.session_factory, display_name="Old", email="old@x.com", password=_PASSWORD
    )
    create_student_account(
        state.session_factory, display_name="Other", email="taken@x.com", password=_PASSWORD
    )
    token = _app_login(http, "student", "old@x.com")

    clash = http.post(
        "/admin/api/users/update",
        json={"role": "student", "id": student_id, "email": "taken@x.com"},
        headers=admin,
    )
    assert clash.status_code == 400
    weak = http.post(
        "/admin/api/users/update",
        json={"role": "student", "id": student_id, "new_password": "short"},
        headers=admin,
    )
    assert weak.status_code == 400

    done = http.post(
        "/admin/api/users/update",
        json={
            "role": "student",
            "id": student_id,
            "name": "New Name",
            "email": "new@x.com",
            "new_password": "brand-new-pass-1",
            "must_change_password": True,
        },
        headers=admin,
    ).json()
    assert set(done["changed"]) == {"name", "email", "password"}
    assert done["signed_out"] == 1
    assert http.post("/api/call/get_labs", json={"args": []}, headers=token).status_code == 401

    login = http.post(
        "/api/login",
        json={"role": "student", "email": "new@x.com", "password": "brand-new-pass-1"},
    ).json()
    assert login["ok"] is True
    assert login["name"] == "New Name"
    assert login["must_change_password"] is True


def test_deleting_accounts(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    admin = _setup(http)
    with_work = create_student_account(
        state.session_factory, display_name="Busy", email="b@x.com", password=_PASSWORD
    )
    start_practice_session(state.session_factory, with_work)
    idle = create_student_account(
        state.session_factory, display_name="Idle", email="i@x.com", password=_PASSWORD
    )
    prof = create_professor_account(
        state.session_factory, display_name="P", email="p@x.com", password=_PASSWORD
    )

    refused = http.post(
        "/admin/api/users/delete", json={"role": "student", "id": with_work}, headers=admin
    )
    assert refused.status_code == 400
    assert "Disable" in refused.json()["detail"]
    assert (
        http.post(
            "/admin/api/users/delete", json={"role": "student", "id": idle}, headers=admin
        ).status_code
        == 200
    )
    assert (
        http.post(
            "/admin/api/users/delete", json={"role": "professor", "id": prof}, headers=admin
        ).status_code
        == 200
    )
    users = http.get("/admin/api/users", headers=admin).json()
    assert [u["name"] for u in users["students"]] == ["Busy"]
    assert users["professors"] == []


def test_admin_can_create_accounts(server: tuple[TestClient, ServerState]) -> None:
    http, _ = server
    admin = _setup(http)
    ok = http.post(
        "/admin/api/users/create",
        json={"role": "student", "name": "Made", "email": "made@x.com", "password": _PASSWORD},
        headers=admin,
    )
    assert ok.status_code == 200
    _app_login(http, "student", "made@x.com")
    dup = http.post(
        "/admin/api/users/create",
        json={"role": "student", "name": "Dup", "email": "made@x.com", "password": _PASSWORD},
        headers=admin,
    )
    assert dup.status_code == 400


def test_audit_log_records_actions_without_secrets(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    admin = _setup(http)
    student_id = create_student_account(
        state.session_factory, display_name="S", email="s@x.com", password=_PASSWORD
    )
    _app_login(http, "student", "s@x.com")
    http.post(
        "/api/login", json={"role": "student", "email": "s@x.com", "password": "wrong-pass-1"}
    )
    result = http.post(
        "/admin/api/users/reset-password", json={"role": "student", "id": student_id}, headers=admin
    ).json()

    log = http.get("/admin/api/audit", headers=admin).json()
    actions = [row["action"] for row in log["rows"]]
    assert {"login", "login_failed", "reset_password"} <= set(actions)
    assert result["temporary_password"] not in str(log)
    assert _PASSWORD not in str(log)


def test_live_view_lists_who_is_working_and_what(server: tuple[TestClient, ServerState]) -> None:
    http, state = server
    admin = _setup(http)
    student_id = create_student_account(
        state.session_factory, display_name="Ada", email="a@x.com", password=_PASSWORD
    )
    session_id = start_practice_session(state.session_factory, student_id)
    state.event_logger.log(PendingEvent(session_id=session_id, event_type=EventType.CODE_RUN))
    state.event_logger.stop()
    state.event_logger.start()

    live = http.get("/admin/api/live", headers=admin).json()
    assert [w["student"] for w in live["working_now"]] == ["Ada"]
    assert live["working_now"][0]["task"] == "Practice"
    assert live["accounts"]["students"] == 1
    assert live["feed"][0]["type"] == "CODE_RUN"

    labs = http.get("/admin/api/labs", headers=admin).json()
    assert {"students", "in_progress", "submitted"} <= set(labs[0])


# -- classes and shared resources (admin side, and server live updates) ----------------------------


def _note_spec(title: str) -> dict[str, object]:
    return {"kind": "NOTE", "title": title, "body": "text"}


def test_admin_assigns_students_and_the_access_rules_follow(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    admin = _setup(http)
    p1 = create_professor_account(
        state.session_factory, display_name="P1", email="p1@x.com", password=_PASSWORD
    )
    p2 = create_professor_account(
        state.session_factory, display_name="P2", email="p2@x.com", password=_PASSWORD
    )
    sid = create_student_account(
        state.session_factory, display_name="S", email="s@x.com", password=_PASSWORD
    )
    prof1 = _app_login(http, "professor", "p1@x.com")
    student = _app_login(http, "student", "s@x.com")
    http.post("/api/call/create_resource", json={"args": [_note_spec("Hello")]}, headers=prof1)
    assert (
        http.post("/api/call/get_student_resources", json={"args": []}, headers=student).json()[
            "result"
        ]
        == []
    )

    ok = http.post(
        "/admin/api/users/assign", json={"student_id": sid, "professor_ids": [p1]}, headers=admin
    )
    assert ok.status_code == 200
    seen = http.post("/api/call/get_student_resources", json={"args": []}, headers=student).json()[
        "result"
    ]
    assert [r["title"] for r in seen] == ["Hello"]

    http.post(
        "/admin/api/users/assign", json={"student_id": sid, "professor_ids": [p2]}, headers=admin
    )
    gone = http.post("/api/call/get_student_resources", json={"args": []}, headers=student).json()[
        "result"
    ]
    assert gone == []
    users = http.get("/admin/api/users", headers=admin).json()
    assert users["students"][0]["professor_ids"] == [p2]
    assert {p["name"]: p["students"] for p in users["professors"]} == {"P1": 0, "P2": 1}

    # A student has a professor for each subject, so several are allowed.
    http.post(
        "/admin/api/users/assign",
        json={"student_id": sid, "professor_ids": [p1, p2]},
        headers=admin,
    )
    assert http.get("/admin/api/users", headers=admin).json()["students"][0][
        "professor_ids"
    ] == sorted([p1, p2])
    assert [
        r["title"]
        for r in http.post(
            "/api/call/get_student_resources", json={"args": []}, headers=student
        ).json()["result"]
    ] == ["Hello"]

    bad = http.post(
        "/admin/api/users/assign", json={"student_id": sid, "professor_ids": [999]}, headers=admin
    )
    assert bad.status_code == 400
    http.post(
        "/admin/api/users/assign", json={"student_id": sid, "professor_ids": []}, headers=admin
    )
    assert http.get("/admin/api/users", headers=admin).json()["students"][0]["professor_ids"] == []


def test_resources_wake_listeners_and_appear_in_the_admin_list(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    admin = _setup(http)
    create_professor_account(
        state.session_factory, display_name="P1", email="p1@x.com", password=_PASSWORD
    )
    prof = _app_login(http, "professor", "p1@x.com")
    before = http.post("/api/updates", json={"versions": {}, "wait": False}, headers=prof).json()[
        "versions"
    ]

    created = http.post(
        "/api/call/create_resource", json={"args": [_note_spec("Syllabus")]}, headers=prof
    )
    assert created.json()["result"]["ok"] is True

    after = http.post("/api/updates", json={"versions": before}, headers=prof).json()["versions"]
    assert after["resources"] > before.get("resources", 0)
    listed = http.get("/admin/api/resources", headers=admin).json()
    assert [(r["title"], r["owner"], r["shared_with"]) for r in listed] == [
        ("Syllabus", "P1", "whole class")
    ]

    removed = http.post(f"/admin/api/resources/{listed[0]['id']}/delete", headers=admin)
    assert removed.status_code == 200
    assert http.get("/admin/api/resources", headers=admin).json() == []
    assert http.post("/admin/api/resources/999/delete", headers=admin).status_code == 404
    actions = [r["action"] for r in http.get("/admin/api/audit", headers=admin).json()["rows"]]
    assert "delete_resource" in actions
    assert "create_resource" in actions


def test_file_bytes_never_appear_in_the_database_browser(
    server: tuple[TestClient, ServerState],
) -> None:
    import base64

    http, state = server
    admin = _setup(http)
    create_professor_account(
        state.session_factory, display_name="P1", email="p1@x.com", password=_PASSWORD
    )
    prof = _app_login(http, "professor", "p1@x.com")
    spec = {
        "kind": "FILE",
        "title": "Secret notes",
        "filename": "a.pdf",
        "data_base64": base64.b64encode(b"TOP-SECRET-BYTES").decode(),
    }
    http.post("/api/call/create_resource", json={"args": [spec]}, headers=prof)

    listing = http.get("/admin/api/tables", headers=admin).json()
    files = next(t for t in listing if t["name"] == "resource_files")
    assert "data" not in {c["name"] for c in files["columns"]}
    assert files["rows"] == 1
    assert "TOP-SECRET" not in http.get("/admin/api/tables/resource_files", headers=admin).text
    assert "TOP-SECRET" not in http.get("/admin/api/tables/resource_files/1", headers=admin).text
    assert "TOP-SECRET" not in http.get("/admin/api/export/resource_files.csv", headers=admin).text


def test_deleting_a_professor_frees_their_students_and_removes_their_material(
    server: tuple[TestClient, ServerState],
) -> None:
    http, state = server
    admin = _setup(http)
    pid = create_professor_account(
        state.session_factory, display_name="P1", email="p1@x.com", password=_PASSWORD
    )
    sid = create_student_account(
        state.session_factory, display_name="S", email="s@x.com", password=_PASSWORD
    )
    http.post(
        "/admin/api/users/assign", json={"student_id": sid, "professor_ids": [pid]}, headers=admin
    )
    prof = _app_login(http, "professor", "p1@x.com")
    http.post("/api/call/create_resource", json={"args": [_note_spec("Gone soon")]}, headers=prof)

    done = http.post(
        "/admin/api/users/delete", json={"role": "professor", "id": pid}, headers=admin
    )

    assert done.status_code == 200
    assert http.get("/admin/api/resources", headers=admin).json() == []
    assert http.get("/admin/api/users", headers=admin).json()["students"][0]["professor_ids"] == []
