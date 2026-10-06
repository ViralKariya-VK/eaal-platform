"""What a professor sees of students' work, and the AI help with writing problems."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.ai.provider import AIProvider, GenerationContext, GenerationResult, Purpose
from eaal_platform.api.bridge import CavyApi
from eaal_platform.db.bootstrap import (
    create_professor_account,
    create_student_account,
    seed_demo_content,
)
from eaal_platform.events.logger import EventLogger

Factory = sessionmaker[OrmSession]
_PW = "hunter2-hunter2"


class _Writer(AIProvider):
    def __init__(self, text: str = "Write a function that reverses a list.", ok: bool = True):
        self.text, self.ok, self.prompts = text, ok, []  # type: ignore[var-annotated]

    def generate(
        self, prompt: str, context: GenerationContext, purpose: Purpose
    ) -> GenerationResult:
        self.prompts.append(prompt)
        return GenerationResult(text=self.text if self.ok else "", available=self.ok, error="down")

    def ping(self) -> bool:
        return self.ok

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str | None:
        return "m"


def _people(factory: Factory, ai: AIProvider | None = None, server: bool = False):
    seed_demo_content(factory)
    create_professor_account(factory, display_name="Dr", email="p@x.com", password=_PW)
    create_student_account(factory, display_name="Sam", email="s@x.com", password=_PW)
    logger = EventLogger(factory, batch_size=1, flush_interval=0.1)
    logger.start()
    student = CavyApi(factory, logger, ai_provider=ai, restrict_signup=server)
    professor = CavyApi(factory, logger, ai_provider=ai, restrict_signup=server)
    student.login("student", "s@x.com", _PW)
    professor.login("professor", "p@x.com", _PW)
    return student, professor, logger


def _do_lab(student: CavyApi) -> list[int]:
    stages = student.get_stages(student.get_labs()[0]["id"])["stages"]
    ids = []
    for index, stage in enumerate(stages):
        session_id = student.start_stage(stage["id"])["session_id"]
        ids.append(session_id)
        code = f"print('stage {index}')"
        student.log_code_edit(session_id, {"main.py": code}, "main.py", False, True)
        student.record_run(
            session_id,
            student.prepare_run(session_id, {"main.py": code})["snapshot_id"],
            "main.py",
            {"stdout": f"stage {index}\n", "stderr": "", "exit_status": 0},
        )
    student.submit_session(ids[-1], {"main.py": "print('final answer')"})
    return ids


def test_the_professor_sees_each_stages_final_code_and_output(db_session_factory: Factory) -> None:
    student, professor, logger = _people(db_session_factory)
    _do_lab(student)
    task_id = student.get_labs()[0]["id"]
    student_id = professor.get_students() or None
    row = professor.get_lab_report(task_id)["rows"][0]
    assert row["student_id"] and row["status"] == "Submitted"

    work = professor.get_student_submission(task_id, row["student_id"])
    assert [s["stage_type"] for s in work["stages"]] == ["LEARNING", "EXPLORATION", "ASSESSMENT"]
    assert work["stages"][0]["files"]["main.py"] == "print('stage 0')"
    assert work["stages"][2]["files"]["main.py"] == "print('final answer')"
    assert work["stages"][0]["output"]["stdout"] == "stage 0\n"
    assert all(stage["submitted"] for stage in work["stages"])
    assert work["student"]["name"] == "Sam"
    assert student_id is None  # nobody is in the professor's class yet
    logger.stop()


def test_a_followup_can_be_edited_and_its_work_seen(db_session_factory: Factory) -> None:
    student, professor, logger = _people(db_session_factory)
    task_id = student.get_labs()[0]["id"]
    created = professor.create_followup_assessment(
        {
            "source_task_id": task_id,
            "kind": "TRANSFER",
            "title": "Transfer",
            "description": "old",
            "ai_assistance_mode": "RESTRICTED",
            "duration_minutes": 20,
        }
    )["task_id"]
    listed = professor.get_followup_assessments(task_id)[0]
    assert listed["duration_minutes"] == 20 and listed["mode_locked"] is False

    edited = professor.update_followup_assessment(
        created,
        {
            "title": "Transfer v2",
            "description": "new",
            "ai_assistance_mode": "NONE",
            "duration_minutes": 30,
        },
    )
    assert edited == {"ok": True}
    listed = professor.get_followup_assessments(task_id)[0]
    assert (listed["title"], listed["description"], listed["ai_assistance_mode"]) == (
        "Transfer v2",
        "new",
        "NONE",
    )

    stage_id = listed["stage_id"]
    session_id = student.start_stage(stage_id)["session_id"]
    student.submit_session(session_id, {"main.py": "print('transfer answer')"})
    row = professor.get_lab_report(created)["rows"][0]
    assert row["status"] == "Submitted"
    work = professor.get_student_submission(created, row["student_id"])
    assert work["stages"][0]["files"]["main.py"] == "print('transfer answer')"
    assert professor.get_followup_assessments(task_id)[0]["submitted_count"] == 1

    locked = professor.update_followup_assessment(
        created, {"title": "x", "ai_assistance_mode": "FULL", "duration_minutes": 30}
    )
    assert locked["ok"] is False and "already started" in locked["error"]
    kept = professor.update_followup_assessment(
        created, {"title": "Renamed", "ai_assistance_mode": "NONE", "duration_minutes": 10}
    )
    assert kept["ok"] is True
    assert professor.update_followup_assessment(task_id, {"title": "lab"})["ok"] is False
    assert professor.update_followup_assessment(created, {"title": " "})["ok"] is False
    logger.stop()


def test_the_ai_drafts_a_problem_statement(db_session_factory: Factory) -> None:
    writer = _Writer()
    _, professor, logger = _people(db_session_factory, writer)
    result = professor.draft_problem_description(
        {"title": "Reverse a list", "topic": "Lists", "difficulty": "Easy", "notes": "no slicing"}
    )
    assert result == {"ok": True, "text": "Write a function that reverses a list."}
    prompt = writer.prompts[0]
    assert "Reverse a list" in prompt and "Lists" in prompt and "no slicing" in prompt
    assert professor.draft_problem_description({})["ok"] is False

    writer.ok = False
    down = professor.draft_problem_description({"title": "x"})
    assert down["ok"] is False and down["error"]
    logger.stop()


def test_students_cannot_use_the_professor_tools(db_session_factory: Factory) -> None:
    student, _, logger = _people(db_session_factory)
    for call in (
        lambda: student.draft_problem_description({"title": "x"}),
        lambda: student.get_student_submission(1, 1),
        lambda: student.get_class_overview(),
        lambda: student.get_student_progress(1),
        lambda: student.update_followup_assessment(1, {"title": "x"}),
    ):
        with pytest.raises(ValueError, match="professor"):
            call()
    logger.stop()


def test_class_overview_and_progress_follow_the_class(db_session_factory: Factory) -> None:
    student, professor, logger = _people(db_session_factory, server=True)
    _do_lab(student)
    logger.flush()
    assert professor.get_class_overview() == []
    with pytest.raises(ValueError, match="isn't in your class"):
        professor.get_student_progress(1)

    professor.add_students_to_class([1])  # (not limited to courses: none exist here)
    overview = professor.get_class_overview()
    assert overview[0]["name"] == "Sam" and overview[0]["sessions_submitted"] == 3
    progress = professor.get_student_progress(1)
    assert progress["student"]["name"] == "Sam" and progress["totals"]["sessions"] == 3
    work = professor.get_session_submission(progress["history"][0]["session_id"])
    assert work["stages"][0]["files"]
    logger.stop()
