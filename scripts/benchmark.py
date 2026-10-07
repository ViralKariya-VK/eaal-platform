"""Measure how the classroom server copes with a class working at once.

Starts a throwaway server (its own database, its own port), signs in N students
and has each one do a typical lab (open it, type, run, move between stages,
submit, look at their progress), all at the same time. Prints latency per kind of
call (median, 95th percentile, slowest), overall throughput, how fast the event
log writes, and how long running one piece of student code takes.

    python scripts/benchmark.py            # 30 students
    python scripts/benchmark.py --students 60 --markdown
"""

from __future__ import annotations

import argparse
import platform
import socket
import statistics
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from eaal_platform.db.bootstrap import create_student_account
from eaal_platform.db.models import EventType
from eaal_platform.events.logger import EventLogger, PendingEvent
from eaal_platform.sandbox.executor import run_code
from eaal_platform.server.host import ServerHost, build_state

PASSWORD = "benchmark-pass-1"
EDITS_PER_STAGE = 15


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Student(threading.Thread):
    def __init__(
        self, base: str, index: int, timings: dict[str, list[float]], lock: threading.Lock
    ):
        super().__init__(daemon=True)
        self.base, self.index, self.timings, self.lock = base, index, timings, lock
        self.errors = 0
        self.http = httpx.Client(timeout=60)
        self.token = ""

    def _timed(self, label: str, work: Any) -> Any:
        start = time.perf_counter()
        try:
            return work()
        except Exception as exc:
            self.errors += 1
            if self.errors == 1:
                print(f"  student {self.index}: {label} failed: {exc!r}"[:200])
            return None
        finally:
            with self.lock:
                self.timings[label].append((time.perf_counter() - start) * 1000)

    def call(self, method: str, *args: Any) -> Any:
        def go() -> Any:
            reply = self.http.post(
                f"{self.base}/api/call/{method}",
                json={"args": list(args)},
                headers={"Authorization": f"Bearer {self.token}"},
            )
            reply.raise_for_status()
            return reply.json()["result"]

        return self._timed(method, go)

    def run(self) -> None:
        def login() -> None:
            reply = self.http.post(
                f"{self.base}/api/login",
                json={"role": "student", "email": f"b{self.index}@bench.edu", "password": PASSWORD},
            )
            reply.raise_for_status()
            self.token = reply.json()["token"]

        self._timed("login", login)
        labs = self.call("get_labs")
        if not labs:
            return
        stages = self.call("get_stages", labs[0]["id"])["stages"]
        sessions = []
        for stage in stages:
            info = self.call("start_stage", stage["id"])
            if info is None:
                return
            sessions.append(info["session_id"])
            code = "total = 0\n"
            for step in range(EDITS_PER_STAGE):
                code += f"total += {step}\n"
                self.call(
                    "log_code_edit", info["session_id"], {"main.py": code}, "main.py", False, False
                )
            prepared = self.call("prepare_run", info["session_id"], {"main.py": code}, "main.py")
            if prepared:
                self.call(
                    "record_run",
                    info["session_id"],
                    prepared["snapshot_id"],
                    "main.py",
                    {"stdout": "ok\n", "stderr": "", "exit_status": 0, "duration_ms": 30},
                )
        self.call("submit_session", sessions[-1], {"main.py": "print('done')"})
        self.call("get_my_progress")


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _event_log_speed(count: int = 5000) -> float:
    with tempfile.TemporaryDirectory() as folder:
        state = build_state(Path(folder) / "events.db")
        from eaal_platform.db.bootstrap import start_practice_session

        student = create_student_account(
            state.session_factory, display_name="E", email="e@bench.edu", password=PASSWORD
        )
        session_id = start_practice_session(state.session_factory, student)
        logger = EventLogger(state.session_factory)
        logger.start()
        start = time.perf_counter()
        for i in range(count):
            logger.log(
                PendingEvent(
                    session_id=session_id, event_type=EventType.CODE_EDIT, payload_json={"i": i}
                )
            )
        logger.stop()  # waits until everything is written
        elapsed = time.perf_counter() - start
        state.engine.dispose()
        return count / elapsed


def _sandbox_speed(runs: int = 10) -> list[float]:
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        outcome = run_code({"main.py": "print(sum(range(1000)))"})
        assert outcome.exit_status == 0
        times.append((time.perf_counter() - start) * 1000)
    return times


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--students", type=int, default=30)
    parser.add_argument("--markdown", action="store_true", help="print the tables as Markdown")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as folder:
        db_path = Path(folder) / "bench.db"
        host = ServerHost(lambda: build_state(db_path))
        port = _free_port()
        host.start(port)
        state_factory = build_state(db_path)  # a second connection, to create the accounts
        for i in range(args.students):
            create_student_account(
                state_factory.session_factory,
                display_name=f"Student {i}",
                email=f"b{i}@bench.edu",
                password=PASSWORD,
            )
        state_factory.engine.dispose()

        timings: dict[str, list[float]] = defaultdict(list)
        lock = threading.Lock()
        base = f"http://127.0.0.1:{port}"
        students = [Student(base, i, timings, lock) for i in range(args.students)]
        started = time.perf_counter()
        for student in students:
            student.start()
        for student in students:
            student.join()
        elapsed = time.perf_counter() - started
        host.stop()

    total_calls = sum(len(v) for v in timings.values())
    errors = sum(s.errors for s in students)
    rows = [
        (
            name,
            len(values),
            statistics.median(values),
            _percentile(values, 0.95),
            max(values),
        )
        for name, values in sorted(timings.items())
    ]
    events_per_second = _event_log_speed()
    sandbox = _sandbox_speed()

    lines = [
        f"Machine: {platform.platform()}, Python {platform.python_version()}, {platform.machine()}",
        f"{args.students} students at once: {total_calls} calls in {elapsed:.1f} s "
        f"= {total_calls / elapsed:.0f} calls/s, {errors} failed",
        "",
        "| Call | Count | Median ms | 95th % ms | Slowest ms |",
        "|---|---|---|---|---|",
        *[f"| {n} | {c} | {m:.1f} | {p:.1f} | {x:.1f} |" for n, c, m, p, x in rows],
        "",
        f"Event log: {events_per_second:.0f} events/s written",
        f"Running student code: median {statistics.median(sandbox):.0f} ms, "
        f"slowest {max(sandbox):.0f} ms (10 runs, each in its own process)",
    ]
    print(
        "\n".join(lines) if args.markdown else "\n".join(line.replace("|", " ") for line in lines)
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
