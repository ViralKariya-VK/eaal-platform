"""Hosting the server from inside the app."""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.client.api import ClientApi
from eaal_platform.db import approvals
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.host import ServerHost, build_state


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_host_starts_serves_and_stops(tmp_path: Path) -> None:
    host = ServerHost(lambda: build_state(tmp_path / "server.db"))
    port = _free_port()
    host.start(port)
    try:
        assert host.running
        reply = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=5)
        assert reply.json()["service"] == "cavy"
        assert httpx.get(f"http://127.0.0.1:{port}/admin", timeout=5).status_code == 200
    finally:
        host.stop()
    assert not host.running
    # Windows can take a few seconds to refuse a connection, which shows up as a timeout.
    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2)


def test_host_reports_a_port_that_is_taken(tmp_path: Path) -> None:
    with socket.socket() as blocker:
        blocker.bind(("0.0.0.0", 0))
        blocker.listen()
        port = int(blocker.getsockname()[1])
        host = ServerHost(lambda: build_state(tmp_path / "server.db"))
        with pytest.raises(OSError, match="Couldn't start the server"):
            host.start(port)
        assert not host.running
        assert host.error is not None


def test_app_can_host_a_server_and_use_it(
    tmp_path: Path,
    db_session_factory: sessionmaker[OrmSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EAAL_DB_PATH", str(tmp_path / "eaal.db"))  # server.db lands beside it
    monkeypatch.delenv("EAAL_SERVER_URL", raising=False)
    api = ClientApi(CavyApi(db_session_factory, EventLogger(db_session_factory)), None)
    assert api.get_hosting()["running"] is False
    port = _free_port()

    result = api.start_hosting(port)
    try:
        assert result["ok"] is True
        assert result["running"] is True
        assert any(a.endswith(f":{port}") for a in result["addresses"])
        assert api.get_server_settings()["mode"] == "server"  # this app is now connected to itself

        host_state = api._host.state
        assert host_state is not None
        approvals.add_entry(
            host_state.session_factory, "professor", name="Dr K", email="k@example.com"
        )
        api.create_account("professor", "Dr K", "k@example.com", "hunter2-hunter2")
        assert api.login("professor", "k@example.com", "hunter2-hunter2")["ok"] is True
        assert (tmp_path / "server.db").exists()  # data went to the server's file ...
        assert api.get_hosting()["database"] == str(tmp_path / "server.db")
    finally:
        stopped = api.stop_hosting()
    assert stopped["running"] is False
    assert api.get_server_settings()["mode"] == "local"  # ... and the app falls back to standalone


def test_starting_twice_and_open_admin_without_a_server(
    tmp_path: Path, db_session_factory: sessionmaker[OrmSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EAAL_DB_PATH", str(tmp_path / "eaal.db"))
    monkeypatch.delenv("EAAL_SERVER_URL", raising=False)
    api = ClientApi(CavyApi(db_session_factory, EventLogger(db_session_factory)), None)
    assert api.open_admin_panel()["ok"] is False
    port = _free_port()
    try:
        assert api.start_hosting(port)["ok"] is True
        assert api.start_hosting(port)["ok"] is True  # idempotent
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
        assert api.open_admin_panel()["ok"] is True
        assert opened == [f"http://127.0.0.1:{port}/admin"]
    finally:
        api.stop_hosting()


def _make_admin(db_path: Path, email: str = "boss@x.com") -> None:
    from eaal_platform.auth import hash_password
    from eaal_platform.db.models import Admin

    state = build_state(db_path)
    with state.session_factory() as db:
        db.add(Admin(display_name="Boss", email=email, password_hash=hash_password("old-password")))
        db.commit()
    state.engine.dispose()


def test_the_command_creates_the_first_administrator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from eaal_platform.auth import verify_password
    from eaal_platform.db.models import Admin
    from eaal_platform.server.__main__ import reset_admin

    db_path = tmp_path / "server.db"
    assert reset_admin(db_path, None) == 0
    shown = capsys.readouterr().out
    assert "admin@cavy.local" in shown
    password = next(ln.split(":  ")[1] for ln in shown.splitlines() if "New password" in ln)
    state = build_state(db_path)
    with state.session_factory() as db:
        admin = db.query(Admin).one()
        assert verify_password(password, admin.password_hash)
    state.engine.dispose()
    assert reset_admin(db_path, "someone@else.com") == 1  # naming an unknown admin still fails


def test_reset_admin_password_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from eaal_platform.auth import verify_password
    from eaal_platform.db.models import Admin, AuditLog
    from eaal_platform.server.__main__ import reset_admin

    db_path = tmp_path / "server.db"
    _make_admin(db_path)
    assert reset_admin(db_path, None) == 0
    shown = capsys.readouterr().out
    new_password = next(ln.split(":  ")[1] for ln in shown.splitlines() if "New password" in ln)
    state = build_state(db_path)
    with state.session_factory() as db:
        admin = db.query(Admin).one()
        assert verify_password(new_password, admin.password_hash)
        assert not verify_password("old-password", admin.password_hash)
        assert db.query(AuditLog).filter_by(action="reset admin password").count() == 1
        assert new_password not in str([row.detail for row in db.query(AuditLog)])
    state.engine.dispose()


def test_reset_admin_password_with_several_admins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from eaal_platform.server.__main__ import reset_admin

    db_path = tmp_path / "server.db"
    _make_admin(db_path, "a@x.com")
    _make_admin(db_path, "b@x.com")
    assert reset_admin(db_path, None) == 1
    assert "a@x.com" in capsys.readouterr().out
    assert reset_admin(db_path, "nobody@x.com") == 1
    capsys.readouterr()
    assert reset_admin(db_path, "B@X.com") == 0
    assert "b@x.com" in capsys.readouterr().out
