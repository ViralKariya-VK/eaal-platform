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
