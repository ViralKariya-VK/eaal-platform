"""How the college really works: academic years from 1 July, SAP IDs, divisions and batches."""

from __future__ import annotations

import base64
import io
from collections.abc import Iterator
from datetime import date
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.buildinfo import VERSION, VERSION_HEADER
from eaal_platform.db import academic_calendar as cal
from eaal_platform.db import academics, roster
from eaal_platform.db.bootstrap import create_professor_account, seed_demo_content
from eaal_platform.db.models import ClassMember, Course, CourseOption, Student
from eaal_platform.events.logger import EventLogger
from eaal_platform.server.app import ServerState, create_app

Factory = sessionmaker[OrmSession]
OCT_2026 = date(2026, 10, 12)

# -- the calendar ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 6, 30), 2025),
        (date(2026, 7, 1), 2026),
        (date(2026, 10, 12), 2026),
        (date(2027, 1, 15), 2026),
        (date(2027, 6, 30), 2026),
        (date(2027, 7, 1), 2027),
    ],
)
def test_the_academic_year_starts_on_the_first_of_july(day: date, expected: int) -> None:
    assert cal.academic_year(day) == expected


def test_labels_and_years_of_study() -> None:
    assert cal.academic_year_label(2026) == "2026-27"
    assert cal.academic_year_label(2099) == "2099-00"
    # Admitted 2025: second year in 2026-27; first year in 2025-26.
    assert cal.year_of_study(2025, OCT_2026) == 2
    assert cal.year_of_study(2025, date(2026, 6, 30)) == 1
    assert cal.year_of_study(2025, date(2026, 7, 1)) == 2
    assert cal.year_of_study(2024, OCT_2026) == 3
    assert cal.year_of_study(2027, OCT_2026) == 0  # not admitted yet
    assert cal.batch_of(2025, 2) == "2025-2027"
    assert cal.batch_of(2024, 3) == "2024-2027"
    assert cal.admission_year_for(2, OCT_2026) == 2025


@pytest.mark.parametrize(
    ("year", "day", "expected"),
    [
        (2, date(2026, 10, 12), 3),  # October of the second year: semester III
        (2, date(2027, 2, 1), 4),  # February: semester IV
        (3, date(2026, 10, 12), 5),
        (1, date(2026, 7, 1), 1),
        (1, date(2027, 6, 30), 2),
    ],
)
def test_odd_semesters_run_july_to_december(year: int, day: date, expected: int) -> None:
    assert cal.semester(year, day) == expected


def test_roman_numerals() -> None:
    assert [cal.roman(n) for n in (1, 3, 5, 6)] == ["I", "III", "V", "VI"]


# -- sheet names, names and SAP IDs ------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "programme", "division", "semester"),
    [
        ("MSc DS Div-A (Sem-III)", "MSc Data Science", "A", 3),
        ("BSc DS Div-B (Sem-V)", "BSc Data Science", "B", 5),
        ("MSc SDS Div-A (Sem 1)", "MSc Statistics and Data Science", "A", 1),
        ("MSc ASA Div-C (Sem-IV)", "MSc Applied Statistics and Analytics", "C", 4),
        (
            "B.Sc Applied Statistics and Analytics Div A Sem II",
            "BSc Applied Statistics and Analytics",
            "A",
            2,
        ),
        ("Sheet1", None, None, None),
    ],
)
def test_a_sheet_name_says_programme_division_and_semester(
    title: str, programme: str | None, division: str | None, semester: int | None
) -> None:
    assert roster.parse_sheet_name(title) == {
        "programme": programme,
        "division": division,
        "semester": semester,
    }


def test_sap_ids_and_names_are_cleaned() -> None:
    assert roster.clean_sap_id(86032400039.0) == "86032400039"
    assert roster.clean_sap_id(" 86092500001 ") == "86092500001"
    for bad in (None, "", "ABC123", "12", 1.5):
        assert roster.clean_sap_id(bad) is None
    assert roster.tidy_name("ANUYA  NALAVADE") == "Anuya Nalavade"
    assert roster.tidy_name("Sobhagaya DATTA") == "Sobhagaya DATTA"  # mixed case: left alone


def test_the_first_half_of_a_division_is_batch_one() -> None:
    assert roster.split_batches(60) == ["1"] * 30 + ["2"] * 30
    assert roster.split_batches(5) == ["1"] * 3 + ["2"] * 2
    assert roster.split_batches(1) == ["1"]
    assert roster.split_batches(0) == []


# -- reading and planning ---------------------------------------------------------------------


def _workbook(sheets: dict[str, list[list[Any]]]) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _students(n: int, start: int, rolls: bool = True) -> list[list[Any]]:
    return [
        [f"STUDENT {i:02d}", str(start + i), f"A{i + 1:03d}" if rolls else None] for i in range(n)
    ]


_SHEET = "MSc DS Div-A (Sem-III)"


def test_a_workbook_is_read_by_header_names_not_positions(tmp_path: Any) -> None:
    data = _workbook(
        {
            _SHEET: [
                ["Roll No", "SAP ID", "Student Name"],
                ["A001", "86092500001", "ANUYA NALAVADE"],
            ],
            "Attendance": [["NAME", "ROLL NO", "P"], ["X", "A1", "P"]],  # no SAP column: skipped
        }
    )

    [group] = roster.read_groups(data, filename="x.xlsx")

    assert (group.programme, group.division, group.semester) == ("MSc Data Science", "A", 3)
    assert [(r.sap_id, r.name, r.roll) for r in group.rows] == [
        ("86092500001", "Anuya Nalavade", "A001")
    ]


def test_rows_without_a_name_or_sap_id_are_reported_not_loaded() -> None:
    data = _workbook(
        {
            _SHEET: [
                ["SAP ID", "Name", "Roll No"],
                ["86092500001", "Ok One", "A001"],
                ["not-a-number", "Bad Sap", "A002"],
                ["86092500003", "", "A003"],
                [None, None, None],
            ]
        }
    )

    [group] = roster.read_groups(data, filename="x.xlsx")

    assert len(group.rows) == 1
    assert [p["line"] for p in group.problems] == [3, 4]


def test_a_csv_is_one_list_named_after_the_file() -> None:
    csv_bytes = b"SAP ID,Name\n86092500001,ANUYA NALAVADE\n86092500002,PRATHIT PAITHANKAR\n"

    [group] = roster.read_groups(csv_bytes, filename="MSc DS Div-B (Sem-III).csv")

    assert (group.division, group.semester, len(group.rows)) == ("B", 3, 2)


def test_the_plan_works_out_year_batch_and_roll_numbers(db_session_factory: Factory) -> None:
    data = _workbook(
        {
            _SHEET: [
                ["SAP ID", "Name"],
                *[[a, b] for b, a, _ in _students(5, 86092500001, rolls=False)][::-1],
            ]
        }
    )

    plan = roster.build_plan(
        db_session_factory, roster.read_groups(data, filename="x.xlsx"), today=OCT_2026
    )

    assert [(s.roll, s.batch) for s in plan.students] == [
        ("A001", "1"),
        ("A002", "1"),
        ("A003", "1"),
        ("A004", "2"),
        ("A005", "2"),
    ]  # numbered in ascending SAP ID order, whatever order the sheet had
    assert {(s.admission_year, s.year, s.division) for s in plan.students} == {(2025, 2, "A")}
    assert plan.groups[0]["batch_of"] == "2025-2027"


def test_given_roll_numbers_are_kept_even_with_gaps(db_session_factory: Factory) -> None:
    rows = [
        ["SAP ID", "Name", "Roll No"],
        ["86092500001", "A", "A001"],
        ["86092500002", "B", "A004"],
        ["86092500003", "C", "A002"],
        ["86092500004", "D", "A005"],
    ]
    data = _workbook({_SHEET: rows})

    plan = roster.build_plan(
        db_session_factory, roster.read_groups(data, filename="x.xlsx"), today=OCT_2026
    )

    # Batches follow roll-number order: A001, A002 | A004, A005
    assert {s.name: (s.roll, s.batch) for s in plan.students} == {
        "A": ("A001", "1"),
        "C": ("A002", "1"),
        "B": ("A004", "2"),
        "D": ("A005", "2"),
    }


def test_a_sap_id_used_twice_is_left_out_and_reported(db_session_factory: Factory) -> None:
    data = _workbook(
        {
            _SHEET: [["SAP ID", "Name", "Roll No"], ["86092500063", "Yashika", "A061"]],
            "MSc DS Div-B (Sem-III)": [
                ["SAP ID", "Name", "Roll No"],
                ["86092500063", "Shatakshi", "B001"],
                ["86092500064", "Nishant", "B002"],
            ],
        }
    )

    plan = roster.build_plan(
        db_session_factory, roster.read_groups(data, filename="x.xlsx"), today=OCT_2026
    )

    assert sorted(s.name for s in plan.students) == ["Nishant", "Yashika"]
    [problem] = plan.problems
    assert problem["name"] == "Shatakshi" and "also used" in problem["error"]


def test_unknown_programme_division_or_semester_is_a_problem_not_a_guess(
    db_session_factory: Factory,
) -> None:
    data = _workbook({"Sheet1": [["SAP ID", "Name"], ["86092500001", "A"]]})
    groups = roster.read_groups(data, filename="x.xlsx")

    assert "not set up" in roster.build_plan(db_session_factory, groups).problems[0]["error"]
    still = roster.build_plan(db_session_factory, groups, programme="MSc Data Science")
    assert "division" in still.problems[0]["error"]
    ok = roster.build_plan(
        db_session_factory,
        groups,
        programme="MSc Data Science",
        division="a",
        semester=3,
        today=OCT_2026,
    )
    assert not ok.problems and ok.students[0].division == "A"
    beyond = roster.build_plan(
        db_session_factory, groups, programme="MSc Data Science", division="A", semester=5
    )
    assert "beyond" in beyond.problems[0]["error"]


# -- writing it --------------------------------------------------------------------------------


def _import(factory: Factory, sheets: dict[str, list[list[Any]]]) -> roster.Result:
    plan = roster.build_plan(
        factory, roster.read_groups(_workbook(sheets), filename="x.xlsx"), today=OCT_2026
    )
    return roster.apply_plan(factory, plan)


def _sheet(n: int = 4) -> dict[str, list[list[Any]]]:
    return {
        _SHEET: [
            ["SAP ID", "Name", "Roll No"],
            *[[sap, name, roll] for name, sap, roll in _students(n, 86092500001)],
        ]
    }


def test_the_six_programmes_are_set_up_once(db_session_factory: Factory) -> None:
    assert roster.load_programmes(db_session_factory) == 6
    assert roster.load_programmes(db_session_factory) == 0
    with db_session_factory() as db:
        years = {c.name: c.years for c in db.query(Course).all()}
    assert years["MSc Data Science"] == 2 and years["BSc Applied Statistics and Analytics"] == 3
    assert len(years) == 6


def test_importing_creates_accounts_placed_by_the_college(db_session_factory: Factory) -> None:
    result = _import(db_session_factory, _sheet(4))

    assert (result.created, result.updated) == (4, 0)
    with db_session_factory() as db:
        first = db.query(Student).filter_by(sap_id="86092500001").one()
        assert first.display_name == "Student 00" and first.email is None
        assert (first.division, first.batch, first.roll_number) == ("A", "1", "A001")
        assert (first.admission_year, first.year, first.enrollment_no) == (2025, 2, "86092500001")
        assert first.must_change_password is True and first.password_hash
        last = db.query(Student).filter_by(sap_id="86092500004").one()
        assert last.batch == "2"
        names = {(o.kind, o.name) for o in db.query(CourseOption).all()}
        assert {("division", "A"), ("batch", "1"), ("batch", "2")} <= names
    assert {c["sap_id"] for c in result.credentials} == {f"8609250000{i}" for i in range(1, 5)}
    assert all(len(c["temporary_password"]) >= 8 for c in result.credentials)


def test_students_sign_in_with_their_sap_id_and_choose_their_own_password(
    db_session_factory: Factory,
) -> None:
    result = _import(db_session_factory, _sheet(2))
    temporary = result.credentials[0]["temporary_password"]
    app = CavyApi(db_session_factory, EventLogger(db_session_factory))

    assert app.login("student", "86092500001", "wrong")["ok"] is False
    login = app.login("student", " 86092500001 ", temporary)
    assert login["ok"] and login["must_change_password"] and not login["needs_details"]
    # The college already placed them, so they are not asked for course, year, division or batch.
    assert app.complete_first_login("My-own-pass-1")["ok"] is True
    profile = app.get_profile()
    assert (profile["sap_id"], profile["batch_of"], profile["semester_label"]) == (
        "86092500001",
        "2025-2027",
        "Sem III",
    )
    app.logout()
    assert app.login("student", "86092500001", "My-own-pass-1")["ok"] is True


def test_importing_again_updates_in_place_and_keeps_passwords(db_session_factory: Factory) -> None:
    _import(db_session_factory, _sheet(3))
    with db_session_factory() as db:
        before = db.query(Student).filter_by(sap_id="86092500002").one().password_hash
    sheets = _sheet(3)
    sheets[_SHEET][2][1] = "Renamed Person"

    again = _import(db_session_factory, sheets)

    assert (again.created, again.updated, again.credentials) == (0, 3, [])
    with db_session_factory() as db:
        student = db.query(Student).filter_by(sap_id="86092500002").one()
        assert student.display_name == "Renamed Person" and student.password_hash == before
        assert db.query(Student).count() == 3


def test_a_professor_taking_a_group_gets_imported_students_in_class(
    db_session_factory: Factory,
) -> None:
    roster.load_programmes(db_session_factory)
    professor = create_professor_account(
        db_session_factory, display_name="Dr K", email="k@college.edu", password="hunter2-hunter2"
    )
    with db_session_factory() as db:
        course = db.query(Course).filter_by(name="MSc Data Science").one().id
    academics.set_professor_courses(db_session_factory, professor, [course])

    _import(db_session_factory, _sheet(3))

    with db_session_factory() as db:
        members = db.query(ClassMember).filter_by(professor_id=professor).count()
    assert members == 3  # accounts without an email count as registered students


# -- the years move by themselves ----------------------------------------------------------------


def test_everyone_moves_up_on_the_first_of_july(db_session_factory: Factory) -> None:
    _import(db_session_factory, _sheet(3))
    with db_session_factory() as db:
        assert {s.year for s in db.query(Student).all()} == {2}

    assert academics.sync_student_years(db_session_factory, date(2027, 6, 30)) == 0
    assert academics.sync_student_years(db_session_factory, date(2027, 7, 1)) == 3

    with db_session_factory() as db:
        students = db.query(Student).all()
        assert {s.year for s in students} == {0}  # a 2-year programme is over for the 2025 intake
        place = academics.describe(students[0], date(2027, 7, 1))
        assert (
            place["completed"] is True
            and place["batch_of"] == "2025-2027"
            and place["semester"] is None
        )


def test_a_later_intake_waits_then_starts_in_year_one(db_session_factory: Factory) -> None:
    _import(db_session_factory, _sheet(2))
    with db_session_factory() as db:
        for student in db.query(Student).all():
            student.admission_year = 2027
        db.commit()

    academics.sync_student_years(db_session_factory, OCT_2026)
    with db_session_factory() as db:
        assert {s.year for s in db.query(Student).all()} == {None}
    academics.sync_student_years(db_session_factory, date(2027, 7, 1))
    with db_session_factory() as db:
        assert {s.year for s in db.query(Student).all()} == {1}


def test_year_is_kept_current_when_someone_logs_in(db_session_factory: Factory) -> None:
    result = _import(db_session_factory, _sheet(2))
    with db_session_factory() as db:
        for student in db.query(Student).all():
            student.admission_year = 2020  # long since finished
        db.commit()
    app = CavyApi(db_session_factory, EventLogger(db_session_factory))

    app.login("student", "86092500001", result.credentials[0]["temporary_password"])

    with db_session_factory() as db:
        assert {s.year for s in db.query(Student).all()} == {0}


def test_a_sap_id_belongs_to_one_person(db_session_factory: Factory) -> None:
    _import(db_session_factory, _sheet(1))
    with db_session_factory() as db:
        db.add(Student(display_name="Twin", sap_id="86092500001"))
        with pytest.raises(Exception, match="UNIQUE"):
            db.commit()


# -- the admin panel ---------------------------------------------------------------------------


@pytest.fixture
def admin(
    db_engine: Engine, db_session_factory: Factory
) -> Iterator[tuple[TestClient, dict[str, str], Factory]]:
    seed_demo_content(db_session_factory)
    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state), headers={VERSION_HEADER: VERSION}) as http:
        reply = http.post(
            "/admin/api/setup",
            json={"name": "Root", "email": "root@x.com", "password": "hunter2-hunter2"},
        )
        yield http, {"Authorization": f"Bearer {reply.json()['token']}"}, db_session_factory


def _upload(sheets: dict[str, list[list[Any]]], **extra: Any) -> dict[str, Any]:
    return {
        "filename": "students.xlsx",
        "data_base64": base64.b64encode(_workbook(sheets)).decode(),
        **extra,
    }


def test_the_admin_can_check_then_import_and_gets_the_passwords_once(
    admin: tuple[TestClient, dict[str, str], Factory],
) -> None:
    http, headers, factory = admin

    check = http.post("/admin/api/roster/import", json=_upload(_sheet(4)), headers=headers).json()
    assert check["committed"] is False and check["students"] == 4
    assert check["groups"][0]["batch_of"] is not None
    with factory() as db:
        assert db.query(Student).count() == 0  # checking changed nothing

    done = http.post(
        "/admin/api/roster/import", json=_upload(_sheet(4), commit=True), headers=headers
    ).json()
    assert done["committed"] and done["created"] == 4 and len(done["credentials"]) == 4
    with factory() as db:
        assert db.query(Student).count() == 4
    users = http.get("/admin/api/users", headers=headers).json()
    assert {s["sap_id"] for s in users["students"]} == {f"8609250000{i}" for i in range(1, 5)}
    assert users["students"][0]["batch_of"] == "2025-2027"


def test_the_admin_import_rejects_unreadable_or_empty_files(
    admin: tuple[TestClient, dict[str, str], Factory],
) -> None:
    http, headers, _ = admin

    junk = http.post(
        "/admin/api/roster/import",
        json={"filename": "x.xlsx", "data_base64": "bm90IGEgd29ya2Jvb2s="},
        headers=headers,
    )
    empty = http.post(
        "/admin/api/roster/import",
        json=_upload({"Sheet1": [["colour", "size"], ["red", "big"]]}),
        headers=headers,
    )
    anonymous = http.post("/admin/api/roster/import", json=_upload(_sheet(1)))

    assert junk.status_code == 400 and empty.status_code == 400 and anonymous.status_code == 401
    assert "SAP ID column" in empty.json()["detail"]


def test_the_admin_can_set_up_the_programmes(
    admin: tuple[TestClient, dict[str, str], Factory],
) -> None:
    http, headers, _ = admin

    assert http.post("/admin/api/programmes/load", headers=headers).json() == {"added": 6}
    assert http.post("/admin/api/programmes/load", headers=headers).json() == {"added": 0}


# -- the official list is the truth --------------------------------------------------------------


def _directory(people: list[tuple[str, str, str, str]]) -> bytes:
    rows: list[list[Any]] = [
        ["SAP ID", "Email ID", "FirstName", "LastName", "DisplayName", "Password"]
    ]
    for sap, first, last, email in people:
        rows.append([sap, email, first, last, f"{first} {last} - {sap}", "Shared#Start1"])
    return _workbook({"MSc DS SEM I": rows})


def test_the_official_list_is_read_without_its_shared_password() -> None:
    people = roster.read_directory(
        _directory([("86092500001", "ANUYA", "NALAVADE", "ANUYA.NALAVADE001@NMIMS.IN")]),
        filename="d.xlsx",
    )

    assert [(p.sap_id, p.name, p.email) for p in people] == [
        ("86092500001", "Anuya Nalavade", "anuya.nalavade001@nmims.in")
    ]
    assert not hasattr(people[0], "password")


def test_matching_prefers_the_official_list_when_sheet_ids_are_wrong(
    db_session_factory: Factory,
) -> None:
    people = roster.read_directory(
        _directory(
            [
                ("86092500063", "YASHIKA", "PARMAR", "yashika.parmar063@nmims.in"),
                ("86092500064", "SHATAKSHI", "PANDEY", "shatakshi.pandey064@nmims.in"),
                ("86092500016", "SHRVANI", "THAKUR", "shrvani.thakur016@nmims.in"),
                ("86092500090", "NO", "SHEETROW", "no.sheetrow090@nmims.in"),
            ]
        ),
        filename="d.xlsx",
    )
    sheets = {
        "MSc DS Div-A (Sem-III)": [
            ["SAP ID", "Name", "Roll No"],
            ["86092500016", "Shrawani Thakur", "A001"],  # spelt differently, SAP ID right
            ["86092500063", "Yashika Parmar", "A002"],
        ],
        "MSc DS Div-B (Sem-III)": [
            ["SAP ID", "Name", "Roll No"],
            ["86092500063", "Shatakshi Pandey", "B001"],  # the sheet reused Yashika's number
            ["86092500999", "Stranger Danger", "B002"],  # nobody on the list
        ],
    }
    groups = roster.read_groups(_workbook(sheets), filename="x.xlsx")

    unplaced, problems = roster.merge_directory(groups, people, programme="MSc Data Science")

    assert [p.sap_id for p in unplaced] == ["86092500090"]
    assert [p["name"] for p in problems] == ["Stranger Danger"]
    rows = {r.name: (r.sap_id, r.email) for g in groups for r in g.rows}
    assert rows["Shatakshi Pandey"] == ("86092500064", "shatakshi.pandey064@nmims.in")
    assert rows["Shrvani Thakur"][0] == "86092500016"
    assert rows["Yashika Parmar"][0] == "86092500063"


def test_people_with_no_division_are_loaded_without_one(db_session_factory: Factory) -> None:
    group = roster.Group(
        "Official list (no division)",
        "MSc Data Science",
        None,
        3,
        None,
        [roster.Row(0, "86092500090", "No Sheetrow", None, "no.sheetrow090@nmims.in")],
        allow_unplaced=True,
    )

    plan = roster.build_plan(db_session_factory, [group], today=OCT_2026)
    roster.apply_plan(db_session_factory, plan)

    with db_session_factory() as db:
        student = db.query(Student).one()
        assert (student.division, student.batch, student.roll_number) == (None, None, None)
        assert (student.year, student.email) == (2, "no.sheetrow090@nmims.in")


def test_named_people_are_taken_out_to_become_teachers() -> None:
    people = [
        roster.Person("86092500001", "VIRAL", "KARIYA", "viral.kariya001@nmims.in"),
        roster.Person("86092500002", "OTHER", "PERSON", "other.person002@nmims.in"),
    ]

    rest, taken = roster.split_out(people, ["viral kariya", "Nobody Here"])

    assert [p.sap_id for p in rest] == ["86092500002"] and [p.sap_id for p in taken] == [
        "86092500001"
    ]


@pytest.mark.parametrize(
    ("name", "sap", "expected"),
    [
        ("Anuya Nalavade", "86092500001", "anuya.nalavade001@nmims.in"),
        ("SHLOK AGRAWAL", "86032400039", "shlok.agrawal039@nmims.in"),
        ("Keerthana Senthil Kumar", "86032400067", None),  # three words: not guessed
        ("Shambhavi .", "86032400069", None),
        ("Mono", "86032400001", None),
    ],
)
def test_the_college_email_scheme_is_only_used_when_unambiguous(
    name: str, sap: str, expected: str | None
) -> None:
    assert roster.derive_email(name, sap) == expected


def test_derived_emails_are_flagged_and_never_shared(db_session_factory: Factory) -> None:
    sheets = {
        _SHEET: [
            ["SAP ID", "Name", "Roll No"],
            ["86092500001", "Same Name", "A001"],
            ["86092500101", "Same Name", "A002"],
        ]
    }
    plan = roster.build_plan(
        db_session_factory,
        roster.read_groups(_workbook(sheets), filename="x.xlsx"),
        today=OCT_2026,
        derive_emails=True,
    )

    emails = [s.email for s in plan.students]
    assert emails == ["same.name001@nmims.in", "same.name101@nmims.in"]
    assert any("worked out from the college's scheme" in n for n in plan.notes)

    roster.apply_plan(db_session_factory, plan)
    again = roster.build_plan(
        db_session_factory,
        roster.read_groups(
            _workbook(
                {_SHEET: [["SAP ID", "Name", "Roll No"], ["86092500777", "Same Name", "A003"]]}
            ),
            filename="x.xlsx",
        ),
        today=OCT_2026,
    )
    assert again.students[0].email is None


# -- starting again --------------------------------------------------------------------------------


def test_wiping_keeps_only_the_administrators_settings(
    db_engine: Engine, db_session_factory: Factory
) -> None:
    from eaal_platform.db.bootstrap import create_student_account
    from eaal_platform.db.maintenance import wipe_data
    from eaal_platform.db.models import Admin, AppSetting, Task

    seed_demo_content(db_session_factory)
    create_student_account(
        db_session_factory, display_name="S", email="s@x.com", password="hunter2-hunter2"
    )
    with db_session_factory() as db:
        db.add(Admin(display_name="Root", email="r@x.com", password_hash="x"))
        db.add(AppSetting(key="lab_mode", value="on"))
        db.commit()

    removed = wipe_data(db_engine)

    assert removed["students"] == 1 and removed["tasks"] >= 3
    with db_session_factory() as db:
        assert db.query(Student).count() == 0 and db.query(Task).count() == 0
        assert db.query(Admin).count() == 1 and db.query(AppSetting).count() == 1


def test_a_backup_is_a_working_copy(tmp_path: Any) -> None:
    import sqlite3

    from eaal_platform.db.maintenance import backup_database

    source = tmp_path / "server.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.execute("INSERT INTO t VALUES (42)")

    copy = backup_database(source)

    assert copy.parent == tmp_path / "backups" and "before-clean" in copy.name
    assert sqlite3.connect(copy).execute("SELECT x FROM t").fetchone() == (42,)


def test_a_classroom_server_starts_without_demo_labs(tmp_path: Any) -> None:
    from eaal_platform.db.models import Task
    from eaal_platform.server.host import build_state

    state = build_state(tmp_path / "s.db")
    with state.session_factory() as db:
        assert [t.title for t in db.query(Task).all()] == ["Practice"]
    state.engine.dispose()
