"""Async, batched event logger.

The editor pane calls ``log()`` on every keystroke-debounced edit and every
run. If that write went straight to SQLite on the calling (UI) thread, the
editor would stutter under normal typing load — a single fsync-backed
commit can take several milliseconds, and Qt's UI thread has no budget to
spare per keystroke. So ``log()`` only ever pushes onto an in-memory queue
and returns immediately; a dedicated background thread drains the queue and
performs the actual (batched) SQLite writes.

Flushing happens whichever comes first: the queue reaches ``batch_size``, or
``flush_interval`` seconds have elapsed since the last flush. This bounds
both memory growth (a burst of edits doesn't grow the queue forever) and
staleness (a quiet period still gets written promptly instead of sitting in
memory indefinitely, which matters since the events table is the single
source of truth and a crash before flush would lose that history).
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.models import Event, EventType


@dataclass(frozen=True, slots=True)
class PendingEvent:
    """An event queued for persistence, before it has a database identity."""

    session_id: int
    event_type: EventType
    payload_json: dict[str, Any] | None = None
    code_version_id: int | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class _Stats:
    """Internal counters, exposed read-only for tests and diagnostics."""

    enqueued: int = 0
    flushed: int = 0
    flush_calls: int = 0


class EventLogger:
    """Background-thread, queue-backed writer for the append-only events table.

    Usage:
        logger = EventLogger(session_factory)
        logger.start()
        logger.log(PendingEvent(session_id=1, event_type=EventType.CODE_EDIT))
        ...
        logger.stop()  # flushes remaining events and joins the thread
    """

    def __init__(
        self,
        session_factory: sessionmaker[OrmSession],
        *,
        batch_size: int = 25,
        flush_interval: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._flush_interval = flush_interval

        self._queue: queue.Queue[PendingEvent | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stats = _Stats()
        self._stats_lock = threading.Lock()

    def start(self) -> None:
        """Start the background flush thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="eaal-event-logger", daemon=True)
        self._thread.start()

    def log(self, pending: PendingEvent) -> None:
        """Enqueue an event. Never blocks on I/O — safe to call from the UI thread."""
        self._queue.put(pending)
        with self._stats_lock:
            self._stats.enqueued += 1

    def stop(self, *, timeout: float | None = 5.0) -> None:
        """Signal the background thread to flush and exit, then join it."""
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def stats(self) -> _Stats:
        """A snapshot-safe read of internal counters, for tests/diagnostics."""
        with self._stats_lock:
            return _Stats(
                enqueued=self._stats.enqueued,
                flushed=self._stats.flushed,
                flush_calls=self._stats.flush_calls,
            )

    def _run(self) -> None:
        batch: list[PendingEvent] = []
        while True:
            try:
                item = self._queue.get(timeout=self._flush_interval)
            except queue.Empty:
                self._flush(batch)
                batch = []
                continue

            if item is None:
                self._flush(batch)
                return

            batch.append(item)
            if len(batch) >= self._batch_size:
                self._flush(batch)
                batch = []

    def _flush(self, batch: list[PendingEvent]) -> None:
        if not batch:
            return
        with self._session_factory() as session:
            for pending in batch:
                kwargs: dict[str, Any] = {
                    "session_id": pending.session_id,
                    "event_type": pending.event_type,
                    "payload_json": pending.payload_json,
                    "code_version_id": pending.code_version_id,
                }
                if pending.timestamp is not None:
                    kwargs["timestamp"] = pending.timestamp
                session.add(Event(**kwargs))
            session.commit()
        with self._stats_lock:
            self._stats.flushed += len(batch)
            self._stats.flush_calls += 1
