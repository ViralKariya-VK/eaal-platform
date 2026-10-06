"""Classes and shared resources: who can see and change what."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db import resources as store
from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    seed_demo_content,
)
from eaal_platform.db.models import Resource, ResourceFile, ResourceLab, ResourceStudent
from eaal_platform.events.logger import EventLogger

_PW = "hunter2-hunter2"


class World:
    """Two professors, three students, and a separate API (a "computer") for each person."""

    def __init__(self, factory: sessionmaker[OrmSession]) -> None:
        self.factory = factory
        seed_demo_content(factory)
        create_professor_account(factory, display_name="Prof One", email="p1@x.com", password=_PW)
        create_professor_account(factory, display_name="Prof Two", email="p2@x.com", password=_PW)
        for name in ("Stu1", "Stu2", "Stu3"):
            create_student_account(
                factory, display_name=name, email=f"{name.lower()}@x.com", password=_PW
            )
        self.p1 = self._api("professor", "p1@x.com")
        self.p2 = self._api("professor", "p2@x.com")
        self.s1 = self._api("student", "stu1@x.com")
        self.s2 = self._api("student", "stu2@x.com")
        self.s3 = self._api("student", "stu3@x.com")
        self.ids = {
            n: next(
                s["id"] for s in self.p1.get_unassigned_students() if s["email"] == f"{n}@x.com"
            )
            for n in ("stu1", "stu2", "stu3")
        }

    def _api(self, role: str, email: str) -> CavyApi:
        api = CavyApi(self.factory, EventLogger(self.factory))
        assert api.login(role, email, _PW)["ok"] is True
        return api

    def lab_id(self) -> int:
        return int(self.p1.get_professor_labs()[0]["id"])


@pytest.fixture
def world(db_session_factory: sessionmaker[OrmSession]) -> World:
    return World(db_session_factory)


def _note(title: str = "Read this", **extra: Any) -> dict[str, Any]:
    return {"kind": "NOTE", "title": title, "body": "Chapter 3 first.", **extra}


def _file(name: str = "notes.pdf", data: bytes = b"%PDF-1.4 hi", **extra: Any) -> dict[str, Any]:
    return {
        "kind": "FILE",
        "title": "Notes",
        "filename": name,
        "mime_type": "application/pdf",
        "data_base64": base64.b64encode(data).decode(),
        **extra,
    }


# -- classes ------------------------------------------------------------------------------------


def test_professor_adds_students_and_sees_only_their_own_class(world: World) -> None:
    assert [s["name"] for s in world.p1.get_unassigned_students()] == ["Stu1", "Stu2", "Stu3"]
    assert world.p1.add_students_to_class([world.ids["stu1"], world.ids["stu2"]]) == {
        "ok": True,
        "added": 2,
    }
    assert [s["name"] for s in world.p1.get_students()] == ["Stu1", "Stu2"]
    assert world.p2.get_students() == []
    # Dr Two can still add anyone: being in Dr One's class doesn't keep a student out of theirs.
    assert [s["name"] for s in world.p2.get_unassigned_students()] == ["Stu1", "Stu2", "Stu3"]
    assert [s["name"] for s in world.p1.get_unassigned_students()] == ["Stu3"]
    assert world.s1.get_my_teacher() == "Prof One"
    assert world.s3.get_my_teacher() is None


def test_a_student_can_be_in_several_professors_classes(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    assert world.p2.add_students_to_class([world.ids["stu1"]]) == {"ok": True, "added": 1}
    assert world.p2.add_students_to_class([world.ids["stu1"]]) == {"ok": True, "added": 0}
    assert [s["name"] for s in world.p2.get_students()] == ["Stu1"]
    assert world.s1.get_my_teacher() == "Prof One, Prof Two"

    # Each professor's material reaches the student; leaving one class ends only that one.
    world.p1.create_resource(_note("From one"))
    world.p2.create_resource(_note("From two"))
    assert {r["title"] for r in world.s1.get_student_resources()} == {"From one", "From two"}
    world.p1.remove_student_from_class(world.ids["stu1"])
    assert {r["title"] for r in world.s1.get_student_resources()} == {"From two"}
    assert world.s1.get_my_teacher() == "Prof Two"


def test_a_professor_cannot_remove_or_reset_someone_elses_student(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    assert world.p2.remove_student_from_class(world.ids["stu1"])["ok"] is False
    assert world.p2.reset_student_password(world.ids["stu1"])["ok"] is False
    assert world.p1.reset_student_password(world.ids["stu1"])["ok"] is True
    assert world.p1.remove_student_from_class(world.ids["stu1"]) == {"ok": True}
    assert world.p1.get_students() == []
    assert world.p2.add_students_to_class([world.ids["stu1"]])["ok"] is True  # free again


def test_class_management_is_for_professors(world: World) -> None:
    for call in (
        lambda: world.s1.get_students(),
        lambda: world.s1.get_unassigned_students(),
        lambda: world.s1.add_students_to_class([world.ids["stu1"]]),
        lambda: world.s1.get_my_resources(),
        lambda: world.s1.create_resource(_note()),
    ):
        with pytest.raises(ValueError, match="professor"):
            call()
    with pytest.raises(ValueError, match="student"):
        world.p1.get_student_resources()


# -- who sees a resource ----------------------------------------------------------------------


def test_students_see_only_their_own_professors_resources(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    world.p2.add_students_to_class([world.ids["stu2"]])
    world.p1.create_resource(_note("From one"))
    world.p2.create_resource(_note("From two"))

    assert [r["title"] for r in world.s1.get_student_resources()] == ["From one"]
    assert [r["title"] for r in world.s2.get_student_resources()] == ["From two"]
    assert world.s3.get_student_resources() == []  # nobody's student: sees nothing
    assert [r["title"] for r in world.p1.get_my_resources()] == ["From one"]
    assert [r["title"] for r in world.p2.get_my_resources()] == ["From two"]


def test_sharing_with_selected_students_only(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"], world.ids["stu2"]])
    created = world.p1.create_resource(
        _note("Just for Stu1", audience_all=False, student_ids=[world.ids["stu1"]])
    )
    assert created["ok"] is True
    assert [r["title"] for r in world.s1.get_student_resources()] == ["Just for Stu1"]
    assert world.s2.get_student_resources() == []
    mine = world.p1.get_my_resources()[0]
    assert mine["audience_all"] is False
    assert mine["student_ids"] == [world.ids["stu1"]]


def test_cannot_share_with_students_outside_your_class(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    world.p2.add_students_to_class([world.ids["stu2"]])
    result = world.p1.create_resource(
        _note(audience_all=False, student_ids=[world.ids["stu1"], world.ids["stu2"]])
    )
    assert result["ok"] is False
    assert "your own class" in result["error"]
    assert world.p1.get_my_resources() == []  # nothing half-created
    nobody = world.p1.create_resource(_note(audience_all=False, student_ids=[]))
    assert nobody["ok"] is False


def test_leaving_the_class_ends_access_and_individual_shares(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    world.p1.create_resource(_note("All"))
    world.p1.create_resource(_note("Mine", audience_all=False, student_ids=[world.ids["stu1"]]))
    assert len(world.s1.get_student_resources()) == 2

    world.p1.remove_student_from_class(world.ids["stu1"])

    assert world.s1.get_student_resources() == []
    with world.factory() as db:
        assert db.query(ResourceStudent).count() == 0
    # Joining another class doesn't resurrect the old shares or reveal the old professor's files.
    world.p2.add_students_to_class([world.ids["stu1"]])
    assert world.s1.get_student_resources() == []


def test_a_professor_cannot_edit_or_delete_anothers_resource(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    rid = world.p1.create_resource(_note("Mine"))["id"]
    assert world.p2.update_resource(rid, _note("Hijacked"))["ok"] is False
    assert world.p2.delete_resource(rid)["ok"] is False
    assert [r["title"] for r in world.p1.get_my_resources()] == ["Mine"]
    assert world.p1.update_resource(rid, _note("Renamed", body="New text"))["ok"] is True
    assert world.s1.get_student_resources()[0]["title"] == "Renamed"
    assert world.p1.delete_resource(rid)["ok"] is True
    assert world.s1.get_student_resources() == []


# -- kinds, validation ----------------------------------------------------------------------------


def test_links_are_checked(world: World) -> None:
    ok = world.p1.create_resource({"kind": "LINK", "title": "Docs", "url": "docs.python.org"})
    assert ok["ok"] is True
    assert world.p1.get_my_resources()[0]["url"] == "https://docs.python.org"
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x.com/a", "", "http://"):
        result = world.p1.create_resource({"kind": "LINK", "title": "x", "url": bad})
        assert result["ok"] is False, bad


def test_notes_need_text_and_titles_are_required(world: World) -> None:
    assert world.p1.create_resource({"kind": "NOTE", "title": "t", "body": "  "})["ok"] is False
    assert world.p1.create_resource({"kind": "NOTE", "title": "  ", "body": "x"})["ok"] is False
    assert world.p1.create_resource({"kind": "WIZARD", "title": "t"})["ok"] is False
    assert (
        world.p1.create_resource({"kind": "NOTE", "title": "t", "body": "x" * 30000})["ok"] is False
    )


def test_files_round_trip_and_are_scoped(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    rid = world.p1.create_resource(_file("lecture.pdf", b"PDFDATA"))["id"]

    mine = world.p1.get_resource_file(rid)
    assert mine["ok"] and base64.b64decode(mine["data_base64"]) == b"PDFDATA"
    assert mine["filename"] == "lecture.pdf"
    theirs = world.s1.get_resource_file(rid)
    assert base64.b64decode(theirs["data_base64"]) == b"PDFDATA"

    assert world.s2.get_resource_file(rid)["ok"] is False  # not in the class
    assert world.s3.get_resource_file(rid)["ok"] is False
    assert world.p2.get_resource_file(rid)["ok"] is False  # another professor
    assert world.p1.get_resource_file(99999)["ok"] is False
    listing = world.s1.get_student_resources()[0]
    assert "data_base64" not in listing
    assert listing["size_bytes"] == 7


def test_selected_audience_applies_to_file_downloads_too(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"], world.ids["stu2"]])
    rid = world.p1.create_resource(_file(audience_all=False, student_ids=[world.ids["stu1"]]))["id"]
    assert world.s1.get_resource_file(rid)["ok"] is True
    assert world.s2.get_resource_file(rid)["ok"] is False


def test_dangerous_and_oversized_files_are_refused(world: World) -> None:
    for name in ("setup.exe", "run.bat", "x.SH", "evil.js", "app.jar", "a.ps1", "tool.msi"):
        refused = world.p1.create_resource(_file(name))
        assert refused["ok"] is False, name
        assert "can't be shared" in refused["error"]
    assert world.p1.create_resource(_file("empty.pdf", b""))["ok"] is False
    big = world.p1.create_resource(_file("big.pdf", b"x" * (store.MAX_FILE_BYTES + 1)))
    assert big["ok"] is False
    assert "too large" in big["error"]
    assert world.p1.create_resource({**_file(), "data_base64": "***not base64***"})["ok"] is False
    assert world.p1.create_resource({"kind": "FILE", "title": "no file"})["ok"] is False
    assert world.p1.get_my_resources() == []


def test_file_names_are_cleaned() -> None:
    assert store.clean_filename("../../etc/passwd") == "passwd"
    assert store.clean_filename("C:\\Users\\x\\report.pdf") == "report.pdf"
    assert store.clean_filename('a<b>:"c".pdf') == "abc.pdf"
    assert store.clean_filename("...") == "file"


def test_replacing_a_file_keeps_the_resource(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    rid = world.p1.create_resource(_file("v1.pdf", b"ONE"))["id"]
    same = {k: v for k, v in _file().items() if k != "data_base64"}
    assert world.p1.update_resource(rid, {**same, "title": "Retitled"})["ok"] is True  # no new file
    assert base64.b64decode(world.s1.get_resource_file(rid)["data_base64"]) == b"ONE"
    new = _file("v2.pdf", b"TWO")
    assert world.p1.update_resource(rid, new)["ok"] is True
    assert base64.b64decode(world.s1.get_resource_file(rid)["data_base64"]) == b"TWO"
    assert world.s1.get_resource_file(rid)["filename"] == "v2.pdf"
    with world.factory() as db:
        assert db.query(ResourceFile).count() == 1


def test_a_resource_cannot_change_type(world: World) -> None:
    rid = world.p1.create_resource(_note())["id"]
    result = world.p1.update_resource(rid, {"kind": "LINK", "title": "x", "url": "https://x.com"})
    assert result["ok"] is False


# -- labs ----------------------------------------------------------------------------------------


def test_attaching_to_labs_and_seeing_them_on_the_lab(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    lab = world.lab_id()
    world.p1.create_resource(_note("On the lab", task_ids=[lab]))
    world.p1.create_resource(_note("General"))

    on_lab = [r["title"] for r in world.s1.get_lab_resources(lab)]
    assert on_lab == ["On the lab"]
    assert world.s2.get_lab_resources(lab) == []  # not in the class
    assert [r["title"] for r in world.p1.get_lab_resources(lab)] == ["On the lab"]
    assert world.p2.get_lab_resources(lab) == []
    assert world.s1.get_student_resources()[-1]["labs"] == [
        {"id": lab, "title": world.p1.get_professor_labs()[0]["title"]}
    ] or any(r["labs"] for r in world.s1.get_student_resources())


def test_only_real_labs_can_be_attached(world: World) -> None:
    assert world.p1.create_resource(_note(task_ids=[99999]))["ok"] is False
    with world.factory() as db:
        practice_id = db.query(store.Task).filter_by(title="Practice").one().id
    assert world.p1.create_resource(_note(task_ids=[practice_id]))["ok"] is False


def test_deleting_removes_everything_attached(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    rid = world.p1.create_resource(
        _file(task_ids=[world.lab_id()], audience_all=False, student_ids=[world.ids["stu1"]])
    )["id"]
    world.p1.delete_resource(rid)
    with world.factory() as db:
        assert db.query(Resource).count() == 0
        assert db.query(ResourceFile).count() == 0
        assert db.query(ResourceLab).count() == 0
        assert db.query(ResourceStudent).count() == 0


def test_live_resource_lists_include_owner_and_dates(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    world.p1.create_resource(_note("Hello"))
    row = world.s1.get_student_resources()[0]
    assert row["owner"] == "Prof One"
    assert row["created_at"]
    assert row["kind"] == "NOTE"


def test_admin_chooses_which_classes_a_student_is_in(world: World) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    world.p1.create_resource(_note("From one"))
    with world.factory() as db:
        p1_id = db.query(store.Professor).filter_by(email="p1@x.com").one().id
        p2_id = db.query(store.Professor).filter_by(email="p2@x.com").one().id
    store.set_student_professors(world.factory, world.ids["stu1"], [p2_id])
    assert world.s1.get_my_teacher() == "Prof Two"
    assert world.s1.get_student_resources() == []  # Prof One's material is no longer theirs
    store.set_student_professors(world.factory, world.ids["stu1"], [p1_id, p2_id])
    assert world.s1.get_my_teacher() == "Prof One, Prof Two"
    store.set_student_professors(world.factory, world.ids["stu1"], [])
    assert world.s1.get_my_teacher() is None
    with pytest.raises(store.ResourceError):
        store.set_student_professors(world.factory, world.ids["stu1"], [99999])
    with pytest.raises(store.ResourceError):
        store.set_student_professors(world.factory, 99999, [])


# -- opening things on the user's own computer ---------------------------------------------------


def _client(world: World, email: str, role: str, dialog: Any = None) -> Any:
    from eaal_platform.client.api import ClientApi

    api = ClientApi(CavyApi(world.factory, EventLogger(world.factory)), dialog)
    assert api.login(role, email, _PW)["ok"] is True
    return api


def test_saving_a_shared_file_writes_it_where_the_student_chooses(
    world: World, tmp_path: Any
) -> None:
    world.p1.add_students_to_class([world.ids["stu1"]])
    rid = world.p1.create_resource(_file("lecture.pdf", b"PDFDATA"))["id"]
    target = tmp_path / "saved.pdf"
    asked: list[str] = []

    def dialog(name: str) -> str:
        asked.append(name)
        return str(target)

    student = _client(world, "stu1@x.com", "student", dialog)
    assert student.save_resource(rid)["ok"] is True
    assert asked == ["lecture.pdf"]
    assert target.read_bytes() == b"PDFDATA"

    outsider = _client(world, "stu2@x.com", "student", dialog)
    assert outsider.save_resource(rid)["ok"] is False
    cancelled = _client(world, "stu1@x.com", "student", lambda _name: None)
    assert cancelled.save_resource(rid) == {"ok": False, "cancelled": True}
    assert _client(world, "stu1@x.com", "student").save_resource(rid)["ok"] is False  # no dialog


def test_opening_a_shared_file_uses_the_system_opener(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eaal_platform.client import api as client_api

    world.p1.add_students_to_class([world.ids["stu1"]])
    rid = world.p1.create_resource(_file("../../sneaky/name.pdf", b"BYTES"))["id"]
    opened: list[Any] = []
    monkeypatch.setattr(client_api, "_open_with_system", lambda path: opened.append(path))

    assert _client(world, "stu1@x.com", "student").open_resource(rid) == {"ok": True}

    assert len(opened) == 1
    assert opened[0].name == "name.pdf"  # the folder part was stripped
    assert opened[0].read_bytes() == b"BYTES"
    assert _client(world, "stu3@x.com", "student").open_resource(rid)["ok"] is False
    assert len(opened) == 1  # nothing was written or opened for the refused attempt


def test_only_web_links_are_opened(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    student = _client(world, "stu1@x.com", "student")
    assert student.open_link("https://docs.python.org")["ok"] is True
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x", "mailto:a@b.c"):
        assert student.open_link(bad)["ok"] is False
    assert opened == ["https://docs.python.org"]
