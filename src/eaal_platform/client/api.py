"""The object the web frontend talks to, whichever way data is stored.

Standalone, calls go to an in-process ``CavyApi`` over this computer's own
database. Connected, they go to the central server. The frontend can't tell
the difference: ``ClientApi`` exposes every ``CavyApi`` method by name and
forwards it to the active backend.

Two things always happen on *this* computer, in both modes: running student
code in the sandbox, and showing the "Save as" dialog for exports.
"""

from __future__ import annotations

import base64
import inspect
import os
import subprocess  # nosec B404 - only to hand a file to the OS's own opener
import sys
import tempfile
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from eaal_platform.api.bridge import CavyApi, save_text_file
from eaal_platform.client.remote import RemoteBackend, ServerUnreachableError
from eaal_platform.client.settings import load_server_url, normalise_url, save_server_url
from eaal_platform.db.resources import clean_filename
from eaal_platform.sandbox.executor import run_code as sandbox_run_code
from eaal_platform.server.host import (
    DEFAULT_PORT,
    ServerHost,
    build_state,
    lan_addresses,
    server_db_path,
)

_ENTRY_FILENAME = "main.py"
# Implemented by hand below (or on the backend), not forwarded generically.
_HANDLED_HERE = frozenset({"run_code", "export_lab_report", "login", "logout", "create_account"})


class ClientApi:
    """Exposed to the frontend as ``window.pywebview.api``."""

    def __init__(
        self,
        local_api: CavyApi,
        save_file_dialog: Callable[[str], str | None] | None = None,
        server_url: str | None = None,
    ) -> None:
        self._local = local_api
        self._backend: CavyApi | RemoteBackend = local_api
        self._save_file_dialog = save_file_dialog
        self._server_url: str | None = None
        # Lets this app act as the classroom server too ("Host a server").
        self._host = ServerHost(lambda: build_state(server_db_path()))
        if server_url:
            # Not checked here: if the server is down at startup we must NOT
            # quietly fall back to local data (the student's work would then
            # never reach the server). Calls fail with a clear message instead.
            self._use_server(RemoteBackend(server_url))

    # -- connection ----------------------------------------------------------

    def _use_server(self, backend: RemoteBackend) -> None:
        self._backend = backend
        self._server_url = backend.base_url

    def get_server_settings(self) -> dict[str, Any]:
        return {"mode": "server" if self._server_url else "local", "url": self._server_url}

    def set_server(self, url: str) -> dict[str, Any]:
        """Connect to a server, or (blank address) go back to this computer's own data."""
        if self._backend is not self._local:
            self._backend.logout()
        else:
            self._local.logout()
        cleaned = normalise_url(url)
        if not cleaned:
            self._backend, self._server_url = self._local, None
            save_server_url(None)
            return {"ok": True, **self.get_server_settings()}
        candidate = RemoteBackend(cleaned)
        try:
            candidate.health()
        except ServerUnreachableError as exc:
            return {"ok": False, "error": str(exc), **self.get_server_settings()}
        self._use_server(candidate)
        save_server_url(cleaned)
        return {"ok": True, **self.get_server_settings()}

    def wait_for_updates(self, versions: dict[str, int], wait: bool = True) -> dict[str, Any]:
        """Hold until the server has news (live updates); standalone mode has none.

        ``wait=False`` returns the current state immediately (used once at start-up).
        """
        if self._server_url is None or not isinstance(self._backend, RemoteBackend):
            return {"versions": {}, "live": False}
        return {**self._backend.updates(versions, wait), "live": True}

    # -- hosting a server from this app --------------------------------------------

    def get_hosting(self) -> dict[str, Any]:
        """Is this computer hosting the server, and at which addresses?"""
        port = self._host.port
        if not self._host.running or port is None:
            return {"running": False, "error": self._host.error}
        addresses = [f"{ip}:{port}" for ip in lan_addresses()] or [f"127.0.0.1:{port}"]
        return {
            "running": True,
            "port": port,
            "addresses": addresses,
            "admin_url": f"http://127.0.0.1:{port}/admin",
            "database": str(server_db_path()),
        }

    def start_hosting(self, port: int = DEFAULT_PORT) -> dict[str, Any]:
        """Start the server here and point this app at it."""
        try:
            self._host.start(int(port))
        except OSError as exc:
            return {"ok": False, "error": str(exc), **self.get_hosting()}
        result = self.set_server(f"127.0.0.1:{int(port)}")
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error"), **self.get_hosting()}
        return {"ok": True, **self.get_hosting()}

    def stop_hosting(self) -> dict[str, Any]:
        was_connected_here = self._server_url is not None and self._host.running
        self._host.stop()
        if was_connected_here:
            self.set_server("")  # back to standalone; the local server is gone
        return {"ok": True, **self.get_hosting()}

    def open_admin_panel(self) -> dict[str, Any]:
        """Open the admin panel in the computer's web browser."""
        info = self.get_hosting()
        if not info["running"]:
            return {"ok": False, "error": "The server isn't running on this computer."}
        webbrowser.open(info["admin_url"])
        return {"ok": True}

    def shutdown(self) -> None:
        """Called when the app window closes."""
        self._host.stop()

    # -- calls handled here -------------------------------------------------

    def login(self, role: str, email: str, password: str) -> dict[str, Any]:
        return self._backend.login(role, email, password)

    def logout(self) -> None:
        self._backend.logout()

    def create_account(self, *args: Any) -> dict[str, Any]:
        return self._backend.create_account(*args)

    def run_code(
        self, session_id: int, files: dict[str, str], entry_filename: str = _ENTRY_FILENAME
    ) -> dict[str, Any]:
        """Run the student's code on this computer; the backend only records it."""
        prepared = self._backend.prepare_run(session_id, files, entry_filename)
        outcome = sandbox_run_code(files, entry_filename=prepared["entry_filename"])
        recorded: dict[str, Any] = self._backend.record_run(
            session_id,
            prepared["snapshot_id"],
            prepared["entry_filename"],
            {
                "stdout": outcome.stdout,
                "stderr": outcome.stderr,
                "exit_status": outcome.exit_status,
                "duration_ms": outcome.duration_ms,
                "timed_out": outcome.timed_out,
            },
        )
        return recorded

    # -- shared resources: files and links are handled on this computer ----------------

    def _fetch_resource_file(self, resource_id: int) -> tuple[str, bytes] | dict[str, Any]:
        reply = self._backend.get_resource_file(resource_id)
        if not reply.get("ok"):
            return {"ok": False, "error": reply.get("error") or "That file isn't available."}
        return clean_filename(reply["filename"]), base64.b64decode(reply["data_base64"])

    def open_resource(self, resource_id: int) -> dict[str, Any]:
        """Download a shared file to a temporary folder and open it in the default app."""
        fetched = self._fetch_resource_file(resource_id)
        if isinstance(fetched, dict):
            return fetched
        name, data = fetched
        target = Path(tempfile.mkdtemp(prefix="cavy-resource-")) / name
        target.write_bytes(data)
        try:
            _open_with_system(target)
        except OSError as exc:
            return {"ok": False, "error": f"Couldn't open the file: {exc}"}
        return {"ok": True}

    def save_resource(self, resource_id: int) -> dict[str, Any]:
        """Download a shared file to a place the person chooses."""
        if self._save_file_dialog is None:
            return {"ok": False, "error": "Saving files isn't available in this window."}
        fetched = self._fetch_resource_file(resource_id)
        if isinstance(fetched, dict):
            return fetched
        name, data = fetched
        chosen = self._save_file_dialog(name)
        if not chosen:
            return {"ok": False, "cancelled": True}
        Path(chosen).write_bytes(data)
        return {"ok": True, "path": chosen}

    def open_link(self, url: str) -> dict[str, Any]:
        """Open a web address in the browser (http/https only)."""
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "Only web links can be opened."}
        webbrowser.open(url)
        return {"ok": True}

    def export_lab_report(self, task_id: int) -> dict[str, Any]:
        return save_text_file(self._save_file_dialog, self._backend.get_lab_report_csv(task_id))


def _open_with_system(path: Path) -> None:
    """Hand a file to the operating system's default program for it."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # nosec B606 - a file we just wrote
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)  # nosec B603, B607
    else:
        subprocess.run(["xdg-open", str(path)], check=True)  # nosec B603, B607


def _forwarder(name: str) -> Callable[..., Any]:
    def method(self: ClientApi, *args: Any) -> Any:
        return getattr(self._backend, name)(*args)

    method.__name__ = name
    method.__doc__ = f"Forwarded to the active backend: ``CavyApi.{name}``."
    return method


# pywebview lists an object's functions with ``dir()``, so each forwarded
# method must exist as a real attribute rather than via ``__getattr__``.
for _name, _member in inspect.getmembers(CavyApi, predicate=inspect.isfunction):
    if not _name.startswith("_") and _name not in _HANDLED_HERE and not hasattr(ClientApi, _name):
        setattr(ClientApi, _name, _forwarder(_name))


def build_client_api(
    local_api: CavyApi, save_file_dialog: Callable[[str], str | None] | None
) -> ClientApi:
    """Create the app's API, using the saved server address if one is configured."""
    return ClientApi(local_api, save_file_dialog, load_server_url())
