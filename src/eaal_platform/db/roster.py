"""Loading the college's student lists: programmes, SAP IDs, divisions, batches and roll numbers.

A college sends spreadsheets with a name, a SAP ID and (usually) a roll number, one sheet per
division, for example ``MSc DS Div-A (Sem-III)``. This module reads them and works out the rest:

- **Programme** from the sheet name (``MSc DS`` -> *MSc Data Science*), **division** from ``Div-A``.
- **Year of study and admission year** from the semester in the sheet name and today's date
  (Semester III in October 2026 is the second year, so the 2025 intake).
- **Roll numbers** are kept as given; if a sheet has none they are numbered ``A001``, ``A002``... in
  ascending SAP ID order.
- **Batches**: Batch 1 is the first half of the division in roll-number order, Batch 2 the rest.
- Each student gets an account that is signed into with the SAP ID and a one-time temporary password
  (they choose their own at first sign-in). An email can be added later.

Reading a file and making a plan changes nothing; only ``apply_plan`` writes to the database.
Rows that cannot be loaded (a SAP ID used twice, a missing name, an unknown programme) are listed
as problems and left out, never guessed at.

Pure database logic: no HTTP, no screens.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.auth import generate_temporary_password, hash_password
from eaal_platform.db import academic_calendar as cal
from eaal_platform.db import academics
from eaal_platform.db.models import Course, CourseOption, Student

# name, years, level, department
PROGRAMMES: tuple[tuple[str, int, str, str], ...] = (
    ("MSc Data Science", 2, "MASTERS", "Data Science"),
    ("MSc Statistics and Data Science", 2, "MASTERS", "Data Science"),
    ("MSc Applied Statistics and Analytics", 2, "MASTERS", "Science"),
    ("BSc Data Science", 3, "BACHELORS", "Data Science"),
    ("BSc Statistics and Data Science", 3, "BACHELORS", "Data Science"),
    ("BSc Applied Statistics and Analytics", 3, "BACHELORS", "Science"),
)
# How a sheet name refers to a specialization.
_SPECIALIZATIONS = (
    (
        "Statistics and Data Science",
        re.compile(r"\b(sds|stat\w*\s*(and|&)?\s*(ds|data\s*science))\b", re.I),
    ),
    (
        "Applied Statistics and Analytics",
        re.compile(r"\b(asa|applied\s*stat\w*(\s*(and|&)?\s*analytics)?)\b", re.I),
    ),
    ("Data Science", re.compile(r"\b(ds|data\s*science)\b", re.I)),
)
_DEGREE = re.compile(r"\b(m\.?\s?sc|b\.?\s?sc)\b", re.I)
_DIVISION = re.compile(r"\bdiv(?:ision)?[\s._-]*([A-Za-z0-9]+)", re.I)
_SEMESTER = re.compile(r"\bsem(?:ester)?[\s._-]*([IVX]+|\d+)\b", re.I)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10}
_SAP_NAMES = ("sap id", "sapid", "sap", "sap no", "sap number")
_NAME_NAMES = ("student name", "name", "full name", "student")
_ROLL_NAMES = ("roll no", "roll number", "roll no.", "roll", "rollno")


@dataclass
class Row:
    line: int
    sap_id: str
    name: str
    roll: str | None


@dataclass
class Group:
    """One sheet (one division of one programme)."""

    sheet: str
    programme: str | None
    division: str | None
    semester: int | None
    admission_year: int | None
    rows: list[Row] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)


def _roman_to_int(text: str) -> int:
    if text.isdigit():
        return int(text)
    total = 0
    values = [_ROMAN_VALUES.get(c.upper(), 0) for c in text]
    for index, value in enumerate(values):
        total += -value if index + 1 < len(values) and value < values[index + 1] else value
    return total


def clean_sap_id(value: Any) -> str | None:
    """A SAP ID as text of digits (a spreadsheet may hand over 8.6e10 as a number)."""
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        value = int(value)
    text = str(value).strip()
    return text if re.fullmatch(r"\d{5,15}", text) else None


def tidy_name(name: str) -> str:
    """``ANUYA NALAVADE`` -> ``Anuya Nalavade``; a name already in mixed case is left alone."""
    name = re.sub(r"\s+", " ", name).strip()
    if name.isupper() or name.islower():
        return " ".join(part.capitalize() for part in name.split(" "))
    return name


def parse_sheet_name(title: str) -> dict[str, Any]:
    """What a sheet name says: programme (e.g. ``MSc Data Science``), division and semester."""
    programme = None
    degree = _DEGREE.search(title)
    if degree:
        for specialization, pattern in _SPECIALIZATIONS:
            if pattern.search(title[degree.end() :]):
                programme = (
                    f"{'MSc' if degree[1].lower().startswith('m') else 'BSc'} {specialization}"
                )
                break
    division = _DIVISION.search(title)
    semester = _SEMESTER.search(title)
    return {
        "programme": programme,
        "division": division[1].upper() if division else None,
        "semester": _roman_to_int(semester[1]) if semester else None,
    }


def _find_columns(header: list[Any]) -> dict[str, int] | None:
    cells = [str(c).strip().lower() if c is not None else "" for c in header]
    found: dict[str, int] = {}
    for key, names in (("sap", _SAP_NAMES), ("name", _NAME_NAMES), ("roll", _ROLL_NAMES)):
        for index, cell in enumerate(cells):
            if cell in names and index not in found.values():
                found[key] = index
                break
    return found if "sap" in found and "name" in found else None


def _sheets(source: Path | bytes, filename: str | None) -> list[tuple[str, list[tuple[Any, ...]]]]:
    """The tables in a file: each sheet of a workbook, or a CSV as one table named for the file."""
    name = filename or (source.name if isinstance(source, Path) else "")
    if name.lower().endswith((".csv", ".txt")):
        raw = source if isinstance(source, bytes) else Path(source).read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        table = [tuple(row) for row in csv.reader(io.StringIO(text))]
        return [(Path(name).stem, table)]
    handle: Any = io.BytesIO(source) if isinstance(source, bytes) else source
    workbook = openpyxl.load_workbook(handle, data_only=True, read_only=True)
    sheets = [(s.title, list(s.iter_rows(values_only=True))) for s in workbook.worksheets]
    workbook.close()
    return sheets


def read_groups(
    source: Path | bytes, *, filename: str | None = None, today: date | None = None
) -> list[Group]:
    """Read every sheet that looks like a student list (one without a SAP ID column is skipped)."""
    on = today or date.today()
    groups: list[Group] = []
    for title, rows in _sheets(source, filename):
        header_at = next((i for i, row in enumerate(rows[:10]) if _find_columns(list(row))), None)
        if header_at is None:
            continue
        columns = _find_columns(list(rows[header_at])) or {}
        parsed = parse_sheet_name(title)
        semester = parsed["semester"]
        admission = cal.admission_year_for((semester + 1) // 2, on) if semester else None
        group = Group(title, parsed["programme"], parsed["division"], semester, admission)
        for offset, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
            cells = list(row)
            raw_sap = cells[columns["sap"]] if columns["sap"] < len(cells) else None
            raw_name = cells[columns["name"]] if columns["name"] < len(cells) else None
            raw_roll = (
                cells[columns["roll"]]
                if "roll" in columns and columns["roll"] < len(cells)
                else None
            )
            if raw_sap in (None, "") and raw_name in (None, ""):
                continue
            sap = clean_sap_id(raw_sap)
            name = str(raw_name).strip() if raw_name is not None else ""
            if sap is None or not name:
                group.problems.append(
                    {
                        "sheet": title,
                        "line": offset,
                        "sap_id": str(raw_sap or ""),
                        "name": name,
                        "error": "A student needs a name and a SAP ID made of digits.",
                    }
                )
                continue
            roll = str(raw_roll).strip() if raw_roll not in (None, "") else None
            group.rows.append(Row(offset, sap, tidy_name(name), roll))
        groups.append(group)
    return groups


def _roll_key(roll: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Za-z]*)\s*0*(\d+)", roll.strip())
    return (match[1].upper(), int(match[2])) if match else (roll.upper(), 0)


def split_batches(count: int) -> list[str]:
    """Batch names for ``count`` students in order: the first half is Batch 1, the rest Batch 2."""
    first = (count + 1) // 2
    return ["1"] * first + ["2"] * (count - first)


# -- the plan --------------------------------------------------------------------------------


@dataclass
class PlannedStudent:
    sap_id: str
    name: str
    roll: str
    division: str
    batch: str
    admission_year: int
    year: int
    programme: str


@dataclass
class Plan:
    students: list[PlannedStudent] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)


def build_plan(
    session_factory: sessionmaker[OrmSession],
    groups: list[Group],
    *,
    today: date | None = None,
    programme: str | None = None,
    division: str | None = None,
    semester: int | None = None,
) -> Plan:
    """Turn what was read into what would be written. The three keyword arguments fill in what a
    sheet name does not say (a single list called ``Sheet1``, say)."""
    on = today or date.today()
    plan = Plan()
    seen: dict[str, str] = {}
    with session_factory() as db:
        # Programmes that exist, plus the college's standard ones that apply_plan will set up.
        years_of = {name: years for name, years, _, _ in PROGRAMMES}
        years_of.update({c.name: c.years for c in db.query(Course).all()})
        for group in groups:
            plan.problems.extend(group.problems)
            name = group.programme or programme
            div = group.division or (division.upper() if division else None)
            sem = group.semester or semester
            info: dict[str, Any] = {
                "sheet": group.sheet,
                "programme": name,
                "division": div,
                "semester": sem,
                "students": 0,
                "batch_1": 0,
                "batch_2": 0,
            }
            plan.groups.append(info)
            programme_years = years_of.get(name or "")
            if programme_years is None or not div or not sem:
                reason = (
                    f"The programme {name or '(not recognised)'} is not set up."
                    if programme_years is None
                    else "The division is not in the sheet name; choose one."
                    if not div
                    else "The semester is not in the sheet name; choose one."
                )
                for row in group.rows:
                    plan.problems.append(
                        {
                            "sheet": group.sheet,
                            "line": row.line,
                            "sap_id": row.sap_id,
                            "name": row.name,
                            "error": reason,
                        }
                    )
                continue
            year = (sem + 1) // 2
            if year > programme_years:
                for row in group.rows:
                    plan.problems.append(
                        {
                            "sheet": group.sheet,
                            "line": row.line,
                            "sap_id": row.sap_id,
                            "name": row.name,
                            "error": f"Semester {sem} is beyond the {programme_years}-year course.",
                        }
                    )
                continue
            admission = cal.admission_year_for(year, on)
            info["admission_year"] = admission
            info["batch_of"] = cal.batch_of(admission, programme_years)
            usable: list[Row] = []
            for row in group.rows:
                earlier = seen.get(row.sap_id)
                if earlier is not None:
                    plan.problems.append(
                        {
                            "sheet": group.sheet,
                            "line": row.line,
                            "sap_id": row.sap_id,
                            "name": row.name,
                            "error": f"SAP ID {row.sap_id} is also used in {earlier}; left out.",
                        }
                    )
                    continue
                seen[row.sap_id] = f"{group.sheet} (line {row.line})"
                usable.append(row)
            _number_rolls(usable, div)
            ordered = sorted(usable, key=lambda r: _roll_key(r.roll or ""))
            for row, batch in zip(ordered, split_batches(len(ordered)), strict=True):
                plan.students.append(
                    PlannedStudent(
                        row.sap_id,
                        row.name,
                        row.roll or "",
                        div,
                        batch,
                        admission,
                        year,
                        name or "",
                    )
                )
            info["students"] = len(ordered)
            info["batch_1"] = sum(1 for b in split_batches(len(ordered)) if b == "1")
            info["batch_2"] = info["students"] - info["batch_1"]
    return plan


def _number_rolls(rows: list[Row], division: str) -> None:
    """Keep given roll numbers; give the rest the next free ones in ascending SAP ID order."""
    taken = {r.roll for r in rows if r.roll}
    counter = 0
    for row in sorted(rows, key=lambda r: int(r.sap_id)):
        if row.roll:
            continue
        while True:
            counter += 1
            candidate = f"{division}{counter:03d}"
            if candidate not in taken:
                break
        row.roll = candidate
        taken.add(candidate)


# -- writing it ---------------------------------------------------------------------------------


@dataclass
class Result:
    created: int = 0
    updated: int = 0
    credentials: list[dict[str, str]] = field(default_factory=list)


def load_programmes(session_factory: sessionmaker[OrmSession]) -> int:
    """Make sure the college's six programmes exist (idempotent); returns how many were added."""
    added = 0
    for name, years, level, department in PROGRAMMES:
        with session_factory() as db:
            if db.query(Course).filter(Course.name == name).first() is None:
                academics.add_course(session_factory, name, years, level, department)
                added += 1
    return added


def _ensure_option(db: OrmSession, course_id: int, kind: str, name: str) -> None:
    exists = (
        db.query(CourseOption)
        .filter(
            CourseOption.course_id == course_id,
            CourseOption.kind == kind,
            CourseOption.name == name,
        )
        .first()
    )
    if exists is None:
        db.add(CourseOption(course_id=course_id, kind=kind, name=name))


def apply_plan(session_factory: sessionmaker[OrmSession], plan: Plan) -> Result:
    """Create or update the students in a plan.

    A student already known by SAP ID is updated in place
    (name, roll number, place) and keeps their password; a new one gets a temporary password."""
    result = Result()
    changed: list[int] = []
    load_programmes(session_factory)
    with session_factory() as db:
        course_ids = {c.name: c.id for c in db.query(Course).all()}
        for item in plan.students:
            course_id = course_ids[item.programme]
            _ensure_option(db, course_id, "division", item.division)
            _ensure_option(db, course_id, "batch", item.batch)
            student = db.query(Student).filter(Student.sap_id == item.sap_id).first()
            if student is None:
                temporary = generate_temporary_password()
                student = Student(
                    display_name=item.name,
                    sap_id=item.sap_id,
                    password_hash=hash_password(temporary),
                    must_change_password=True,
                )
                db.add(student)
                result.created += 1
                result.credentials.append(
                    {
                        "sap_id": item.sap_id,
                        "name": item.name,
                        "division": item.division,
                        "batch": item.batch,
                        "temporary_password": temporary,
                    }
                )
            else:
                student.display_name = item.name
                result.updated += 1
            student.enrollment_no = item.sap_id
            student.roll_number = item.roll
            student.course_id = course_id
            student.division = item.division
            student.batch = item.batch
            student.admission_year = item.admission_year
            student.year = item.year
            db.flush()
            changed.append(student.id)
        db.commit()
    for student_id in changed:
        academics.assign_by_groups(session_factory, student_id)
    return result


def credentials_csv(credentials: list[dict[str, str]]) -> str:
    """The temporary passwords as a spreadsheet to hand out (they are shown only once)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["SAP ID", "Name", "Division", "Batch", "Temporary password"])
    for item in credentials:
        writer.writerow(
            [
                item["sap_id"],
                item["name"],
                item["division"],
                item["batch"],
                item["temporary_password"],
            ]
        )
    return out.getvalue()


def summarise(plan: Plan) -> dict[str, Any]:
    """What the import screen shows before anything is written."""
    return {
        "groups": plan.groups,
        "students": len(plan.students),
        "problems": plan.problems,
    }
