"""Courses, years, divisions and batches; classes taken by group; labs aimed at a group."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db import academics
from eaal_platform.db.academics import AcademicError
from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    seed_demo_content,
)
from eaal_platform.db.models import Student
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.app import ServerState, create_app

Factory = sessionmaker[OrmSession]
_PW = "hunter2-hunter2"


def _place(course_id: int, year: int = 1, division: str = "A", batch: str = "A1", roll: str = "7"):
    return {
        "course_id": course_id,
        "year": year,
        "division": division,
        "batch": batch,
        "roll_number": roll,
    }


def _course(factory: Factory, name: str = "Engineering", years: int = 4) -> int:
    course_id = academics.add_course(factory, name, years)
    for division in ("A", "B"):
        academics.add_option(factory, course_id, "division", division)
    for batch in ("A1", "A2", "B1"):
        academics.add_option(factory, course_id, "batch", batch)
    return course_id


# -- the lists -----------------------------------------------------------------------------


def test_courses_have_a_set_number_of_years(db_session_factory: Factory) -> None:
    ids = {
        name: academics.add_course(db_session_factory, name, years)
        for name, years in academics.SUGGESTED_COURSES
    }
    listed = {c["name"]: c for c in academics.list_courses(db_session_factory)}
    assert [y["label"] for y in listed["Masters"]["year_options"]] == ["First Year", "Second Year"]
    assert len(listed["Degree"]["year_options"]) == 3
    assert len(listed["Engineering"]["year_options"]) == 4
    assert listed["Engineering"]["id"] == ids["Engineering"]


def test_course_rules(db_session_factory: Factory) -> None:
    course_id = _course(db_session_factory)
    with pytest.raises(AcademicError, match="already a course"):
        academics.add_course(db_session_factory, "engineering", 4)
    with pytest.raises(AcademicError, match="1 to 6"):
        academics.add_course(db_session_factory, "Odd", 9)
    with pytest.raises(AcademicError, match="Enter a course name"):
        academics.add_course(db_session_factory, "  ", 3)
    with pytest.raises(AcademicError, match="already in the list"):
        academics.add_option(db_session_factory, course_id, "batch", "a1")
    academics.update_course(db_session_factory, course_id, "B.Tech", 4)
    assert academics.list_courses(db_session_factory)[0]["name"] == "B.Tech"


def test_a_course_in_use_cannot_shrink_or_go(db_session_factory: Factory) -> None:
    course_id = _course(db_session_factory)
    create_student_account(
        db_session_factory,
        display_name="A",
        email="a@x.com",
        password=_PW,
        cohort=academics.signup_cohort(db_session_factory, _place(course_id, year=4)),
    )
    with pytest.raises(AcademicError, match="beyond 3"):
        academics.update_course(db_session_factory, course_id, "Engineering", 3)
    with pytest.raises(AcademicError, match="still used"):
        academics.delete_course(db_session_factory, course_id)
    division = next(
        d["id"]
        for d in academics.list_courses(db_session_factory)[0]["divisions"]
        if d["name"] == "A"
    )
    with pytest.raises(AcademicError, match="used by"):
        academics.remove_option(db_session_factory, division)


def test_an_unused_option_and_course_can_be_removed(db_session_factory: Factory) -> None:
    course_id = _course(db_session_factory)
    option = academics.list_courses(db_session_factory)[0]["batches"][0]["id"]
    academics.remove_option(db_session_factory, option)
    assert len(academics.list_courses(db_session_factory)[0]["batches"]) == 2
    academics.delete_course(db_session_factory, course_id)
    assert academics.list_courses(db_session_factory) == []


# -- signing up ----------------------------------------------------------------------------


def test_nothing_is_asked_until_courses_exist(db_session_factory: Factory) -> None:
    assert academics.signup_cohort(db_session_factory, None) is None


def test_a_student_must_fill_in_everything_their_course_offers(db_session_factory: Factory) -> None:
    course_id = _course(db_session_factory, "Masters", 2)
    check = academics.signup_cohort
    with pytest.raises(AcademicError, match="Choose a course"):
        check(db_session_factory, None)
    with pytest.raises(AcademicError, match="Choose your year"):
        check(db_session_factory, {**_place(course_id), "year": None})
    with pytest.raises(AcademicError, match="2 years"):
        check(db_session_factory, _place(course_id, year=3))
    with pytest.raises(AcademicError, match="division"):
        check(db_session_factory, {**_place(course_id), "division": None})
    with pytest.raises(AcademicError, match="from the list"):
        check(db_session_factory, {**_place(course_id), "batch": "Z9"})
    with pytest.raises(AcademicError, match="roll number"):
        check(db_session_factory, {**_place(course_id), "roll_number": " "})
    assert check(db_session_factory, _place(course_id, year=2))["year"] == 2


def test_account_creation_stores_the_place(db_session_factory: Factory) -> None:
    seed_demo_content(db_session_factory)
    course_id = _course(db_session_factory)
    api = CavyApi(db_session_factory, EventLogger(db_session_factory))
    refused = api.create_account("student", "Sam", "sam@x.com", _PW, None, {"course_id": None})
    assert refused["ok"] is False and "course" in refused["error"].lower()
    ok = api.create_account("student", "Sam", "sam@x.com", _PW, None, _place(course_id, 3, "B"))
    assert ok["ok"] is True
    api.login("student", "sam@x.com", _PW)
    profile = api.get_profile()
    assert (profile["course"], profile["year_label"], profile["division"]) == (
        "Engineering",
        "Third Year",
        "B",
    )
    assert profile["roll_number"] == "7"


# -- a professor taking groups -------------------------------------------------------------


@pytest.fixture
def school(db_session_factory: Factory) -> dict[str, Any]:
    seed_demo_content(db_session_factory)
    course_id = _course(db_session_factory)
    prof_a = create_professor_account(
        db_session_factory, display_name="Dr A", email="a@x.com", password=_PW
    )
    prof_b = create_professor_account(
        db_session_factory, display_name="Dr B", email="b@x.com", password=_PW
    )
    academics.set_professor_courses(db_session_factory, prof_a, [course_id])
    students = {}
    for name, year, batch in (("s1", 1, "A1"), ("s2", 1, "A2"), ("s3", 2, "A1")):
        students[name] = create_student_account(
            db_session_factory,
            display_name=name,
            email=f"{name}@x.com",
            password=_PW,
            cohort=academics.signup_cohort(
                db_session_factory, _place(course_id, year, "A", batch, name)
            ),
        )
    return {"course": course_id, "a": prof_a, "b": prof_b, "students": students}


def _owner(factory: Factory, student_id: int) -> int | None:
    with factory() as db:
        student = db.get(Student, student_id)
        assert student is not None
        return student.professor_id


def test_a_professor_takes_a_group_into_their_class(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    group = {"course_id": school["course"], "year": 1, "batch": "A1"}
    preview = academics.preview_group(db_session_factory, school["a"], group, every_course=False)
    assert preview["matching"] == 1 and preview["to_add"] == 1

    academics.add_group(
        db_session_factory,
        school["a"],
        {"course_id": school["course"], "year": 1},
        every_course=False,
    )
    s = school["students"]
    assert [_owner(db_session_factory, s[n]) for n in ("s1", "s2", "s3")] == [school["a"]] * 2 + [
        None
    ]
    labels = [g["label"] for g in academics.professor_groups(db_session_factory, school["a"])]
    assert labels == ["Engineering · First Year · all divisions · all batches"]


def test_only_assigned_courses_and_only_unclaimed_students(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    with pytest.raises(AcademicError, match="haven't been assigned"):
        academics.add_group(
            db_session_factory, school["b"], {"course_id": school["course"]}, every_course=False
        )
    s = school["students"]
    with db_session_factory() as db:
        student = db.get(Student, s["s1"])
        assert student is not None
        student.professor_id = school["b"]
        db.commit()
    academics.add_group(
        db_session_factory,
        school["a"],
        {"course_id": school["course"], "year": 1},
        every_course=False,
    )
    assert _owner(db_session_factory, s["s1"]) == school["b"]  # not taken from Dr B
    assert _owner(db_session_factory, s["s2"]) == school["a"]


def test_students_who_sign_up_later_join_the_group(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    academics.add_group(
        db_session_factory,
        school["a"],
        {"course_id": school["course"], "year": 3 - 2, "batch": "A2"},
        every_course=False,
    )
    late = create_student_account(
        db_session_factory,
        display_name="late",
        email="late@x.com",
        password=_PW,
        cohort=academics.signup_cohort(db_session_factory, _place(school["course"], 1, "B", "A2")),
    )
    other = create_student_account(
        db_session_factory,
        display_name="other",
        email="other@x.com",
        password=_PW,
        cohort=academics.signup_cohort(db_session_factory, _place(school["course"], 1, "B", "B1")),
    )
    assert _owner(db_session_factory, late) == school["a"]
    assert _owner(db_session_factory, other) is None

    rule = academics.professor_groups(db_session_factory, school["a"])[0]["id"]
    academics.remove_group(db_session_factory, school["a"], rule)
    again = create_student_account(
        db_session_factory,
        display_name="again",
        email="again@x.com",
        password=_PW,
        cohort=academics.signup_cohort(db_session_factory, _place(school["course"], 1, "A", "A2")),
    )
    assert _owner(db_session_factory, again) is None


def test_taking_courses_away_stops_the_group(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    academics.add_group(
        db_session_factory, school["a"], {"course_id": school["course"]}, every_course=False
    )
    academics.set_professor_courses(db_session_factory, school["a"], [])
    assert academics.professor_groups(db_session_factory, school["a"]) == []


# -- labs for one group --------------------------------------------------------------------


def _lab(target: dict[str, Any]) -> dict[str, Any]:
    stage = {"duration_minutes": 10, "ai_assistance_mode": "FULL"}
    return {
        "title": "Targeted lab",
        "topic": "t",
        "description": "d",
        "difficulty": "Easy",
        "stages": [stage, stage, {**stage, "ai_assistance_mode": "RESTRICTED"}],
        **target,
    }


def _server_api(factory: Factory) -> CavyApi:
    return CavyApi(factory, EventLogger(factory), restrict_signup=True)


def test_a_lab_reaches_only_the_group_it_was_made_for(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    professor = _server_api(db_session_factory)
    professor.login("professor", "a@x.com", _PW)
    created = professor.create_lab(
        _lab({"course_id": school["course"], "year": 1, "division": "A", "batch": "A1"})
    )
    assert created["ok"] is True

    def titles(email: str) -> set[str]:
        api = _server_api(db_session_factory)
        api.login("student", email, _PW)
        return {lab["title"] for lab in api.get_labs()}

    assert "Targeted lab" in titles("s1@x.com")  # year 1, batch A1
    assert "Targeted lab" not in titles("s2@x.com")  # batch A2
    assert "Targeted lab" not in titles("s3@x.com")  # second year

    outsider = _server_api(db_session_factory)
    outsider.login("student", "s2@x.com", _PW)
    task_id = created["task_id"]
    with pytest.raises(ValueError, match="isn't assigned to you"):
        outsider.get_stages(task_id)
    stage_id = professor.get_lab(task_id)  # the edit form carries the target
    assert (stage_id["course_id"], stage_id["year"], stage_id["batch"]) == (
        school["course"],
        1,
        "A1",
    )


def test_a_professor_must_aim_a_lab_at_one_of_their_courses(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    other = academics.add_course(db_session_factory, "Degree", 3)
    teacher = _server_api(db_session_factory)
    teacher.login("professor", "a@x.com", _PW)
    assert "Choose which course" in teacher.create_lab(_lab({}))["error"]
    assert "haven't been assigned" in teacher.create_lab(_lab({"course_id": other}))["error"]
    assert [c["name"] for c in teacher.get_my_courses()] == ["Engineering"]

    nobody = _server_api(db_session_factory)
    nobody.login("professor", "b@x.com", _PW)
    # No courses assigned: the lab simply goes to their own class.
    made = nobody.create_lab(_lab({}))
    assert made["ok"] is True
    mine = _server_api(db_session_factory)
    mine.login("student", "s1@x.com", _PW)
    assert "Targeted lab" not in {lab["title"] for lab in mine.get_labs()}
    with db_session_factory() as db:
        student = db.get(Student, school["students"]["s1"])
        assert student is not None
        student.professor_id = school["b"]
        db.commit()
    assert "Targeted lab" in {lab["title"] for lab in mine.get_labs()}


def test_editing_a_lab_can_change_its_group(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    teacher = _server_api(db_session_factory)
    teacher.login("professor", "a@x.com", _PW)
    task_id = teacher.create_lab(_lab({"course_id": school["course"], "year": 1}))["task_id"]
    result = teacher.update_lab(task_id, _lab({"course_id": school["course"], "year": 2}))
    assert result["ok"] is True
    assert teacher.get_lab(task_id)["year"] == 2
    bad = teacher.update_lab(task_id, _lab({"course_id": school["course"], "year": 9}))
    assert bad["ok"] is False


def test_unassigned_students_are_limited_to_a_professors_courses(
    db_session_factory: Factory, school: dict[str, Any]
) -> None:
    other = _course(db_session_factory, "Degree", 3)
    create_student_account(
        db_session_factory,
        display_name="deg",
        email="deg@x.com",
        password=_PW,
        cohort=academics.signup_cohort(db_session_factory, _place(other)),
    )
    teacher = _server_api(db_session_factory)
    teacher.login("professor", "a@x.com", _PW)
    emails = {s["email"] for s in teacher.get_unassigned_students()}
    assert "deg@x.com" not in emails and "s1@x.com" in emails


# -- the admin panel -----------------------------------------------------------------------


@pytest.fixture
def server(
    db_engine: Engine, db_session_factory: Factory
) -> Iterator[tuple[TestClient, ServerState, dict[str, str]]]:
    seed_demo_content(db_session_factory)
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state)) as http:
        token = http.post(
            "/admin/api/setup", json={"name": "Root", "email": "r@x.com", "password": _PW}
        ).json()["token"]
        yield http, state, {"Authorization": f"Bearer {token}"}


def test_the_admin_manages_courses_and_teaching(
    server: tuple[TestClient, ServerState, dict[str, str]], db_session_factory: Factory
) -> None:
    http, _, auth = server
    post = lambda path, body=None: http.post(f"/admin/api{path}", json=body or {}, headers=auth)  # noqa: E731

    assert post("/courses/add-suggested").json()["result"] == 3
    assert post("/courses/add-suggested").json()["result"] == 0  # nothing left to add
    body = http.get("/admin/api/courses", headers=auth).json()
    years = {c["name"]: c["years"] for c in body["courses"]}
    assert years == {"Degree": 3, "Masters": 2, "Engineering": 4}

    masters = next(c["id"] for c in body["courses"] if c["name"] == "Masters")
    assert (
        post(
            "/courses/options/add", {"course_id": masters, "kind": "batch", "name": "M1"}
        ).status_code
        == 200
    )
    assert (
        post(
            "/courses/options/add", {"course_id": masters, "kind": "batch", "name": "M1"}
        ).status_code
        == 400
    )
    assert (
        post("/courses/update", {"id": masters, "name": "Masters", "years": 2}).status_code == 200
    )

    prof = create_professor_account(
        db_session_factory, display_name="Dr P", email="p@x.com", password=_PW
    )
    assert (
        post("/courses/professors", {"professor_id": prof, "course_ids": [masters]}).status_code
        == 200
    )
    again = http.get("/admin/api/courses", headers=auth).json()
    assert next(c for c in again["courses"] if c["id"] == masters)["professors"] == [
        {"id": prof, "name": "Dr P"}
    ]
    users = http.get("/admin/api/users", headers=auth).json()
    assert users["professors"][0]["courses"] == ["Masters"]
    assert post("/courses/delete", {"id": masters}).status_code == 200


def test_the_admin_can_set_a_students_place(
    server: tuple[TestClient, ServerState, dict[str, str]], db_session_factory: Factory
) -> None:
    http, _, auth = server
    course_id = _course(db_session_factory)
    student = create_student_account(
        db_session_factory, display_name="Z", email="z@x.com", password=_PW
    )
    reply = http.post(
        "/admin/api/users/update",
        json={"role": "student", "id": student, "cohort": _place(course_id, 2, "B", "B1")},
        headers=auth,
    )
    assert reply.status_code == 200, reply.text
    row = next(
        s
        for s in http.get("/admin/api/users", headers=auth).json()["students"]
        if s["id"] == student
    )
    assert (row["course"], row["year"], row["division"], row["batch"]) == (
        "Engineering",
        2,
        "B",
        "B1",
    )
    bad = http.post(
        "/admin/api/users/update",
        json={"role": "student", "id": student, "cohort": _place(course_id, 7)},
        headers=auth,
    )
    assert bad.status_code == 400


def test_sign_up_options_are_public(
    server: tuple[TestClient, ServerState, dict[str, str]], db_session_factory: Factory
) -> None:
    http, _, _ = server
    _course(db_session_factory)
    listed = http.get("/api/academic").json()  # no token needed
    assert listed[0]["name"] == "Engineering" and len(listed[0]["year_options"]) == 4
