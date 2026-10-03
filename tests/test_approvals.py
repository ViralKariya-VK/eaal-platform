"""Approved emails: who may create an account, and how the lists are filled."""

from __future__ import annotations

import base64
import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db import approval_import, approvals
from eaal_platform.db.bootstrap import create_professor_account, create_student_account
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.app import ServerState, create_app

_PW = "hunter2-hunter2"
Factory = sessionmaker[OrmSession]


# -- the lists themselves ----------------------------------------------------------------


def test_emails_are_checked_and_stored_in_lower_case(db_session_factory: Factory) -> None:
    approvals.add_entry(db_session_factory, "student", name="Ada", email="  Ada@Uni.EDU ")
    [entry] = approvals.list_entries(db_session_factory, "student")
    assert entry["email"] == "ada@uni.edu"
    assert entry["registered"] is False
    for bad in ("", "no-at-sign", "a@b", "two@@x.com", "a b@x.com", "a@x.com, b@x.com"):
        with pytest.raises(approvals.ApprovalError, match="valid email"):
            approvals.add_entry(db_session_factory, "student", name=None, email=bad)
    with pytest.raises(approvals.ApprovalError, match="teacher or student"):
        approvals.add_entry(db_session_factory, "admin", name=None, email="x@y.com")


def test_an_email_can_only_be_listed_once_across_both_lists(db_session_factory: Factory) -> None:
    approvals.add_entry(db_session_factory, "student", name=None, email="a@uni.edu")
    with pytest.raises(approvals.ApprovalError, match="already on the list"):
        approvals.add_entry(db_session_factory, "student", name=None, email="A@uni.edu")
    with pytest.raises(approvals.ApprovalError, match="student list"):
        approvals.add_entry(db_session_factory, "professor", name=None, email="a@uni.edu")


def test_an_existing_account_of_the_other_kind_blocks_the_listing(
    db_session_factory: Factory,
) -> None:
    create_student_account(db_session_factory, display_name="S", email="s@uni.edu", password=_PW)
    with pytest.raises(approvals.ApprovalError, match="already has a student account"):
        approvals.add_entry(db_session_factory, "professor", name=None, email="s@uni.edu")
    # Listing someone who already has the right kind of account is fine: they show as registered.
    approvals.add_entry(db_session_factory, "student", name=None, email="s@uni.edu")
    assert approvals.list_entries(db_session_factory, "student")[0]["registered"] is True


def test_waiting_entries_can_be_edited_but_registered_ones_cannot(
    db_session_factory: Factory,
) -> None:
    waiting = approvals.add_entry(db_session_factory, "student", name="A", email="a@uni.edu")
    done = approvals.add_entry(db_session_factory, "student", name="B", email="b@uni.edu")
    create_student_account(db_session_factory, display_name="B", email="b@uni.edu", password=_PW)

    approvals.update_entry(
        db_session_factory, waiting, name="Alice", email="alice@uni.edu", enrollment_no="E1"
    )
    row = next(
        e for e in approvals.list_entries(db_session_factory, "student") if e["id"] == waiting
    )
    assert (row["name"], row["email"], row["enrollment_no"]) == ("Alice", "alice@uni.edu", "E1")

    with pytest.raises(approvals.ApprovalError, match="Users page"):
        approvals.update_entry(db_session_factory, done, name="B", email="bb@uni.edu")
    with pytest.raises(approvals.ApprovalError, match="already on the list"):
        approvals.update_entry(db_session_factory, waiting, name="A", email="b@uni.edu")
    with pytest.raises(approvals.ApprovalError, match="doesn't exist"):
        approvals.update_entry(db_session_factory, 999, name="A", email="z@uni.edu")


def test_removing_an_approval_leaves_the_account_alone(db_session_factory: Factory) -> None:
    entry = approvals.add_entry(db_session_factory, "student", name=None, email="a@uni.edu")
    create_student_account(db_session_factory, display_name="A", email="a@uni.edu", password=_PW)
    assert approvals.delete_entry(db_session_factory, entry) == "a@uni.edu"
    assert approvals.list_entries(db_session_factory, "student") == []
    api = CavyApi(db_session_factory, EventLogger(db_session_factory))
    assert api.login("student", "a@uni.edu", _PW)["ok"] is True  # can still sign in
    with pytest.raises(approvals.ApprovalError):
        approvals.delete_entry(db_session_factory, entry)


# -- sign-up is restricted on a server ---------------------------------------------------


def _signup_api(factory: Factory, *, restricted: bool = True) -> CavyApi:
    return CavyApi(factory, EventLogger(factory), restrict_signup=restricted)


def test_only_approved_emails_can_create_an_account(db_session_factory: Factory) -> None:
    api = _signup_api(db_session_factory)
    refused = api.create_account("student", "Mallory", "mallory@elsewhere.com", _PW)
    assert refused["ok"] is False
    assert "hasn't been approved" in refused["error"]

    approvals.add_entry(db_session_factory, "student", name="Ada", email="ada@uni.edu")
    assert api.create_account("student", "Ada", "ada@uni.edu", _PW)["ok"] is True
    assert api.login("student", "ada@uni.edu", _PW)["ok"] is True


def test_nobody_can_create_a_teacher_account_unless_approved(db_session_factory: Factory) -> None:
    api = _signup_api(db_session_factory)
    refused = api.create_account("professor", "Evil", "evil@uni.edu", _PW)
    assert refused["ok"] is False
    assert "set up by your administrator" in refused["error"]

    approvals.add_entry(db_session_factory, "professor", name="Dr Real", email="real@uni.edu")
    assert api.create_account("professor", "Dr Real", "real@uni.edu", _PW)["ok"] is True
    assert api.login("professor", "real@uni.edu", _PW)["ok"] is True


def test_an_approval_only_works_for_its_own_role(db_session_factory: Factory) -> None:
    approvals.add_entry(db_session_factory, "student", name=None, email="stu@uni.edu")
    approvals.add_entry(db_session_factory, "professor", name=None, email="prof@uni.edu")
    api = _signup_api(db_session_factory)
    # A student can't use their approval to become a teacher, and vice versa.
    assert api.create_account("professor", "S", "stu@uni.edu", _PW)["ok"] is False
    assert api.create_account("student", "P", "prof@uni.edu", _PW)["ok"] is False


def test_approval_is_not_case_sensitive_and_cannot_be_used_twice(
    db_session_factory: Factory,
) -> None:
    approvals.add_entry(db_session_factory, "student", name=None, email="ada@uni.edu")
    api = _signup_api(db_session_factory)
    assert api.create_account("student", "Ada", "ADA@Uni.edu", _PW)["ok"] is True
    again = api.create_account("student", "Ada 2", "ada@uni.edu", _PW)
    assert again["ok"] is False
    assert "already registered" in again["error"]
    assert api.login("student", "Ada@UNI.edu", _PW)["ok"] is True  # sign-in ignores case too


def test_the_enrolment_number_and_name_come_from_the_approval_when_left_blank(
    db_session_factory: Factory,
) -> None:
    approvals.add_entry(
        db_session_factory, "student", name="Ada Patel", email="ada@uni.edu", enrollment_no="E-77"
    )
    api = _signup_api(db_session_factory)
    assert api.create_account("student", "", "ada@uni.edu", _PW)["ok"] is True
    api.login("student", "ada@uni.edu", _PW)
    profile = api.get_profile()
    assert profile["name"] == "Ada Patel"
    assert profile["enrollment_no"] == "E-77"


def test_a_standalone_app_stays_open(db_session_factory: Factory) -> None:
    """With no server and no administrator, anyone can create a local account."""
    api = _signup_api(db_session_factory, restricted=False)
    assert api.create_account("student", "Free", "free@anywhere.com", _PW)["ok"] is True


# -- reading uploaded files --------------------------------------------------------------


def _csv(text: str, name: str = "list.csv") -> list[dict[str, object]]:
    return approval_import.read_rows(name, text.encode("utf-8"), "student")


def test_csv_columns_are_matched_loosely_and_in_any_order() -> None:
    rows = _csv("Roll No,Full Name,E-mail,Extra\nE1,Ada Patel,ada@uni.edu,x\nE2,Bo,bo@uni.edu,y\n")
    assert [(r["row"], r["name"], r["email"], r["enrollment_no"]) for r in rows] == [
        (2, "Ada Patel", "ada@uni.edu", "E1"),
        (3, "Bo", "bo@uni.edu", "E2"),
    ]


def test_csv_with_bom_semicolons_and_blank_lines() -> None:
    data = "﻿name;email\n\nAda;ada@uni.edu\n;;\nBo;bo@uni.edu\n".encode()
    rows = approval_import.read_rows("x.csv", data, "professor")
    assert [r["email"] for r in rows] == ["ada@uni.edu", "bo@uni.edu"]
    assert all(r["enrollment_no"] == "" for r in rows)  # teachers have no enrolment number


def test_a_file_without_a_header_row_works_if_it_has_emails() -> None:
    rows = _csv("Ada Patel,ada@uni.edu,E1\nBo Lee,bo@uni.edu,E2\n")
    assert [(r["name"], r["email"], r["enrollment_no"]) for r in rows] == [
        ("Ada Patel", "ada@uni.edu", "E1"),
        ("Bo Lee", "bo@uni.edu", "E2"),
    ]
    assert rows[0]["row"] == 1


def test_excel_files_are_read_and_numbers_stay_clean() -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Name", "Email", "Enrollment No"])
    sheet.append(["Ada", "ada@uni.edu", 101])  # Excel numbers arrive as numbers
    sheet.append(["Bo", "bo@uni.edu", 102.0])
    buffer = io.BytesIO()
    book.save(buffer)
    rows = approval_import.read_rows("class.xlsx", buffer.getvalue(), "student")
    assert [(r["email"], r["enrollment_no"]) for r in rows] == [
        ("ada@uni.edu", "101"),
        ("bo@uni.edu", "102"),
    ]


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("a.csv", b"", "empty"),
        ("a.csv", b"name,phone\nAda,123\n", "Email column"),
        ("a.csv", b"\n\n,,\n", "no rows"),
        ("a.xls", b"data", "Old .xls"),
        ("a.pdf", b"data", ".csv or .xlsx"),
        ("a.xlsx", b"not really excel", "couldn't be read"),
        ("a.csv", b"x" * (approval_import.MAX_UPLOAD_BYTES + 1), "too large"),
    ],
)
def test_unusable_files_are_explained(name: str, data: bytes, message: str) -> None:
    with pytest.raises(approval_import.UploadError, match=message):
        approval_import.read_rows(name, data, "student")


def test_the_templates_import_cleanly(db_session_factory: Factory) -> None:
    for role in ("student", "professor"):
        rows = approval_import.read_rows(
            f"{role}.csv", approval_import.TEMPLATES[role].encode(), role
        )
        report = approvals.import_entries(db_session_factory, role, rows)
        assert report.added == 2
        assert report.problems == []


# -- importing ---------------------------------------------------------------------------


def test_import_adds_good_rows_and_explains_the_rest(db_session_factory: Factory) -> None:
    approvals.add_entry(db_session_factory, "student", name=None, email="have@uni.edu")
    approvals.add_entry(db_session_factory, "professor", name=None, email="prof@uni.edu")
    rows = _csv(
        "name,email\n"
        "New One,new1@uni.edu\n"
        "Bad,not-an-email\n"
        "Have,HAVE@uni.edu\n"
        "Dup,new1@uni.edu\n"
        "Teacher,prof@uni.edu\n"
        "New Two,new2@uni.edu\n"
    )
    report = approvals.import_entries(db_session_factory, "student", rows)

    assert report.added == 2
    assert report.already_listed == 2  # "have" (already there) and the repeated new1
    problems = {(p["row"], p["email"]): p["error"] for p in report.problems}
    assert "valid email" in problems[(3, "not-an-email")]
    assert "teacher list" in problems[(6, "prof@uni.edu")]
    emails = {e["email"] for e in approvals.list_entries(db_session_factory, "student")}
    assert emails == {"have@uni.edu", "new1@uni.edu", "new2@uni.edu"}


# -- the admin panel API -----------------------------------------------------------------


@pytest.fixture
def server(
    db_engine: Engine, db_session_factory: Factory
) -> Iterator[tuple[TestClient, ServerState, dict[str, str]]]:
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state)) as http:
        token = http.post(
            "/admin/api/setup", json={"name": "Root", "email": "root@x.com", "password": _PW}
        ).json()["token"]
        yield http, state, {"Authorization": f"Bearer {token}"}


def test_the_whole_sign_up_flow_through_the_server(
    server: tuple[TestClient, ServerState, dict[str, str]],
) -> None:
    http, _, admin = server
    signup = {"args": ["professor", "Dr K", "k@uni.edu", _PW]}
    assert http.post("/api/create_account", json=signup).json()["result"]["ok"] is False

    added = http.post(
        "/admin/api/allowed",
        json={"role": "professor", "name": "Dr K", "email": "K@uni.edu"},
        headers=admin,
    )
    assert added.status_code == 200
    listing = http.get("/admin/api/allowed/professor", headers=admin).json()
    assert listing["counts"] == {"total": 1, "registered": 0, "waiting": 1}

    assert http.post("/api/create_account", json=signup).json()["result"]["ok"] is True
    listing = http.get("/admin/api/allowed/professor", headers=admin).json()
    assert listing["counts"] == {"total": 1, "registered": 1, "waiting": 0}
    assert listing["entries"][0]["registered"] is True


def test_admin_adds_edits_and_removes_approvals(
    server: tuple[TestClient, ServerState, dict[str, str]],
) -> None:
    http, _, admin = server
    new = http.post(
        "/admin/api/allowed",
        json={"role": "student", "name": "Ada", "email": "ada@uni.edu", "enrollment_no": "E1"},
        headers=admin,
    ).json()["id"]
    dup = http.post(
        "/admin/api/allowed", json={"role": "student", "email": "ada@uni.edu"}, headers=admin
    )
    assert dup.status_code == 400
    assert "already on the list" in dup.json()["detail"]
    assert (
        http.post(
            "/admin/api/allowed", json={"role": "student", "email": "nope"}, headers=admin
        ).status_code
        == 400
    )

    edited = http.post(
        f"/admin/api/allowed/{new}/update",
        json={"name": "Ada P", "email": "ada.p@uni.edu", "enrollment_no": "E2"},
        headers=admin,
    )
    assert edited.status_code == 200
    row = http.get("/admin/api/allowed/student", headers=admin).json()["entries"][0]
    assert (row["name"], row["email"], row["enrollment_no"]) == ("Ada P", "ada.p@uni.edu", "E2")

    assert http.post(f"/admin/api/allowed/{new}/delete", headers=admin).status_code == 200
    assert http.get("/admin/api/allowed/student", headers=admin).json()["entries"] == []
    assert http.post(f"/admin/api/allowed/{new}/delete", headers=admin).status_code == 404
    assert http.get("/admin/api/allowed/wizard", headers=admin).status_code == 400


def test_admin_imports_a_file_and_gets_a_report(
    server: tuple[TestClient, ServerState, dict[str, str]],
) -> None:
    http, _, admin = server
    content = "name,email,enrollment_no\nAda,ada@uni.edu,E1\nBad,oops\nBo,bo@uni.edu,E2\n"
    reply = http.post(
        "/admin/api/allowed-import",
        json={
            "role": "student",
            "filename": "class.csv",
            "data_base64": base64.b64encode(content.encode()).decode(),
        },
        headers=admin,
    )
    body = reply.json()
    assert reply.status_code == 200
    assert (body["added"], body["rows"], len(body["problems"])) == (2, 3, 1)
    assert body["problems"][0]["row"] == 3

    for payload, expected in (
        ({"role": "student", "filename": "a.csv", "data_base64": "***"}, "couldn't be read"),
        ({"role": "student", "filename": "a.pdf", "data_base64": "AAAA"}, ".csv or .xlsx"),
        ({"role": "boss", "filename": "a.csv", "data_base64": "AAAA"}, "teacher or student"),
    ):
        bad = http.post("/admin/api/allowed-import", json=payload, headers=admin)
        assert bad.status_code == 400
        assert expected in bad.json()["detail"]


def test_templates_download_and_everything_needs_an_admin(
    server: tuple[TestClient, ServerState, dict[str, str]],
) -> None:
    http, _, admin = server
    template = http.get("/admin/api/allowed-template/student.csv", headers=admin)
    assert template.status_code == 200
    assert "enrollment_no" in template.text
    assert (
        "teachers-template.csv"
        in http.get("/admin/api/allowed-template/professor.csv", headers=admin).headers[
            "content-disposition"
        ]
    )
    assert http.get("/admin/api/allowed-template/boss.csv", headers=admin).status_code == 404
    for call in (
        lambda: http.get("/admin/api/allowed/student"),
        lambda: http.post("/admin/api/allowed", json={"role": "student", "email": "a@b.co"}),
        lambda: http.post("/admin/api/allowed-import", json={}),
        lambda: http.get("/admin/api/allowed-template/student.csv"),
    ):
        assert call().status_code in (401, 422)


def test_editing_an_accounts_email_moves_its_approval(
    server: tuple[TestClient, ServerState, dict[str, str]],
) -> None:
    http, state, admin = server
    approvals.add_entry(state.session_factory, "student", name="A", email="old@uni.edu")
    student_id = create_student_account(
        state.session_factory, display_name="A", email="old@uni.edu", password=_PW
    )
    reply = http.post(
        "/admin/api/users/update",
        json={"role": "student", "id": student_id, "email": "New@Uni.edu"},
        headers=admin,
    )
    assert reply.status_code == 200
    entries = http.get("/admin/api/allowed/student", headers=admin).json()["entries"]
    assert [(e["email"], e["registered"]) for e in entries] == [("new@uni.edu", True)]
    # And nobody can claim the old address any more.
    assert (
        http.post(
            "/api/create_account", json={"args": ["student", "X", "old@uni.edu", _PW]}
        ).json()["result"]["ok"]
        is False
    )


def test_admin_made_and_edited_accounts_use_clean_emails(
    server: tuple[TestClient, ServerState, dict[str, str]],
) -> None:
    http, state, admin = server
    create_professor_account(
        state.session_factory, display_name="P", email="taken@uni.edu", password=_PW
    )
    made = http.post(
        "/admin/api/users/create",
        json={"role": "student", "name": "S", "email": " Mixed@Uni.EDU ", "password": _PW},
        headers=admin,
    )
    assert made.status_code == 200
    users = http.get("/admin/api/users", headers=admin).json()
    assert [s["email"] for s in users["students"]] == ["mixed@uni.edu"]
    clash = http.post(
        "/admin/api/users/update",
        json={"role": "student", "id": users["students"][0]["id"], "email": "TAKEN@uni.edu"},
        headers=admin,
    )
    assert clash.status_code == 400  # case-insensitive uniqueness
