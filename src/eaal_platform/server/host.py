"""Run the CAVY server from inside the desktop app (no terminal needed).

The installed app is the only thing teachers have, so the app itself can
host the classroom server: it starts the same server as
``python -m eaal_platform.server``, in a background thread. It stops when
the app closes, so the hosting computer must stay on with CAVY open.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn

from eaal_platform.ai.ollama_provider import OllamaProvider
from eaal_platform.db.bootstrap import seed_demo_content
from eaal_platform.db.engine import (
    create_db_engine,
    create_session_factory,
    default_db_path,
    init_db,
)
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.app import ServerState, create_app

DEFAULT_PORT = 8000
_START_TIMEOUT_SECONDS = 15.0


def server_db_path() -> Path:
    """The hosted server's database, kept apart from this computer's standalone data."""
    return default_db_path().parent / "server.db"


def lan_addresses() -> list[str]:
    """This computer's address(es) on the local network, for telling other PCs where to connect."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # No packet is sent: connecting a UDP socket only picks the outgoing interface.
            probe.connect(("10.255.255.255", 1))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")]


def build_state(db_path: Path | None) -> ServerState:
    """Open (or create) the database and assemble the server's shared state."""
    engine = create_db_engine(db_path)
    init_db(engine)
    factory = create_session_factory(engine)
    seed_demo_content(factory)
    return ServerState(engine, factory, EventLogger(factory), OllamaProvider())


class ServerHost:
    """Starts and stops the server in a background thread."""

    def __init__(self, build_state: Any) -> None:
        self._build_state = build_state  # () -> ServerState
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._state: ServerState | None = None
        self.port: int | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.port is not None

    def start(self, port: int = DEFAULT_PORT) -> None:
        if self.running:
            return
        self.error = None
        state: ServerState = self._build_state()
        config = uvicorn.Config(
            create_app(state),
            host="0.0.0.0",
            port=port,
            log_level="warning",  # nosec B104 - LAN
        )
        server = uvicorn.Server(config)
        failure: list[BaseException] = []

        def run() -> None:
            try:
                server.run()
            except BaseException as exc:
                failure.append(exc)

        thread = threading.Thread(target=run, name="cavy-server", daemon=True)
        thread.start()
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if server.started:
                break
            if not thread.is_alive():
                break
            time.sleep(0.05)
        if not server.started:
            server.should_exit = True
            state.engine.dispose()
            reason = str(failure[0]) if failure else ""
            # uvicorn exits with SystemExit(1) after logging "address already in use".
            self.error = (
                f"Couldn't start the server on port {port}. Another program may already be "
                f"using it. {reason}".strip()
            )
            raise OSError(self.error)
        self._server, self._thread, self._state, self.port = server, thread, state, port

    def stop(self) -> None:
        server, thread, state = self._server, self._thread, self._state
        self._server = self._thread = self._state = None
        self.port = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        if state is not None:
            state.engine.dispose()

    @property
    def state(self) -> ServerState | None:
        return self._state
