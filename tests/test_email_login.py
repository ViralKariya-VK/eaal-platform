"""Students' first sign-in by email: settings, sending, the request flow, first login."""

from __future__ import annotations

import smtplib
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform import mailer
from eaal_platform.api.bridge import CavyApi
from eaal_platform.client.remote import RemoteBackend
from eaal_platform.db import academics, approvals
from eaal_platform.db.bootstrap import create_professor_account, seed_demo_content
from eaal_platform.db.models import AuditLog, Student
from eaal_platform.events.logger import EventLogger
from eaal_platform.server import onboarding
from eaal_platform.server.app import ServerState, create_app

Factory = sessionmaker[OrmSession]
_PW = "hunter2-hunter2"
_SETTINGS = mailer.MailSettings("smtp.test", 587, "cavy@gmail.com", "apppassword", "CAVY Team")


# -- sending --------------------------------------------------------------------------------


class _Smtp:
    sent: ClassVar[list[Any]] = []
    steps: ClassVar[list[str]] = []
    fail_login = False

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    def __enter__(self) -> _Smtp:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def ehlo(self) -> None:
        self.steps.append("ehlo")

    def starttls(self, context: object = None) -> None:
        self.steps.append("starttls")

    def login(self, user: str, password: str) -> None:
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        self.steps.append(f"login:{user}")

    def send_message(self, message: Any) -> None:
        self.sent.append(message)


def test_a_message_is_built_and_sent_over_starttls() -> None:
    _Smtp.sent, _Smtp.steps, _Smtp.fail_login = [], [], False
    mailer.send_mail(_SETTINGS, "ada@uni.edu", "Hi", "Body text", smtp_factory=_Smtp)
    message = _Smtp.sent[0]
    assert message["To"] == "ada@uni.edu" and message["Subject"] == "Hi"
    assert "CAVY Team" in message["From"] and "cavy@gmail.com" in message["From"]
    assert _Smtp.steps == ["ehlo", "starttls", "ehlo", "login:cavy@gmail.com"]


def test_a_refused_password_is_explained() -> None:
    _Smtp.fail_login = True
    with pytest.raises(mailer.MailError, match="app password"):
        mailer.send_mail(_SETTINGS, "a@b.c", "s", "b", smtp_factory=_Smtp)
    _Smtp.fail_login = False


def test_settings_keep_the_password_when_left_blank(db_session_factory: Factory) -> None:
    assert mailer.load_settings(db_session_factory) is None
    assert mailer.describe(db_session_factory)["configured"] is False
    with pytest.raises(mailer.MailError, match="app password"):
        mailer.save_settings(
            db_session_factory,
            host="smtp.gmail.com",
            port=587,
            username="x@gmail.com",
            password="",
            from_name="",
        )
    mailer.save_settings(
        db_session_factory,
        host="smtp.gmail.com",
        port=587,
        username="x@gmail.com",
        password="abcd efgh ijkl mnop",
        from_name="",
    )
    mailer.save_settings(
        db_session_factory,
        host="smtp.gmail.com",
        port=587,
        username="y@gmail.com",
        password=None,
        from_name="Team",
    )
    saved = mailer.load_settings(db_session_factory)
    assert saved is not None
    assert (saved.username, saved.password, saved.from_name) == (
        "y@gmail.com",
        "abcdefghijklmnop",  # spaces from Google's display are dropped
        "Team",
    )
    shown = mailer.describe(db_session_factory)
    assert "password" not in shown and shown["has_password"] is True


# -- the request flow -----------------------------------------------------------------------


@pytest.fixture
def server(
    db_engine: Engine, db_session_factory: Factory
) -> Iterator[tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]]]:
    seed_demo_content(db_session_factory)
    mailer.save_settings(
        db_session_factory,
        host="smtp.test",
        port=587,
        username="cavy@gmail.com",
        password="apppassword",
        from_name="CAVY Team",
    )
    approvals.add_entry(
        db_session_factory, "student", name="Ada Lovelace", email="Ada@Uni.edu", enrollment_no="E1"
    )
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    outbox: list[tuple[str, str]] = []
    state.send_mail = lambda settings, to, subject, body: outbox.append((to, body))  # type: ignore[assignment,misc]
    with TestClient(create_app(state)) as http:
        token = http.post(
            "/admin/api/setup", json={"name": "R", "email": "r@x.com", "password": _PW}
        ).json()["token"]
        yield http, state, outbox, {"Authorization": f"Bearer {token}"}


def _password_in(body: str) -> str:
    return next(line.split()[-1] for line in body.splitlines() if "Temporary password" in line)


def test_signup_info_tells_the_app_to_use_email(
    server: tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]],
) -> None:
    http, state, _, _ = server
    backend = RemoteBackend("http://testserver", http=http)
    assert backend.get_signup_info() == {"email_signup": True}
    with state.session_factory() as db:
        from eaal_platform.db.models import EmailSettings

        db.query(EmailSettings).delete()
        db.commit()
    assert backend.get_signup_info() == {"email_signup": False}


def test_the_first_login_email_creates_the_account_and_the_student_signs_in(
    server: tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]],
    db_session_factory: Factory,
) -> None:
    http, _, outbox, _ = server
    academics.add_course(db_session_factory, "Degree", 3)
    reply = http.post("/api/request_login", json={"email": "ADA@uni.edu"})
    assert reply.status_code == 200 and reply.json()["result"]["created"] is True
    to, body = outbox[0]
    assert to == "ada@uni.edu" and "Ada Lovelace" in body and "ada@uni.edu" in body
    temporary = _password_in(body)

    student = RemoteBackend("http://testserver", http=http)
    login = student.login("student", "ada@uni.edu", temporary)
    assert login["ok"] and login["must_change_password"] and login["needs_details"]
    assert student.call("get_my_progress")["totals"]["sessions"] == 0  # can already use the app

    course = academics.list_courses(db_session_factory)[0]["id"]
    done = student.call(
        "complete_first_login",
        "my-new-password",
        {"course_id": course, "year": 2, "roll_number": "27"},
    )
    assert done == {"ok": True}
    with db_session_factory() as db:
        row = db.query(Student).filter_by(email="ada@uni.edu").one()
        assert (row.course_id, row.year, row.roll_number) == (course, 2, "27")
        assert row.must_change_password is False
        assert db.query(AuditLog).filter_by(action="first_login_email").count() == 1

    again = RemoteBackend("http://testserver", http=http)
    assert again.login("student", "ada@uni.edu", temporary)["ok"] is False  # temporary is dead
    assert again.login("student", "ada@uni.edu", "my-new-password")["ok"] is True
    assert student.call("complete_first_login", "another-one-123", None)["ok"] is False


def test_requests_are_refused_or_limited(
    server: tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]],
    db_session_factory: Factory,
) -> None:
    http, state, outbox, _ = server

    def ask(email: str) -> Any:
        return http.post("/api/request_login", json={"email": email})

    assert "approved list" in ask("stranger@uni.edu").json()["error"]
    assert "university email" in ask("nonsense").json()["error"]
    assert ask("ada@uni.edu").status_code == 200
    assert "just sent" in ask("ada@uni.edu").json()["error"]  # a minute between emails

    state.login_throttle = onboarding.Throttle()  # (pretend the minute passed)
    resent = ask("ada@uni.edu").json()["result"]
    assert resent["created"] is False  # never used, so a fresh password is sent
    first, second = (_password_in(body) for _, body in outbox)
    assert first != second
    backend = RemoteBackend("http://testserver", http=http)
    assert backend.login("student", "ada@uni.edu", first)["ok"] is False  # the old one is dead

    backend.login("student", "ada@uni.edu", second)
    backend.call("complete_first_login", "chosen-password-1", None)
    state.login_throttle = onboarding.Throttle()
    assert "already set up" in ask("ada@uni.edu").json()["error"]  # no resets for active accounts


def test_nothing_is_created_when_the_email_cannot_be_sent(
    server: tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]],
    db_session_factory: Factory,
) -> None:
    http, state, _, _ = server

    def broken(*_: Any) -> None:
        raise mailer.MailError("down")

    state.send_mail = broken  # type: ignore[assignment,misc]
    reply = http.post("/api/request_login", json={"email": "ada@uni.edu"})
    assert reply.status_code == 400 and "couldn't be sent" in reply.json()["error"]
    with db_session_factory() as db:
        assert db.query(Student).count() == 0


def test_a_teachers_email_and_unconfigured_servers_are_handled(
    server: tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]],
    db_session_factory: Factory,
) -> None:
    http, _, _, _ = server
    approvals.add_entry(db_session_factory, "student", name="T", email="t@uni.edu")
    create_professor_account(db_session_factory, display_name="T", email="t@uni.edu", password=_PW)
    assert "teacher" in http.post("/api/request_login", json={"email": "t@uni.edu"}).json()["error"]
    with db_session_factory() as db:
        from eaal_platform.db.models import EmailSettings

        db.query(EmailSettings).delete()
        db.commit()
    assert (
        "isn't set up"
        in http.post("/api/request_login", json={"email": "ada@uni.edu"}).json()["error"]
    )


# -- the admin panel ------------------------------------------------------------------------


def test_the_admin_sets_up_and_tests_the_mail_account(
    server: tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]],
) -> None:
    http, _, outbox, auth = server
    shown = http.get("/admin/api/email", headers=auth).json()
    assert shown["username"] == "cavy@gmail.com" and shown["has_password"] is True
    assert "apppassword" not in http.get("/admin/api/email", headers=auth).text

    saved = http.post(
        "/admin/api/email",
        json={"host": "smtp.gmail.com", "port": 587, "username": "new@gmail.com", "password": ""},
        headers=auth,
    )
    assert saved.status_code == 200 and saved.json()["username"] == "new@gmail.com"
    bad = http.post("/admin/api/email", json={"username": ""}, headers=auth)
    assert bad.status_code == 400

    assert (
        http.post("/admin/api/email/test", json={"to": "me@x.com"}, headers=auth).status_code == 200
    )
    assert outbox[-1][0] == "me@x.com"
    assert http.post("/admin/api/email/test", json={"to": "me@x.com"}).status_code == 401


def test_the_database_browser_hides_the_mail_password(
    server: tuple[TestClient, ServerState, list[tuple[str, str]], dict[str, str]],
) -> None:
    http, _, _, auth = server
    table = http.get("/admin/api/tables/email_settings", headers=auth)
    assert table.status_code == 200
    assert "apppassword" not in table.text


def test_local_apps_keep_the_old_sign_up(db_session_factory: Factory) -> None:
    api = CavyApi(db_session_factory, EventLogger(db_session_factory))
    assert api.get_signup_info() == {"email_signup": False}
