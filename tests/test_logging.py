"""Structured logging: JSON lines in a rotating file, and no secrets in them."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from eaal_platform import logging_setup
from eaal_platform.logging_setup import JsonFormatter, configure_logging, get_logger


def _fresh() -> logging.Logger:
    logger = logging.getLogger(logging_setup.LOG_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    if hasattr(logger, "_cavy_configured"):
        del logger._cavy_configured  # type: ignore[attr-defined]
    return logger


def test_lines_are_json_with_the_extra_fields(tmp_path: Path) -> None:
    _fresh()
    logger = configure_logging(tmp_path / "logs")
    get_logger("eaal_platform.demo").warning("lab window left", extra={"session": 7, "count": 1})
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("eaal_platform.demo").exception("it broke")
    for handler in logger.handlers:
        handler.flush()
    lines = (tmp_path / "logs" / "cavy.log").read_text(encoding="utf-8").splitlines()
    first, second = (json.loads(line) for line in lines[-2:])
    assert first["level"] == "WARNING" and first["logger"] == "cavy.demo"
    assert (first["message"], first["session"], first["count"]) == ("lab window left", 7, 1)
    assert "time" in first
    assert second["level"] == "ERROR" and "boom" in second["error"]
    _fresh()


def test_configuring_twice_does_not_double_the_lines(tmp_path: Path) -> None:
    _fresh()
    configure_logging(tmp_path)
    logger = configure_logging(tmp_path)
    files = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(files) == 1
    _fresh()


def test_an_unwritable_folder_does_not_stop_the_app(tmp_path: Path) -> None:
    _fresh()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    configure_logging(blocker / "logs")  # a folder can't be made under a file
    get_logger("eaal_platform.demo").info("still fine")
    _fresh()


def test_the_formatter_handles_objects_it_cannot_serialise() -> None:
    record = logging.LogRecord("cavy.x", logging.INFO, "f.py", 1, "hello", (), None)
    record.thing = object()  # type: ignore[attr-defined]
    assert json.loads(JsonFormatter().format(record))["message"] == "hello"


def test_a_failed_call_is_logged_and_answered_politely(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from eaal_platform.db.bootstrap import create_student_account, seed_demo_content
    from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db
    from eaal_platform.events.logger import EventLogger
    from eaal_platform.server.app import ServerState, create_app

    engine = create_db_engine(tmp_path / "s.db")
    init_db(engine)
    factory = create_session_factory(engine)
    seed_demo_content(factory)
    create_student_account(factory, display_name="S", email="s@x.com", password="hunter2-hunter2")
    state = ServerState(engine, factory, EventLogger(factory))
    with TestClient(create_app(state)) as http:
        token = http.post(
            "/api/login",
            json={"role": "student", "email": "s@x.com", "password": "hunter2-hunter2"},
        ).json()["token"]
        client = state.get_client(token)
        assert client is not None

        def explode(*_: object) -> None:
            raise RuntimeError("secret internal detail")

        client.api.get_labs = explode  # type: ignore[method-assign]
        reply = http.post(
            "/api/call/get_labs", json={"args": []}, headers={"Authorization": f"Bearer {token}"}
        )
    assert reply.status_code == 500
    assert "secret internal detail" not in reply.text  # the person sees a plain message
    engine.dispose()
