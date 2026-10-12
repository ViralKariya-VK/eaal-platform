"""Subjects, and which professor teaches which subject to which division and batch.

Only an administrator allots these. An allotment puts every matching student (right programme,
currently in the subject's semester, in that division and batch) into the professor's class, and
students who reach that semester later join it too: ``sync`` runs after every change and after
the yearly promotion. It never removes anyone; a professor can remove a student from the class.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db import academic_calendar as cal
from eaal_platform.db.models import (
    ClassMember,
    Course,
    Professor,
    ProfessorCourse,
    Student,
    Subject,
    TeachingAssignment,
)

# The subjects the college has given us so far (semester numbers count from admission).
MSC_DS_SUBJECTS: tuple[tuple[int, str], ...] = (
    (1, "Supervised Machine Learning"),
    (1, "Mathematics for Data Science"),
    (2, "Unsupervised Machine Learning"),
    (2, "Analysis and Forecasting of Time Series"),
    (3, "Deep Learning & GenAI"),
    (3, "Natural Language Processing"),
)


class TeachingError(ValueError):
    """The request can't be done; the message is shown to the administrator."""


def load_default_subjects(session_factory: sessionmaker[OrmSession]) -> int:
    """Add the known MSc Data Science subjects (idempotent); returns how many were added."""
    added = 0
    with session_factory() as db:
        course = db.query(Course).filter(Course.name == "MSc Data Science").first()
        if course is None:
            return 0
        for semester, name in MSC_DS_SUBJECTS:
            exists = (
                db.query(Subject)
                .filter_by(course_id=course.id, semester=semester, name=name)
                .first()
            )
            if exists is None:
                db.add(Subject(course_id=course.id, semester=semester, name=name))
                added += 1
        db.commit()
    return added


def add_subject(
    session_factory: sessionmaker[OrmSession], course_id: int, semester: int, name: str
) -> int:
    clean = " ".join(name.split())
    with session_factory() as db:
        course = db.get(Course, course_id)
        if course is None:
            raise TeachingError("No such course.")
        if not clean:
            raise TeachingError("Give the subject a name.")
        if not 1 <= semester <= course.years * 2:
            raise TeachingError(f"{course.name} has semesters 1 to {course.years * 2}.")
        if db.query(Subject).filter_by(course_id=course_id, semester=semester, name=clean).first():
            raise TeachingError("That subject already exists.")
        subject = Subject(course_id=course_id, semester=semester, name=clean)
        db.add(subject)
        db.commit()
        return subject.id


def remove_subject(session_factory: sessionmaker[OrmSession], subject_id: int) -> None:
    with session_factory() as db:
        if db.query(TeachingAssignment).filter_by(subject_id=subject_id).first():
            raise TeachingError("Remove the professors' assignments for this subject first.")
        subject = db.get(Subject, subject_id)
        if subject is not None:
            db.delete(subject)
            db.commit()


def assign(
    session_factory: sessionmaker[OrmSession],
    professor_id: int,
    subject_id: int,
    division: str,
    batch: str | None,
) -> dict[str, int]:
    """Let a professor teach a subject to a division (one batch, or ``None`` for both)."""
    with session_factory() as db:
        if db.get(Professor, professor_id) is None:
            raise TeachingError("No such professor.")
        subject = db.get(Subject, subject_id)
        if subject is None:
            raise TeachingError("No such subject.")
        if not division:
            raise TeachingError("Choose a division.")
        existing = (
            db.query(TeachingAssignment)
            .filter_by(
                professor_id=professor_id, subject_id=subject_id, division=division, batch=batch
            )
            .first()
        )
        if existing is None:
            db.add(
                TeachingAssignment(
                    professor_id=professor_id,
                    subject_id=subject_id,
                    division=division,
                    batch=batch or None,
                )
            )
        if (
            db.query(ProfessorCourse)
            .filter_by(professor_id=professor_id, course_id=subject.course_id)
            .first()
            is None
        ):
            db.add(ProfessorCourse(professor_id=professor_id, course_id=subject.course_id))
        db.commit()
    return {"added_students": sync(session_factory)}


def unassign(session_factory: sessionmaker[OrmSession], assignment_id: int) -> None:
    with session_factory() as db:
        row = db.get(TeachingAssignment, assignment_id)
        if row is not None:
            db.delete(row)
            db.commit()


def sync(session_factory: sessionmaker[OrmSession]) -> int:
    """Put every student an assignment covers into that professor's class; returns how many."""
    added = 0
    with session_factory() as db:
        for row in db.query(TeachingAssignment).all():
            subject = db.get(Subject, row.subject_id)
            if subject is None:
                continue
            members = {
                s
                for (s,) in db.query(ClassMember.student_id).filter_by(
                    professor_id=row.professor_id
                )
            }
            query = db.query(Student).filter(
                Student.course_id == subject.course_id,
                Student.division == row.division,
                Student.password_hash.is_not(None),
            )
            if row.batch:
                query = query.filter(Student.batch == row.batch)
            for student in query:
                if student.id in members or not _in_semester(student, subject.semester):
                    continue
                db.add(ClassMember(student_id=student.id, professor_id=row.professor_id))
                members.add(student.id)
                added += 1
        db.commit()
    return added


def _in_semester(student: Student, semester: int) -> bool:
    if not student.year:
        return False
    return cal.semester(student.year, date.today()) == semester


def overview(session_factory: sessionmaker[OrmSession]) -> dict[str, Any]:
    """Everything the admin's Teaching page shows."""
    with session_factory() as db:
        courses = {c.id: c for c in db.query(Course)}
        subjects = db.query(Subject).order_by(Subject.course_id, Subject.semester, Subject.name)
        professors = {p.id: p.display_name for p in db.query(Professor)}
        options: dict[int, dict[str, list[str]]] = {
            c.id: {
                "division": [o.name for o in c.options if o.kind == "division"],
                "batch": [o.name for o in c.options if o.kind == "batch"],
            }
            for c in courses.values()
        }
        assignments = [
            {
                "id": a.id,
                "professor_id": a.professor_id,
                "professor": professors.get(a.professor_id, "?"),
                "subject_id": a.subject_id,
                "division": a.division,
                "batch": a.batch,
            }
            for a in db.query(TeachingAssignment).order_by(TeachingAssignment.id)
        ]
        return {
            "subjects": [
                {
                    "id": s.id,
                    "course_id": s.course_id,
                    "course": courses[s.course_id].name,
                    "semester": s.semester,
                    "name": s.name,
                    "divisions": options[s.course_id]["division"],
                    "batches": options[s.course_id]["batch"],
                }
                for s in subjects
            ],
            "professors": [
                {"id": i, "name": n} for i, n in sorted(professors.items(), key=lambda x: x[1])
            ],
            "assignments": assignments,
        }


def academic_year_summary(
    session_factory: sessionmaker[OrmSession], today: date | None = None
) -> dict[str, Any]:
    """Where the college is in the academic year, and how many students sit in each year.

    Students move up by themselves on 1 July (``academics.sync_student_years``); this page only
    shows the result and the date of the next move.
    """
    on = today or date.today()
    start = cal.academic_year(on)
    with session_factory() as db:
        courses = {c.id: c.name for c in db.query(Course)}
        counts: dict[tuple[int, int], int] = {}
        for student in db.query(Student).filter(Student.course_id.is_not(None)):
            key = (student.course_id or 0, student.year or 0)
            counts[key] = counts.get(key, 0) + 1
    rows = [
        {
            "course": courses.get(course_id, "?"),
            "year": year or "Completed",
            "semester": cal.semester(year, on) if year else None,
            "students": count,
        }
        for (course_id, year), count in sorted(counts.items())
    ]
    return {
        "academic_year": cal.academic_year_label(start),
        "next_promotion": date(start + 1, *cal.YEAR_STARTS_ON).isoformat(),
        "rows": rows,
    }
