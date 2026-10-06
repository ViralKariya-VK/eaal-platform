"""Courses, years, divisions and batches; where students sit; who teaches whom.

An administrator maintains the lists (a course with its number of years, and
the division and batch names that students and labs can pick from). Students
choose their place at sign-up, professors are given courses to teach, and a
professor takes whole groups (course, year, division, batch) into their class.
Labs can then be aimed at one such group.

Pure database logic: no HTTP, no screens. Problems are raised as
``AcademicError`` with a message that is fit to show to the person.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.models import (
    ClassMember,
    Course,
    CourseOption,
    Professor,
    ProfessorClass,
    ProfessorCourse,
    Student,
    Task,
)

MAX_YEARS = 6
YEAR_NAMES = ("First", "Second", "Third", "Fourth", "Fifth", "Sixth")
KINDS = ("division", "batch")
# The kind of programme, and how many years each usually runs.
LEVELS: dict[str, tuple[str, int]] = {
    "BACHELORS": ("Bachelor's", 3),
    "MASTERS": ("Master's", 2),
    "ENGINEERING": ("Engineering", 4),
}
DEPARTMENTS = (
    "Management",
    "Commerce",
    "Science",
    "Computer Application",
    "Information Technology",
    "Computer Science",
    "Data Science",
    "Arts & Humanities",
    "Law",
    "Other",
)


class AcademicError(ValueError):
    """A request that can't be carried out, worded for the person who made it."""


def year_label(year: int | None) -> str:
    if year is None:
        return ""
    return f"{YEAR_NAMES[year - 1]} Year" if 1 <= year <= len(YEAR_NAMES) else f"Year {year}"


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def course_label(course: Course) -> str:
    """Name with its ID code, the way courses are shown everywhere: ``Data Science (CRS-004)``."""
    return f"{course.name} ({course.code})" if course.code else course.name


def _course_dict(course: Course) -> dict[str, Any]:
    return {
        "id": course.id,
        "name": course.name,
        "code": course.code,
        "label": course_label(course),
        "level": course.level,
        "level_label": LEVELS.get(course.level, (course.level, 0))[0],
        "department": course.department,
        "years": course.years,
        "year_options": [{"value": y, "label": year_label(y)} for y in range(1, course.years + 1)],
        "divisions": [{"id": o.id, "name": o.name} for o in course.options if o.kind == "division"],
        "batches": [{"id": o.id, "name": o.name} for o in course.options if o.kind == "batch"],
    }


# -- the lists the administrator maintains --------------------------------------------------


def list_courses(
    session_factory: sessionmaker[OrmSession], *, only_ids: set[int] | None = None
) -> list[dict[str, Any]]:
    with session_factory() as db:
        courses = db.query(Course).order_by(Course.name).all()
        return [_course_dict(c) for c in courses if only_ids is None or c.id in only_ids]


def list_courses_with_staff(session_factory: sessionmaker[OrmSession]) -> list[dict[str, Any]]:
    """For the admin panel: each course with its student count and assigned professors."""
    with session_factory() as db:
        counts: dict[int, int] = {
            int(course_id): int(total)
            for course_id, total in db.query(Student.course_id, func.count(Student.id))
            .filter(Student.course_id.is_not(None))
            .group_by(Student.course_id)
            .all()
        }
        teaching: dict[int, list[dict[str, Any]]] = {}
        for link, professor in (
            db.query(ProfessorCourse, Professor)
            .join(Professor, Professor.id == ProfessorCourse.professor_id)
            .order_by(Professor.display_name)
            .all()
        ):
            teaching.setdefault(link.course_id, []).append(
                {"id": professor.id, "name": professor.display_name}
            )
        rows = []
        for course in db.query(Course).order_by(Course.name).all():
            row = _course_dict(course)
            row["students"] = counts.get(course.id, 0)
            row["professors"] = teaching.get(course.id, [])
            rows.append(row)
        return rows


def _check_years(years: Any) -> int:
    try:
        number = int(years)
    except (TypeError, ValueError):
        raise AcademicError("Years must be a number.") from None
    if not 1 <= number <= MAX_YEARS:
        raise AcademicError(f"A course runs for 1 to {MAX_YEARS} years.")
    return number


def _name_taken(db: OrmSession, name: str, except_id: int | None = None) -> bool:
    query = db.query(Course).filter(func.lower(Course.name) == name.lower())
    if except_id is not None:
        query = query.filter(Course.id != except_id)
    return query.first() is not None


def _check_level(level: Any) -> str:
    if level not in LEVELS:
        raise AcademicError("Choose Bachelor's, Master's or Engineering.")
    return str(level)


def _check_department(department: Any) -> str:
    clean = _clean(department)
    if clean is None:
        raise AcademicError("Choose the department.")
    return clean[:100]


def add_course(
    session_factory: sessionmaker[OrmSession],
    name: str,
    years: Any,
    level: Any = "BACHELORS",
    department: Any = "Other",
) -> int:
    clean = _clean(name)
    if clean is None:
        raise AcademicError("Enter a course name.")
    number = _check_years(years)
    kind = _check_level(level)
    dept = _check_department(department)
    with session_factory() as db:
        if _name_taken(db, clean):
            raise AcademicError(f"There is already a course called {clean}.")
        course = Course(name=clean, years=number, level=kind, department=dept)
        db.add(course)
        db.flush()
        course.code = f"CRS-{course.id:03d}"
        db.commit()
        return course.id


def update_course(
    session_factory: sessionmaker[OrmSession],
    course_id: int,
    name: str,
    years: Any,
    level: Any = None,
    department: Any = None,
) -> None:
    clean = _clean(name)
    if clean is None:
        raise AcademicError("Enter a course name.")
    number = _check_years(years)
    with session_factory() as db:
        course = db.get(Course, course_id)
        if course is None:
            raise AcademicError("No such course.")
        if _name_taken(db, clean, except_id=course_id):
            raise AcademicError(f"There is already a course called {clean}.")
        too_far = (
            db.query(Student).filter(Student.course_id == course_id, Student.year > number).count()
        )
        if too_far:
            raise AcademicError(
                f"{too_far} student(s) are in a year beyond {number}. Move them first."
            )
        course.name, course.years = clean, number
        if level is not None:
            course.level = _check_level(level)
        if department is not None:
            course.department = _check_department(department)
        for task in db.query(Task).filter(Task.course_id == course_id):
            task.course = course_label(course)
        db.commit()


def delete_course(session_factory: sessionmaker[OrmSession], course_id: int) -> None:
    with session_factory() as db:
        course = db.get(Course, course_id)
        if course is None:
            raise AcademicError("No such course.")
        students = db.query(Student).filter_by(course_id=course_id).count()
        labs = db.query(Task).filter_by(course_id=course_id).count()
        if students or labs:
            raise AcademicError(
                f"{course.name} is still used by {students} student(s) and {labs} lab(s). "
                "Move or remove them first."
            )
        db.query(ProfessorCourse).filter_by(course_id=course_id).delete()
        db.query(ProfessorClass).filter_by(course_id=course_id).delete()
        db.delete(course)
        db.commit()


def add_option(
    session_factory: sessionmaker[OrmSession], course_id: int, kind: str, name: str
) -> int:
    clean = _clean(name)
    if kind not in KINDS:
        raise AcademicError("Unknown kind of option.")
    if clean is None:
        raise AcademicError(f"Enter a {kind} name.")
    with session_factory() as db:
        if db.get(Course, course_id) is None:
            raise AcademicError("No such course.")
        existing = (
            db.query(CourseOption)
            .filter(
                CourseOption.course_id == course_id,
                CourseOption.kind == kind,
                func.lower(CourseOption.name) == clean.lower(),
            )
            .first()
        )
        if existing is not None:
            raise AcademicError(f"{clean} is already in the list.")
        option = CourseOption(course_id=course_id, kind=kind, name=clean)
        db.add(option)
        db.commit()
        return option.id


def remove_option(session_factory: sessionmaker[OrmSession], option_id: int) -> None:
    with session_factory() as db:
        option = db.get(CourseOption, option_id)
        if option is None:
            raise AcademicError("No such option.")
        column = Student.division if option.kind == "division" else Student.batch
        task_column = Task.division if option.kind == "division" else Task.batch
        in_use = (
            db.query(Student)
            .filter(Student.course_id == option.course_id, column == option.name)
            .count()
            + db.query(Task)
            .filter(Task.course_id == option.course_id, task_column == option.name)
            .count()
        )
        if in_use:
            raise AcademicError(
                f"{option.name} is used by {in_use} student(s) or lab(s). Change them first."
            )
        db.delete(option)
        db.commit()


# -- which courses a professor teaches ------------------------------------------------------


def professor_course_ids(session_factory: sessionmaker[OrmSession], professor_id: int) -> set[int]:
    with session_factory() as db:
        return {
            course_id
            for (course_id,) in db.query(ProfessorCourse.course_id).filter_by(
                professor_id=professor_id
            )
        }


def set_professor_courses(
    session_factory: sessionmaker[OrmSession],
    professor_id: int,
    course_ids: list[int],
    *,
    adopt: bool = True,
) -> dict[str, int]:
    """Say which courses a professor teaches.

    Teaching a course means taking its students into the professor's class: every
    student of a newly added course who has no class yet joins at once, and so does
    anyone who signs up for it later. (``adopt=False`` records the courses only.)
    Returns how many students were added.
    """
    with session_factory() as db:
        if db.get(Professor, professor_id) is None:
            raise AcademicError("No such professor.")
        wanted = {int(i) for i in course_ids}
        known = (
            {c for (c,) in db.query(Course.id).filter(Course.id.in_(wanted))} if wanted else set()
        )
        if known != wanted:
            raise AcademicError("One of those courses doesn't exist.")
        before = {
            c for (c,) in db.query(ProfessorCourse.course_id).filter_by(professor_id=professor_id)
        }
        db.query(ProfessorCourse).filter_by(professor_id=professor_id).delete()
        for course_id in wanted:
            db.add(ProfessorCourse(professor_id=professor_id, course_id=course_id))
        # A group in a course they no longer teach stops adding students.
        for group in db.query(ProfessorClass).filter_by(professor_id=professor_id).all():
            if group.course_id not in wanted:
                db.delete(group)
        db.commit()
    added = 0
    if adopt:
        for course_id in sorted(wanted - before):
            added += add_group(
                session_factory, professor_id, {"course_id": course_id}, every_course=True
            )["added"]
    return {"added_students": added}


def catalog_for_professor(
    session_factory: sessionmaker[OrmSession], professor_id: int
) -> list[dict[str, Any]]:
    """Every course, marked with whether this professor teaches it (for the picker)."""
    mine = professor_course_ids(session_factory, professor_id)
    counts = {c["id"]: c for c in list_courses_with_staff(session_factory)}
    return [
        {**course, "teaching": course["id"] in mine, "students": counts[course["id"]]["students"]}
        for course in list_courses(session_factory)
    ]


def course_roster(
    session_factory: sessionmaker[OrmSession], course_id: int
) -> list[dict[str, Any]]:
    """The students of one course and whose class each is in (for the admin panel)."""
    with session_factory() as db:
        names = {p.id: p.display_name for p in db.query(Professor)}
        links: dict[int, list[str]] = {}
        for link in db.query(ClassMember):
            links.setdefault(link.student_id, []).append(names.get(link.professor_id, "?"))
        rows = (
            db.query(Student)
            .filter(Student.course_id == course_id, Student.email.is_not(None))
            .order_by(Student.year, Student.division, Student.batch, Student.display_name)
            .all()
        )
        return [
            {
                "id": s.id,
                "name": s.display_name,
                "email": s.email,
                "year_label": year_label(s.year),
                "division": s.division,
                "batch": s.batch,
                "roll_number": s.roll_number,
                "teacher": ", ".join(sorted(links.get(s.id, []))) or None,
            }
            for s in rows
        ]


def courses_for_professor(
    session_factory: sessionmaker[OrmSession], professor_id: int, *, every_course: bool
) -> list[dict[str, Any]]:
    """The courses a professor can pick from (everything when there's no administrator)."""
    if every_course:
        return list_courses(session_factory)
    return list_courses(
        session_factory, only_ids=professor_course_ids(session_factory, professor_id)
    )


# -- a student's (or a lab's) place in a course ---------------------------------------------


def _resolve(db: OrmSession, raw: dict[str, Any] | None, *, student: bool) -> dict[str, Any]:
    """Check a place (course, year, division, batch; and the roll number for a student).

    A student must fill in everything their course offers. A lab target only
    needs the course; the rest narrow it down and may be left as "any".
    """
    raw = raw or {}
    try:
        course = db.get(Course, int(raw.get("course_id")))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        course = None
    if course is None:
        raise AcademicError("Choose a course.")

    year: int | None = None
    if raw.get("year") not in (None, "", 0):
        try:
            year = int(raw["year"])
        except (TypeError, ValueError):
            raise AcademicError("Choose a year.") from None
        if not 1 <= year <= course.years:
            raise AcademicError(f"{course.name} has {course.years} years.")
    elif student:
        raise AcademicError("Choose your year.")

    values: dict[str, str | None] = {}
    for kind in KINDS:
        allowed = {o.name for o in course.options if o.kind == kind}
        chosen = _clean(raw.get(kind))
        if chosen is not None and chosen not in allowed:
            raise AcademicError(f"Choose a {kind} from the list.")
        if student and allowed and chosen is None:
            raise AcademicError(f"Choose your {kind}.")
        values[kind] = chosen

    roll = _clean(raw.get("roll_number"))
    if student and roll is None:
        raise AcademicError("Enter your roll number.")
    return {
        "course_id": course.id,
        "course_name": course_label(course),
        "year": year,
        "division": values["division"],
        "batch": values["batch"],
        "roll_number": roll,
    }


def signup_cohort(
    session_factory: sessionmaker[OrmSession], raw: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The student's place, checked; ``None`` when no courses are set up (nothing to ask)."""
    with session_factory() as db:
        if db.query(Course).count() == 0:
            return None
        return _resolve(db, raw, student=True)


def lab_target(
    session_factory: sessionmaker[OrmSession], raw: dict[str, Any] | None
) -> dict[str, Any]:
    with session_factory() as db:
        return _resolve(db, raw, student=False)


def describe(student: Student) -> dict[str, Any]:
    """A student's place, ready to show."""
    return {
        "course": student.course.name if student.course else None,
        "course_label": course_label(student.course) if student.course else None,
        "year": student.year,
        "year_label": year_label(student.year),
        "division": student.division,
        "batch": student.batch,
        "roll_number": student.roll_number,
    }


def lab_visible_to(student: Student, task: Task) -> bool:
    """Whether ``task`` is meant for ``student``."""
    if task.course_id is not None:
        return (
            student.course_id == task.course_id
            and (task.year is None or student.year == task.year)
            and (task.division is None or student.division == task.division)
            and (task.batch is None or student.batch == task.batch)
        )
    if task.professor_id is not None:
        return any(link.professor_id == task.professor_id for link in student.class_links)
    return True


# -- a professor taking a group into their class --------------------------------------------


def _matching(
    db: OrmSession,
    course_id: int,
    year: int | None,
    division: str | None,
    batch: str | None,
) -> Any:
    query = db.query(Student).filter(Student.course_id == course_id, Student.email.is_not(None))
    if year is not None:
        query = query.filter(Student.year == year)
    if division is not None:
        query = query.filter(Student.division == division)
    if batch is not None:
        query = query.filter(Student.batch == batch)
    return query


def _group_label(course: Course, year: int | None, division: str | None, batch: str | None) -> str:
    parts = [course_label(course), year_label(year) if year else "all years"]
    parts.append(f"Div {division}" if division else "all divisions")
    parts.append(f"Batch {batch}" if batch else "all batches")
    return " · ".join(parts)


def _teaches(db: OrmSession, professor_id: int, course_id: int) -> bool:
    return db.get(ProfessorCourse, (professor_id, course_id)) is not None


def preview_group(
    session_factory: sessionmaker[OrmSession],
    professor_id: int,
    raw: dict[str, Any],
    *,
    every_course: bool,
) -> dict[str, int]:
    with session_factory() as db:
        target = _resolve(db, raw, student=False)
        if not every_course and not _teaches(db, professor_id, target["course_id"]):
            raise AcademicError("You haven't been assigned that course. Ask your administrator.")
        students = _matching(
            db, target["course_id"], target["year"], target["division"], target["batch"]
        ).all()
        mine = {
            link.student_id for link in db.query(ClassMember).filter_by(professor_id=professor_id)
        }
        return {
            "matching": len(students),
            "to_add": sum(1 for s in students if s.id not in mine),
            "already_yours": sum(1 for s in students if s.id in mine),
        }


def add_group(
    session_factory: sessionmaker[OrmSession],
    professor_id: int,
    raw: dict[str, Any],
    *,
    every_course: bool,
) -> dict[str, Any]:
    """Take a group into the class: remember it, and add everyone in it who has no class yet."""
    with session_factory() as db:
        target = _resolve(db, raw, student=False)
        if not every_course and not _teaches(db, professor_id, target["course_id"]):
            raise AcademicError("You haven't been assigned that course. Ask your administrator.")
        rule = (
            db.query(ProfessorClass)
            .filter_by(
                professor_id=professor_id,
                course_id=target["course_id"],
                year=target["year"],
                division=target["division"],
                batch=target["batch"],
            )
            .first()
        )
        if rule is None:
            db.add(
                ProfessorClass(
                    professor_id=professor_id,
                    course_id=target["course_id"],
                    year=target["year"],
                    division=target["division"],
                    batch=target["batch"],
                )
            )
        mine = {
            link.student_id for link in db.query(ClassMember).filter_by(professor_id=professor_id)
        }
        added = 0
        for student in _matching(
            db, target["course_id"], target["year"], target["division"], target["batch"]
        ):
            if student.id not in mine:
                db.add(ClassMember(student_id=student.id, professor_id=professor_id))
                added += 1
        db.commit()
    return {"added": added}


def professor_groups(
    session_factory: sessionmaker[OrmSession], professor_id: int
) -> list[dict[str, Any]]:
    with session_factory() as db:
        rows = []
        for rule in (
            db.query(ProfessorClass)
            .filter_by(professor_id=professor_id)
            .order_by(ProfessorClass.id)
        ):
            course = db.get(Course, rule.course_id)
            if course is None:
                continue
            rows.append(
                {
                    "id": rule.id,
                    "label": _group_label(course, rule.year, rule.division, rule.batch),
                    "students": _matching(db, rule.course_id, rule.year, rule.division, rule.batch)
                    .join(ClassMember, ClassMember.student_id == Student.id)
                    .filter(ClassMember.professor_id == professor_id)
                    .count(),
                }
            )
        return rows


def remove_group(
    session_factory: sessionmaker[OrmSession], professor_id: int, rule_id: int
) -> None:
    """Stop a group from adding new students (those already in the class stay)."""
    with session_factory() as db:
        rule = db.get(ProfessorClass, rule_id)
        if rule is None or rule.professor_id != professor_id:
            raise AcademicError("That group isn't yours.")
        db.delete(rule)
        db.commit()


def assign_by_groups(session_factory: sessionmaker[OrmSession], student_id: int) -> None:
    """Put a student in the class of every professor with a group they belong to."""
    with session_factory() as db:
        student = db.get(Student, student_id)
        if student is None or student.course_id is None:
            return
        rules = (
            db.query(ProfessorClass)
            .join(Professor, Professor.id == ProfessorClass.professor_id)
            .filter(ProfessorClass.course_id == student.course_id, Professor.disabled.is_(False))
            .all()
        )
        have = {link.professor_id for link in student.class_links}
        for rule in rules:
            if (
                rule.professor_id not in have
                and (rule.year is None or rule.year == student.year)
                and (rule.division is None or rule.division == student.division)
                and (rule.batch is None or rule.batch == student.batch)
            ):
                db.add(ClassMember(student_id=student_id, professor_id=rule.professor_id))
                have.add(rule.professor_id)
        db.commit()


def set_student_cohort(
    session_factory: sessionmaker[OrmSession], student_id: int, raw: dict[str, Any]
) -> None:
    """Admin: change where a student sits (all of it, checked against the course)."""
    with session_factory() as db:
        student = db.get(Student, student_id)
        if student is None:
            raise AcademicError("No such student.")
        if _clean(raw.get("course_id")) is None:
            student.course_id = student.year = student.division = student.batch = None
            student.roll_number = _clean(raw.get("roll_number"))
        else:
            place = _resolve(db, raw, student=True)
            student.course_id = place["course_id"]
            student.year = place["year"]
            student.division = place["division"]
            student.batch = place["batch"]
            student.roll_number = place["roll_number"]
        db.commit()
    assign_by_groups(session_factory, student_id)
