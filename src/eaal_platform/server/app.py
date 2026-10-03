"""CAVY's central server: one shared database behind an HTTP API.

Each signed-in user gets their own ``CavyApi`` instance (the same
"one identity per window" model the desktop app always used), addressed by
a bearer token instead of a pywebview window. Every route is a thin
translation from JSON to an existing, already-tested ``CavyApi`` method, so
no business logic is duplicated here.

Two methods deliberately never run on the server:

- ``run_code`` — student code executes on the student's own computer; the
  server only records it (``prepare_run`` / ``record_run``).
- ``export_lab_report`` — saving a file is a client-side action; the server
  hands over the CSV text (``get_lab_report_csv``).

Traffic is plain HTTP, intended for a trusted local network.
"""

from __future__ import annotations

import inspect
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, ProviderHolder
from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.models import AuditLog
from eaal_platform.events.logger import EventLogger

API_VERSION = 1
TOKEN_IDLE_SECONDS = 12 * 60 * 60

# Methods that must not be callable over the wire (see module docstring), plus
# the two that manage the token itself.
_BLOCKED_METHODS = frozenset({"run_code", "export_lab_report", "login", "logout"})


def callable_methods() -> frozenset[str]:
    """Names of the ``CavyApi`` methods the server exposes."""
    return frozenset(
        name
        for name, member in inspect.getmembers(CavyApi, predicate=inspect.isfunction)
        if not name.startswith("_") and name not in _BLOCKED_METHODS
    )


# What changed, as named topics the apps subscribe to. Each successful call to
# a method below bumps its topics, waking every app that is waiting for news.
TOPIC_LABS = "labs"  # a lab was created / edited / archived
TOPIC_ACTIVITY = "activity"  # a student started work
TOPIC_SUBMISSIONS = "submissions"  # a student submitted something
TOPIC_ACCOUNTS = "accounts"  # accounts were created or changed
TOPIC_PRESENCE = "presence"  # someone signed in or out
TOPIC_RESOURCES = "resources"  # shared material or class membership changed
_METHOD_TOPICS: dict[str, tuple[str, ...]] = {
    "create_lab": (TOPIC_LABS,),
    "update_lab": (TOPIC_LABS,),
    "archive_lab": (TOPIC_LABS,),
    "unarchive_lab": (TOPIC_LABS,),
    "create_followup_assessment": (TOPIC_LABS,),
    "start_stage": (TOPIC_ACTIVITY,),
    "start_practice": (TOPIC_ACTIVITY,),
    "submit_session": (TOPIC_SUBMISSIONS, TOPIC_ACTIVITY),
    "submit_concept_check": (TOPIC_SUBMISSIONS,),
    "create_resource": (TOPIC_RESOURCES,),
    "update_resource": (TOPIC_RESOURCES,),
    "delete_resource": (TOPIC_RESOURCES,),
    "add_students_to_class": (TOPIC_ACCOUNTS, TOPIC_RESOURCES),
    "remove_student_from_class": (TOPIC_ACCOUNTS, TOPIC_RESOURCES),
    "reset_student_password": (TOPIC_ACCOUNTS,),
    "change_password": (TOPIC_ACCOUNTS,),
}
UPDATE_WAIT_SECONDS = 25.0


@dataclass
class _Client:
    api: CavyApi
    role: str
    name: str
    last_seen: float
    client_id: int = 0
    user_id: int = 0
    address: str = ""
    signed_in_at: float = 0.0
    signed_in_wall: str = ""
    revoked_reason: str | None = None


class ServerState:
    """Everything the server shares between requests."""

    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[OrmSession],
        event_logger: EventLogger,
        ai_provider: AIProvider | None = None,
        clock: Callable[[], float] = time.monotonic,
        ai_http: httpx.Client | None = None,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.event_logger = event_logger
        self.ai_holder = ProviderHolder(ai_provider)
        self.ai_http = ai_http  # tests stand in for the AI companies; None = the real internet
        self._clock = clock
        self._clients: dict[str, _Client] = {}
        self._lock = threading.Lock()
        self._next_client_id = 1
        # Tokens removed by an admin, so the app can say why it was signed out.
        self._revoked: dict[str, str] = {}
        self._versions: dict[str, int] = {}
        self._changed = threading.Condition()

    def new_api(self) -> CavyApi:
        return CavyApi(
            self.session_factory,
            self.event_logger,
            ai_holder=self.ai_holder,
            professors_set_ai=True,
            restrict_signup=True,
            ai_http=self.ai_http,
        )

    def add_client(
        self, api: CavyApi, role: str, name: str, *, user_id: int = 0, address: str = ""
    ) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._expire_locked()
            self._clients[token] = _Client(
                api,
                role,
                name,
                self._clock(),
                client_id=self._next_client_id,
                user_id=user_id,
                address=address,
                signed_in_at=self._clock(),
                signed_in_wall=datetime.now(UTC).isoformat(),
            )
            self._next_client_id += 1
        self.bump(TOPIC_PRESENCE)
        return token

    def revoke_client(self, client_id: int, reason: str) -> bool:
        """Sign someone out from the server side (admin action)."""
        with self._lock:
            token = next((t for t, c in self._clients.items() if c.client_id == client_id), None)
            if token is None:
                return False
            del self._clients[token]
            self._revoked[token] = reason
        self.bump(TOPIC_PRESENCE)
        return True

    def revoke_user(self, role: str, user_id: int, reason: str) -> int:
        """Sign out every active session of one account; returns how many."""
        with self._lock:
            tokens = [
                t for t, c in self._clients.items() if c.role == role and c.user_id == user_id
            ]
            for token in tokens:
                del self._clients[token]
                self._revoked[token] = reason
        if tokens:
            self.bump(TOPIC_PRESENCE)
        return len(tokens)

    def revoked_reason(self, token: str) -> str | None:
        with self._lock:
            return self._revoked.get(token)

    # -- change feed -----------------------------------------------------------

    def bump(self, *topics: str) -> None:
        with self._changed:
            for topic in topics:
                self._versions[topic] = self._versions.get(topic, 0) + 1
            self._changed.notify_all()

    def versions(self) -> dict[str, int]:
        with self._changed:
            return dict(self._versions)

    def wait_for_change(self, known: dict[str, int], timeout: float) -> dict[str, int]:
        """Block until some topic is newer than ``known`` (or ``timeout``); return all versions."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                if any(v > known.get(t, 0) for t, v in self._versions.items()):
                    return dict(self._versions)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return dict(self._versions)
                self._changed.wait(remaining)

    def get_client(self, token: str, *, touch: bool = True) -> _Client | None:
        """The signed-in client for a token. ``touch=False`` doesn't count as activity."""
        with self._lock:
            self._expire_locked()
            client = self._clients.get(token)
            if client is not None and touch:
                client.last_seen = self._clock()
            return client

    def drop_client(self, token: str) -> None:
        with self._lock:
            self._clients.pop(token, None)

    def active_users(self) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            self._expire_locked()
            return [
                {
                    "client_id": c.client_id,
                    "user_id": c.user_id,
                    "role": c.role,
                    "name": c.name,
                    "address": c.address,
                    "signed_in_at": c.signed_in_wall,
                    "idle_seconds": int(now - c.last_seen),
                }
                for c in self._clients.values()
            ]

    def audit(self, actor: str, action: str, detail: str | None = None, address: str = "") -> None:
        """Append one line to the audit log (never raises: logging mustn't break a request)."""
        try:
            with self.session_factory() as db:
                db.add(AuditLog(actor=actor, action=action, detail=detail, address=address))
                db.commit()
        except Exception:  # nosec B110 - audit failures must not block the action itself
            pass

    def _expire_locked(self) -> None:
        cutoff = self._clock() - TOKEN_IDLE_SECONDS
        for token in [t for t, c in self._clients.items() if c.last_seen < cutoff]:
            del self._clients[token]


class LoginRequest(BaseModel):
    role: str
    email: str
    password: str


class CallRequest(BaseModel):
    args: list[Any] = []


class UpdatesRequest(BaseModel):
    versions: dict[str, int] = {}
    wait: bool = True  # False: just report the current state, don't hold the request


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in again.")
    return authorization[7:].strip()


def create_app(state: ServerState) -> FastAPI:
    methods = callable_methods()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        state.event_logger.start()
        try:
            yield
        finally:
            state.event_logger.stop()

    app = FastAPI(title="CAVY server", lifespan=lifespan)
    app.state.cavy = state

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "cavy", "version": API_VERSION}

    def _who(client: _Client) -> str:
        return f"{client.role}:{client.name}"

    def _unauthorised(token: str) -> HTTPException:
        reason = state.revoked_reason(token)
        return HTTPException(
            status_code=401, detail=reason or "Your session expired. Please sign in again."
        )

    @app.post("/api/login")
    def login(request: LoginRequest, http: Request) -> dict[str, Any]:
        address = http.client.host if http.client else ""
        api = state.new_api()
        result = api.login(request.role, request.email, request.password)
        if not result.get("ok"):
            state.audit(f"{request.role}:{request.email}", "login_failed", None, address)
            return result
        identity = api._identity()
        user_id = identity[1] if identity else 0
        name = str(result.get("name", ""))
        result["token"] = state.add_client(
            api, request.role, name, user_id=user_id, address=address
        )
        state.audit(f"{request.role}:{name}", "login", None, address)
        return result

    @app.post("/api/logout")
    def logout(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer(authorization)
        client = state.get_client(token, touch=False)
        if client is not None:
            client.api.logout()
            state.drop_client(token)
            state.audit(_who(client), "logout", None, client.address)
            state.bump(TOPIC_PRESENCE)
        return {"ok": True}

    @app.post("/api/create_account")
    def create_account(payload: CallRequest, http: Request) -> JSONResponse:
        # Sign-up happens before there's a token, so it's the one call that
        # doesn't need one.
        try:
            result = state.new_api().create_account(*payload.args)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=400)
        if result.get("ok"):
            role = str(payload.args[0]) if payload.args else "?"
            state.audit(f"{role}:{payload.args[1] if len(payload.args) > 1 else '?'}", "sign_up")
            state.bump(TOPIC_ACCOUNTS)
        return JSONResponse({"result": result})

    @app.post("/api/updates")
    def updates(
        payload: UpdatesRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """Long poll: returns as soon as something the app cares about changes."""
        token = _bearer(authorization)
        if state.get_client(token, touch=False) is None:
            raise _unauthorised(token)
        versions = state.wait_for_change(
            payload.versions, UPDATE_WAIT_SECONDS if payload.wait else 0.0
        )
        if state.get_client(token, touch=False) is None:
            raise _unauthorised(token)  # signed out by an admin while waiting
        return {"versions": versions}

    @app.post("/api/call/{method}")
    def call(
        method: str, payload: CallRequest, authorization: str | None = Header(default=None)
    ) -> JSONResponse:
        token = _bearer(authorization)
        client = state.get_client(token)
        if client is None:
            raise _unauthorised(token)
        if method not in methods:
            raise HTTPException(status_code=404, detail=f"Unknown method {method!r}")
        try:
            result = getattr(client.api, method)(*payload.args)
        except (ValueError, TypeError, PermissionError) as exc:
            return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=400)
        topics = _METHOD_TOPICS.get(method)
        if topics:
            # Listeners react instantly and may score the work, so the event
            # log must be complete before they are told about it.
            state.event_logger.flush()
            state.bump(*topics)
            state.audit(_who(client), method, None, client.address)
        return JSONResponse({"result": result})

    # The admin panel: JSON API plus its static page.
    from eaal_platform.server.admin import create_admin_router, mount_admin_ui

    app.include_router(create_admin_router(state))
    mount_admin_ui(app)

    return app
