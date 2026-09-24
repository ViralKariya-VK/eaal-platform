"""Tests for the background/batched EventLogger."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.models import Event, EventType, Session, Student, Task
from eaal_platform.events.logger import EventLogger, PendingEvent


@pytest.fixture
def seeded_session_id(db_session: OrmSession) -> int:
    student = Student(display_name="Ada")
    task = Task(title="Loop")
    db_session.add_all([student, task])
    db_session.flush()
    session = Session(student_id=student.id, task_id=task.id)
    db_session.add(session)
    db_session.commit()
    return session.id


@pytest.fixture
def logger(db_session_factory: sessionmaker[OrmSession]) -> Iterator[EventLogger]:
    log = EventLogger(db_session_factory, batch_size=5, flush_interval=0.1)
    log.start()
    yield log
    log.stop()


def test_log_returns_immediately(logger: EventLogger, seeded_session_id: int) -> None:
    start = time.perf_counter()
    for _ in range(50):
        logger.log(PendingEvent(session_id=seeded_session_id, event_type=EventType.CODE_EDIT))
    elapsed = time.perf_counter() - start
    # Enqueuing 50 events must not involve any DB I/O on the calling thread;
    # this should take microseconds, not the milliseconds a synchronous
    # commit-per-event would cost.
    assert elapsed < 0.05


def test_events_persisted_after_stop(
    logger: EventLogger, seeded_session_id: int, db_session_factory: sessionmaker[OrmSession]
) -> None:
    for _ in range(3):
        logger.log(PendingEvent(session_id=seeded_session_id, event_type=EventType.CODE_RUN))
    logger.stop()

    with db_session_factory() as session:
        rows = session.query(Event).filter_by(session_id=seeded_session_id).all()
    assert len(rows) == 3
    assert all(row.event_type == EventType.CODE_RUN for row in rows)


def test_flush_triggered_by_batch_size(
    logger: EventLogger, seeded_session_id: int, db_session_factory: sessionmaker[OrmSession]
) -> None:
    for _ in range(5):  # exactly batch_size
        logger.log(PendingEvent(session_id=seeded_session_id, event_type=EventType.CODE_EDIT))

    deadline = time.perf_counter() + 2.0
    while time.perf_counter() < deadline:
        with db_session_factory() as session:
            count = session.query(Event).filter_by(session_id=seeded_session_id).count()
        if count >= 5:
            break
        time.sleep(0.01)

    with db_session_factory() as session:
        count = session.query(Event).filter_by(session_id=seeded_session_id).count()
    assert count == 5


def test_flush_triggered_by_interval(
    logger: EventLogger, seeded_session_id: int, db_session_factory: sessionmaker[OrmSession]
) -> None:
    logger.log(PendingEvent(session_id=seeded_session_id, event_type=EventType.TASK_START))

    deadline = time.perf_counter() + 2.0
    while time.perf_counter() < deadline:
        with db_session_factory() as session:
            count = session.query(Event).filter_by(session_id=seeded_session_id).count()
        if count >= 1:
            break
        time.sleep(0.01)

    with db_session_factory() as session:
        count = session.query(Event).filter_by(session_id=seeded_session_id).count()
    assert count == 1


def test_payload_json_round_trips(
    logger: EventLogger, seeded_session_id: int, db_session_factory: sessionmaker[OrmSession]
) -> None:
    logger.log(
        PendingEvent(
            session_id=seeded_session_id,
            event_type=EventType.EXECUTION_RESULT,
            payload_json={"duration_ms": 12, "exit_status": 0},
        )
    )
    logger.stop()

    with db_session_factory() as session:
        row = session.query(Event).filter_by(session_id=seeded_session_id).one()
    assert row.payload_json == {"duration_ms": 12, "exit_status": 0}


def test_stats_reflect_enqueued_and_flushed_counts(
    logger: EventLogger, seeded_session_id: int
) -> None:
    for _ in range(4):
        logger.log(PendingEvent(session_id=seeded_session_id, event_type=EventType.CODE_EDIT))
    logger.stop()

    stats = logger.stats
    assert stats.enqueued == 4
    assert stats.flushed == 4


def test_stop_is_safe_to_call_multiple_times(logger: EventLogger, seeded_session_id: int) -> None:
    logger.log(PendingEvent(session_id=seeded_session_id, event_type=EventType.TASK_START))
    logger.stop()
    logger.stop()  # must not raise
