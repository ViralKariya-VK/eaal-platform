"""Tests for editing, archiving and exporting labs (professor tools), plus the
lab-list rules they depend on."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import create_professor_account, create_student_account
from eaal_platform.db.engine import create_db_engine, init_db
from eaal_platform.events.logger import EventLogger

_PASSWORD = "hunter2-hunter2"


def _lab_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Sorting Basics",
        "course": "B.Sc. Data Science",
        "division": "A",
        "batch": "2026",
        "topic": "Sorting",
        "description": "Sort a list",
        "difficulty": "Medium",
        "stages": [
            {"duration_minutes": 20, "ai_assistance_mode": "FULL"},
            {"duration_minutes": 20, "ai_assistance_mode": "FULL"},
            {"duration_minutes": 20, "ai_assistance_mode": "RESTRICTED"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def api(
    db_session_factory: sessionmaker[OrmSession], tmp_path: Path
) -> Iterator[tuple[CavyApi, dict[str, Any]]]:
    create_professor_account(
        db_session_factory, display_name="Dr. K", email="prof@example.com", password=_PASSWORD
    )
    create_student_account(
        db_session_factory,
        display_name="Asha",
        email="asha@example.com",
        password=_PASSWORD,
        enrollment_no="BSC1",
    )
    create_student_account(
        db_session_factory,
        display_name="=HYPERLINK(1)",
        email="evil@example.com",
        password=_PASSWORD,
    )
    logger = EventLogger(db_session_factory, batch_size=1, flush_interval=0.05)
    logger.start()
    dialog: dict[str, Any] = {"answer": str(tmp_path / "out.csv"), "asked": []}

    def fake_dialog(name: str) -> str | None:
        dialog["asked"].append(name)
        return dialog["answer"]

    cavy = CavyApi(db_session_factory, logger, save_file_dialog=fake_dialog)
    cavy.login("professor", "prof@example.com", _PASSWORD)
    yield cavy, dialog
    logger.stop()


def _as(cavy: CavyApi, role: str, email: str) -> None:
    assert cavy.login(role, email, _PASSWORD)["ok"] is True


def _create(cavy: CavyApi, **overrides: Any) -> int:
    return int(cavy.create_lab(_lab_payload(**overrides))["task_id"])


# -- editing ----------------------------------------------------------------------------


def test_get_lab_returns_form_payload(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)

    lab = cavy.get_lab(task_id)

    assert lab["title"] == "Sorting Basics"
    assert lab["topic"] == "Sorting"
    assert [s["stage_type"] for s in lab["stages"]] == ["LEARNING", "EXPLORATION", "ASSESSMENT"]
    assert [s["mode_locked"] for s in lab["stages"]] == [False, False, False]


def test_update_lab_saves_every_field(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    edited = _lab_payload(
        title="Sorting, Revised",
        course="B.Tech",
        division="B",
        batch="2027",
        topic="Merge sort",
        description="New text",
        difficulty="Hard",
        stages=[
            {"duration_minutes": 10, "ai_assistance_mode": "NONE"},
            {"duration_minutes": 30, "ai_assistance_mode": "RESTRICTED"},
            {"duration_minutes": 45, "ai_assistance_mode": "NONE"},
        ],
    )

    assert cavy.update_lab(task_id, edited) == {"ok": True}

    lab = cavy.get_lab(task_id)
    assert (lab["title"], lab["course"], lab["division"], lab["batch"]) == (
        "Sorting, Revised",
        "B.Tech",
        "B",
        "2027",
    )
    assert (lab["topic"], lab["description"], lab["difficulty"]) == (
        "Merge sort",
        "New text",
        "Hard",
    )
    assert [(s["duration_minutes"], s["ai_assistance_mode"]) for s in lab["stages"]] == [
        (10, "NONE"),
        (30, "RESTRICTED"),
        (45, "NONE"),
    ]


def test_update_lab_requires_a_title(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)

    result = cavy.update_lab(task_id, _lab_payload(title="   "))

    assert result == {"ok": False, "error": "Session Title is required."}
    assert cavy.get_lab(task_id)["title"] == "Sorting Basics"


def test_ai_mode_locks_once_a_student_starts_the_stage(
    api: tuple[CavyApi, dict[str, Any]],
) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    _as(cavy, "student", "asha@example.com")
    first_stage = cavy.get_stages(task_id)["stages"][0]["id"]
    cavy.start_stage(first_stage)
    _as(cavy, "professor", "prof@example.com")

    assert [s["mode_locked"] for s in cavy.get_lab(task_id)["stages"]] == [True, False, False]

    changed_mode = _lab_payload()
    changed_mode["stages"][0]["ai_assistance_mode"] = "NONE"
    result = cavy.update_lab(task_id, changed_mode)
    assert result["ok"] is False
    assert "already started" in result["error"]
    assert cavy.get_lab(task_id)["stages"][0]["ai_assistance_mode"] == "FULL"

    # Durations and text stay editable on a started stage; unchanged mode is fine.
    changed_duration = _lab_payload(title="Renamed")
    changed_duration["stages"][0]["duration_minutes"] = 99
    assert cavy.update_lab(task_id, changed_duration) == {"ok": True}
    assert cavy.get_lab(task_id)["stages"][0]["duration_minutes"] == 99


def test_a_rejected_edit_changes_nothing(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    _as(cavy, "student", "asha@example.com")
    cavy.start_stage(cavy.get_stages(task_id)["stages"][2]["id"])
    _as(cavy, "professor", "prof@example.com")

    bad = _lab_payload(title="Should not stick")
    bad["stages"][2]["ai_assistance_mode"] = "NONE"
    assert cavy.update_lab(task_id, bad)["ok"] is False

    assert cavy.get_lab(task_id)["title"] == "Sorting Basics"


def test_cohort_tags_propagate_to_followups(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    cavy.create_followup_assessment(
        {
            "source_task_id": task_id,
            "kind": "TRANSFER",
            "title": "Transfer",
            "description": "d",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 10,
        }
    )

    cavy.update_lab(task_id, _lab_payload(division="Z", batch="2030"))

    with cavy._session_factory() as db:
        rows = db.execute(
            text("select division, batch from tasks where linked_task_id is not null")
        )
        assert rows.fetchall() == [("Z", "2030")]


def test_followups_cannot_be_edited_directly(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    followup_id = cavy.create_followup_assessment(
        {
            "source_task_id": task_id,
            "kind": "RETENTION",
            "title": "R",
            "description": "d",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 10,
        }
    )["task_id"]

    with pytest.raises(ValueError, match="No lab"):
        cavy.get_lab(followup_id)
    result = cavy.update_lab(followup_id, _lab_payload(stages=[_lab_payload()["stages"][0]]))
    assert result["ok"] is False


# -- lists: follow-ups hidden ------------------------------------------------------------


def test_followups_are_not_listed_as_labs(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    cavy.create_followup_assessment(
        {
            "source_task_id": task_id,
            "kind": "TRANSFER",
            "title": "FOLLOWUP",
            "description": "d",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 10,
        }
    )

    assert [lab["title"] for lab in cavy.get_professor_labs()] == ["Sorting Basics"]
    assert cavy.get_professor_dashboard_summary()["total_labs"] == 1
    _as(cavy, "student", "asha@example.com")
    assert [lab["title"] for lab in cavy.get_labs()] == ["Sorting Basics"]
    # ...but students still reach it through the parent lab.
    assert [f["title"] for f in cavy.get_followup_assessments(task_id)] == ["FOLLOWUP"]


# -- archiving ---------------------------------------------------------------------------


def test_archived_lab_is_hidden_from_students_but_kept_for_professor(
    api: tuple[CavyApi, dict[str, Any]],
) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    other_id = _create(cavy, title="Other")

    assert cavy.archive_lab(task_id) == {"ok": True}

    assert [lab["title"] for lab in cavy.get_professor_labs()] == ["Other"]
    everything = cavy.get_professor_labs(include_archived=True)
    assert {lab["title"]: lab["archived"] for lab in everything} == {
        "Sorting Basics": True,
        "Other": False,
    }
    assert cavy.get_professor_dashboard_summary()["total_labs"] == 1
    _as(cavy, "student", "asha@example.com")
    assert [lab["id"] for lab in cavy.get_labs()] == [other_id]


def test_students_cannot_start_an_archived_lab(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    _as(cavy, "student", "asha@example.com")
    stage_id = cavy.get_stages(task_id)["stages"][0]["id"]
    _as(cavy, "professor", "prof@example.com")
    cavy.archive_lab(task_id)
    _as(cavy, "student", "asha@example.com")

    with pytest.raises(ValueError, match="archived"):
        cavy.start_stage(stage_id)


def test_archiving_keeps_student_history_and_reports(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    _as(cavy, "student", "asha@example.com")
    assessment = cavy.get_stages(task_id)["stages"][2]["id"]
    session_id = cavy.start_stage(assessment)["session_id"]
    cavy.submit_session(session_id, {"main.py": "print(1)"})
    _as(cavy, "professor", "prof@example.com")

    cavy.archive_lab(task_id)

    report = cavy.get_lab_report(task_id)
    assert report["submitted_count"] == 1


def test_archiving_a_lab_also_hides_its_followups(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    followup_id = cavy.create_followup_assessment(
        {
            "source_task_id": task_id,
            "kind": "TRANSFER",
            "title": "T",
            "description": "d",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 10,
        }
    )["task_id"]
    _as(cavy, "student", "asha@example.com")
    followup_stage = cavy.get_followup_assessments(task_id)[0]["stage_id"]
    _as(cavy, "professor", "prof@example.com")

    cavy.archive_lab(task_id)

    _as(cavy, "student", "asha@example.com")
    with pytest.raises(ValueError, match="archived"):
        cavy.start_stage(followup_stage)
    assert followup_id  # created


def test_unarchive_restores_the_lab(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    cavy.archive_lab(task_id)

    cavy.unarchive_lab(task_id)

    assert [lab["id"] for lab in cavy.get_professor_labs()] == [task_id]
    _as(cavy, "student", "asha@example.com")
    assert [lab["id"] for lab in cavy.get_labs()] == [task_id]


def test_lab_tools_require_a_professor(api: tuple[CavyApi, dict[str, Any]]) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    _as(cavy, "student", "asha@example.com")

    for call in (
        lambda: cavy.get_lab(task_id),
        lambda: cavy.update_lab(task_id, _lab_payload()),
        lambda: cavy.archive_lab(task_id),
        lambda: cavy.unarchive_lab(task_id),
        lambda: cavy.export_lab_report(task_id),
    ):
        with pytest.raises(ValueError, match="professor"):
            call()


# -- export ------------------------------------------------------------------------------


def test_export_writes_a_csv_to_the_chosen_path(
    api: tuple[CavyApi, dict[str, Any]], tmp_path: Path
) -> None:
    cavy, dialog = api
    task_id = _create(cavy, title="Sorting / Basics")
    _as(cavy, "student", "asha@example.com")
    session_id = cavy.start_stage(cavy.get_stages(task_id)["stages"][2]["id"])["session_id"]
    cavy.submit_session(session_id, {"main.py": "print(1)"})
    _as(cavy, "professor", "prof@example.com")

    result = cavy.export_lab_report(task_id)

    assert result == {"ok": True, "path": str(tmp_path / "out.csv")}
    assert dialog["asked"] == ["Sorting___Basics_report.csv"]
    with (tmp_path / "out.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["Student Name", "Enrolment No.", "Score", "Submitted On", "Status"]
    assert rows[1][0] == "Asha"
    assert rows[1][1] == "BSC1"
    assert rows[1][4] == "Submitted"


def test_export_neutralises_spreadsheet_formulas(
    api: tuple[CavyApi, dict[str, Any]], tmp_path: Path
) -> None:
    cavy, _ = api
    task_id = _create(cavy)
    _as(cavy, "student", "evil@example.com")
    session_id = cavy.start_stage(cavy.get_stages(task_id)["stages"][2]["id"])["session_id"]
    cavy.submit_session(session_id, {"main.py": "print(1)"})
    _as(cavy, "professor", "prof@example.com")

    cavy.export_lab_report(task_id)

    content = (tmp_path / "out.csv").read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK(1)" in content


def test_export_cancelled_writes_nothing(
    api: tuple[CavyApi, dict[str, Any]], tmp_path: Path
) -> None:
    cavy, dialog = api
    task_id = _create(cavy)
    dialog["answer"] = None

    assert cavy.export_lab_report(task_id) == {"ok": False, "cancelled": True}
    assert not (tmp_path / "out.csv").exists()


def test_export_without_a_dialog_reports_why(
    db_session_factory: sessionmaker[OrmSession],
) -> None:
    create_professor_account(
        db_session_factory, display_name="P", email="p@example.com", password=_PASSWORD
    )
    cavy = CavyApi(db_session_factory, EventLogger(db_session_factory))
    cavy.login("professor", "p@example.com", _PASSWORD)
    task_id = int(cavy.create_lab(_lab_payload())["task_id"])

    result = cavy.export_lab_report(task_id)

    assert result["ok"] is False
    assert "isn't available" in result["error"]


# -- migration ---------------------------------------------------------------------------


def test_init_db_upgrades_a_database_created_before_archiving(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    engine = create_db_engine(path)
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE tasks DROP COLUMN archived_at"))
        conn.execute(text("INSERT INTO tasks (title) VALUES ('Legacy lab')"))
    engine.dispose()

    upgraded = create_db_engine(path)
    init_db(upgraded)
    init_db(upgraded)  # running it twice must be harmless

    with upgraded.connect() as conn:
        assert conn.execute(text("select title, archived_at from tasks")).fetchall() == [
            ("Legacy lab", None)
        ]
    upgraded.dispose()
