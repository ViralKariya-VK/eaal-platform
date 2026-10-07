"""Keeping students in the lab: the key lock, full screen, and counting departures."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.api.bridge import CavyApi, _paste_comes_from_ai
from eaal_platform.client.api import ClientApi
from eaal_platform.client.lockdown import (
    LLKHF_ALTDOWN,
    VK_ESCAPE,
    VK_F4,
    VK_LWIN,
    VK_TAB,
    LabLock,
    should_block,
)
from eaal_platform.db.bootstrap import create_student_account, seed_demo_content
from eaal_platform.db.models import AIInteraction, Event, EventType
from eaal_platform.db.models import Session as SessionModel
from eaal_platform.events.logger import EventLogger

Factory = sessionmaker[OrmSession]
_PW = "hunter2-hunter2"


# -- the key lock ---------------------------------------------------------------------------


def test_which_keys_are_blocked() -> None:
    assert should_block(VK_TAB, LLKHF_ALTDOWN, False, False)  # Alt+Tab
    assert should_block(VK_ESCAPE, LLKHF_ALTDOWN, False, False)  # Alt+Esc
    assert should_block(VK_F4, LLKHF_ALTDOWN, False, False)  # Alt+F4
    assert should_block(VK_LWIN, 0, False, False)  # the Windows key
    assert should_block(VK_ESCAPE, 0, True, False)  # Ctrl+Esc (Start menu)
    assert not should_block(VK_ESCAPE, 0, True, True)  # Ctrl+Shift+Esc stays: the way out
    assert not should_block(VK_TAB, 0, False, False)  # plain Tab (indenting code)
    assert not should_block(0x41, 0, False, False)  # a letter


class _Window:
    def __init__(self) -> None:
        self.toggles = 0

    def toggle_fullscreen(self) -> None:
        self.toggles += 1


class _Keys:
    def __init__(self, works: bool = True) -> None:
        self.on, self.works = False, works

    def engage(self) -> bool:
        self.on = self.works
        return self.works

    def release(self) -> None:
        self.on = False


def test_the_lab_lock_goes_full_screen_and_comes_back() -> None:
    window, keys = _Window(), _Keys()
    lock = LabLock(window=lambda: window, key_lock=keys, enabled=True, keys_delay=0.0)
    assert lock.engage() == {"fullscreen": True, "keys_blocked": True}
    assert lock.engage()["fullscreen"] is True  # stage to stage: no second toggle
    assert window.toggles == 1 and keys.on
    lock.release()
    assert window.toggles == 2 and not keys.on and not lock.engaged
    lock.release()  # harmless when already released
    assert window.toggles == 2


def _lock(
    window: _Window, keys: _Keys, delay: float = 0.0
) -> tuple[LabLock, list[Any], list[float]]:
    """A lab lock whose timers run at once and whose keyboard hand-backs are recorded."""
    handed_back: list[Any] = []
    waits: list[float] = []

    def now(seconds: float, work: Any) -> None:
        waits.append(seconds)
        work()

    lock = LabLock(
        window=lambda: window,
        key_lock=keys,
        enabled=True,
        schedule=now,
        refocus=handed_back.append,
        keys_delay=delay,
    )
    return lock, handed_back, waits


def test_the_keyboard_is_handed_back_to_the_page_after_full_screen() -> None:
    window, keys = _Window(), _Keys()
    lock, handed_back, _ = _lock(window, keys)
    lock.engage()
    assert handed_back and all(w is window for w in handed_back)  # typing must still work
    before = len(handed_back)
    lock.refocus()  # and again whenever the person clicks
    assert len(handed_back) == before + 1
    lock.release()
    assert len(handed_back) > before + 1  # and when full screen ends
    lock.refocus()  # nothing happens outside a lab
    assert handed_back[-1] is window


def test_the_mac_kiosk_waits_for_full_screen_to_finish() -> None:
    window, keys = _Window(), _Keys()
    lock, _, waits = _lock(window, keys, delay=1.5)
    assert lock.engage()["fullscreen"] is True
    assert window.toggles == 1 and keys.on  # (the timer ran at once here)
    assert 1.5 in waits  # the key lock was asked for after a pause, not together with full screen
    lock2, _, _ = _lock(_Window(), _Keys())
    lock2.engage()
    lock2.release()
    assert not lock2.engaged


def test_the_lab_lock_copes_with_no_window_or_keys() -> None:
    assert LabLock(
        window=lambda: None, key_lock=_Keys(False), enabled=True, keys_delay=0.0
    ).engage() == {
        "fullscreen": False,
        "keys_blocked": False,
    }
    off = LabLock(window=lambda: _Window(), key_lock=_Keys(), enabled=False)
    assert off.engage() == {"fullscreen": False, "keys_blocked": False} and not off.engaged


def test_the_app_api_engages_and_releases_the_lock(db_session_factory: Factory) -> None:
    window = _Window()
    lock = LabLock(window=lambda: window, key_lock=_Keys(), enabled=True)
    api = ClientApi(CavyApi(db_session_factory, EventLogger(db_session_factory)), lab_lock=lock)
    assert api.enter_lab_mode()["fullscreen"] is True
    api.logout()  # signing out always lets go
    assert not lock.engaged
    api.enter_lab_mode()
    api.shutdown()
    assert not lock.engaged


# -- counting departures --------------------------------------------------------------------


def _lab(factory: Factory) -> tuple[CavyApi, EventLogger, list[int]]:
    seed_demo_content(factory)
    create_student_account(factory, display_name="Sam", email="s@x.com", password=_PW)
    logger = EventLogger(factory, batch_size=1, flush_interval=0.1)
    logger.start()
    api = CavyApi(factory, logger)
    api.login("student", "s@x.com", _PW)
    stages = api.get_stages(api.get_labs()[0]["id"])["stages"]
    return api, logger, [api.start_stage(s["id"])["session_id"] for s in stages]


def test_the_first_departure_warns_and_the_second_ends_the_lab(db_session_factory: Factory) -> None:
    api, logger, ids = _lab(db_session_factory)
    first = api.record_focus_lost(ids[0])
    assert (first["count"], first["action"]) == (1, "warn")
    # Going to another stage does not give the warning back.
    second = api.record_focus_lost(ids[1])
    assert (second["count"], second["action"]) == (2, "submit")

    api.submit_session(ids[2], {"main.py": "x"}, "focus")
    logger.flush()
    with db_session_factory() as db:
        rows = [db.get(SessionModel, i) for i in ids]
        assert all(r and r.submitted_at and r.submit_reason == "focus" for r in rows)
        assert db.query(Event).filter_by(event_type=EventType.FOCUS_LOST).count() == 2
    lab = api.get_stages(api.get_labs()[0]["id"])
    assert "automatically" in lab["submit_note"]
    logger.stop()


def test_leaving_in_the_first_stage_ends_the_whole_lab(db_session_factory: Factory) -> None:
    seed_demo_content(db_session_factory)
    create_student_account(db_session_factory, display_name="Sam", email="s@x.com", password=_PW)
    logger = EventLogger(db_session_factory, batch_size=1, flush_interval=0.1)
    logger.start()
    api = CavyApi(db_session_factory, logger)
    api.login("student", "s@x.com", _PW)
    lab_id = api.get_labs()[0]["id"]
    stages = api.get_stages(lab_id)["stages"]
    first = api.start_stage(stages[0]["id"])["session_id"]
    api.record_focus_lost(first)
    api.record_focus_lost(first)
    api.submit_session(first, {"main.py": "x = 1"}, "focus")

    after = api.get_stages(lab_id)
    assert after["lab_submitted"] is True
    assert [s["status"] for s in after["stages"]] == ["submitted"] * 3
    with pytest.raises(ValueError, match="already submitted"):
        api.start_stage(stages[1]["id"])
    logger.stop()


def test_the_reason_is_not_believed_without_the_departures(db_session_factory: Factory) -> None:
    api, logger, ids = _lab(db_session_factory)
    api.record_focus_lost(ids[0])  # only one
    api.submit_session(ids[2], {"main.py": "x"}, "focus")
    with db_session_factory() as db:
        assert db.get(SessionModel, ids[2]).submit_reason is None  # type: ignore[union-attr]
    logger.stop()


def test_practice_is_never_locked(db_session_factory: Factory) -> None:
    seed_demo_content(db_session_factory)
    create_student_account(db_session_factory, display_name="Sam", email="s@x.com", password=_PW)
    api = CavyApi(db_session_factory, EventLogger(db_session_factory))
    api.login("student", "s@x.com", _PW)
    practice = api.start_practice()["session_id"]
    assert api.record_focus_lost(practice)["action"] == "none"


# -- paste detection ------------------------------------------------------------------------

_REPLY = "Try this:\n```python\nfor i in range(5):\n    print(i * 2)\n```\nIt loops five times."


def test_text_from_the_ai_is_recognised_even_with_different_spacing() -> None:
    replies = [_REPLY]
    assert _paste_comes_from_ai("for i in range(5):\n    print(i * 2)", replies)
    assert _paste_comes_from_ai("for i in range(5):\n\tprint(i * 2)", replies)  # tabs vs spaces
    assert _paste_comes_from_ai("for i in range(5):\n    print(i * 2)\nx = 1", replies)  # mostly
    assert not _paste_comes_from_ai("import os\nprint(os.name)", replies)
    assert not _paste_comes_from_ai("i", replies)  # too short to say
    assert not _paste_comes_from_ai("for i in range(5):", [])


def test_pastes_are_logged_with_their_source(db_session_factory: Factory) -> None:
    api, logger, ids = _lab(db_session_factory)
    with db_session_factory() as db:
        db.add(AIInteraction(session_id=ids[0], prompt="help", response=_REPLY, provider="x"))
        db.commit()
    assert api.record_paste(ids[0], "for i in range(5):\n    print(i * 2)") == {"source": "ai"}
    assert api.record_paste(ids[0], "totally_different_code()") == {"source": "other"}
    assert api.record_paste(ids[0], "   ") == {"source": "none"}
    logger.flush()
    api.submit_session(ids[2], {"main.py": "x"})

    professor_view = CavyApi(db_session_factory, logger)
    from eaal_platform.db.bootstrap import create_professor_account

    create_professor_account(db_session_factory, display_name="P", email="p@x.com", password=_PW)
    professor_view.login("professor", "p@x.com", _PW)
    student_id = professor_view.get_lab_report(api.get_labs()[0]["id"])["rows"][0]["student_id"]
    work = professor_view.get_student_submission(api.get_labs()[0]["id"], student_id)
    pastes = work["stages"][0]["pastes"]
    assert (pastes["ai"], pastes["other"]) == (1, 1)
    assert pastes["ai_chars"] > 0
    logger.stop()
    with pytest.raises(ValueError):
        api.record_paste(999, "x")


# -- the administrator's switch -------------------------------------------------------------


def test_lab_mode_can_be_switched_off_for_the_class(db_session_factory: Factory) -> None:
    from eaal_platform.db import app_settings

    api, logger, ids = _lab(db_session_factory)
    assert api.get_lab_policy() == {"lab_mode": True}
    lock = LabLock(window=lambda: _Window(), key_lock=_Keys(), enabled=True, keys_delay=0.0)
    app = ClientApi(api, lab_lock=lock)

    app_settings.set_flag(db_session_factory, app_settings.LAB_MODE, False)
    assert api.get_lab_policy() == {"lab_mode": False}
    assert app.enter_lab_mode() == {
        "ok": True,
        "fullscreen": False,
        "keys_blocked": False,
        "disabled": True,
    }
    assert not lock.engaged  # nothing was locked
    assert api.record_focus_lost(ids[0])["action"] == "none"  # and leaving the window is free

    app_settings.set_flag(db_session_factory, app_settings.LAB_MODE, True)
    assert app.enter_lab_mode()["fullscreen"] is True and lock.engaged
    assert api.record_focus_lost(ids[0])["action"] == "warn"
    logger.stop()


def test_the_admin_panel_has_a_lab_mode_switch(db_session_factory: Factory, db_engine: Any) -> None:
    from fastapi.testclient import TestClient

    from eaal_platform.server.app import ServerState, create_app

    state = ServerState(db_engine, db_session_factory, EventLogger(db_session_factory))
    with TestClient(create_app(state)) as http:
        token = http.post(
            "/admin/api/setup", json={"name": "R", "email": "r@x.com", "password": _PW}
        ).json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert http.get("/admin/api/lab-mode", headers=auth).json() == {"enabled": True}
        assert http.post("/admin/api/lab-mode", json={"enabled": False}, headers=auth).json() == {
            "enabled": False
        }
        assert http.get("/admin/api/lab-mode", headers=auth).json() == {"enabled": False}
        assert http.get("/admin/api/lab-mode").status_code == 401
