"""Tests for the sandboxed code executor: success, timeout, and
resource-limit cases."""

from __future__ import annotations

import sys

import pytest

from eaal_platform.sandbox.executor import run_code

pytestmark = pytest.mark.slow


def _files(source: str) -> dict[str, str]:
    return {"main.py": source}


def test_successful_run_captures_stdout() -> None:
    outcome = run_code(_files("print('hello from sandbox')"))
    assert outcome.exit_status == 0
    assert "hello from sandbox" in outcome.stdout
    assert outcome.stderr == ""
    assert outcome.timed_out is False
    assert outcome.duration_ms >= 0


def test_run_captures_stderr_and_nonzero_exit() -> None:
    outcome = run_code(_files("raise ValueError('boom')"))
    assert outcome.exit_status == 1
    assert "ValueError" in outcome.stderr
    assert "boom" in outcome.stderr
    assert outcome.timed_out is False


def test_entry_file_can_import_a_sibling_file() -> None:
    outcome = run_code(
        {
            "main.py": "from helper import double\nprint(double(21))",
            "helper.py": "def double(n):\n    return n * 2",
        }
    )
    assert outcome.exit_status == 0
    assert "42" in outcome.stdout


def test_unknown_entry_filename_raises() -> None:
    with pytest.raises(ValueError, match="entry_filename"):
        run_code({"main.py": "print(1)"}, entry_filename="missing.py")


def test_infinite_loop_times_out() -> None:
    outcome = run_code(_files("while True:\n    pass\n"), timeout_seconds=0.5)
    assert outcome.timed_out is True
    assert outcome.exit_status is None


def test_duration_reflects_actual_elapsed_time() -> None:
    outcome = run_code(_files("import time; time.sleep(0.2)"), timeout_seconds=5.0)
    assert outcome.timed_out is False
    assert outcome.duration_ms >= 150  # allow scheduling slack


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="RLIMIT_AS is only applied on Linux (see executor.py module docstring)",
)
def test_memory_limit_kills_excessive_allocation() -> None:
    hog = "x = bytearray(1024 * 1024 * 1024)"  # 1 GiB, over the 256 MiB default
    outcome = run_code(_files(hog), timeout_seconds=5.0)
    # Killed by MemoryError (caught, non-zero exit) or by the OS (negative
    # returncode from a signal) — either way it must not silently succeed.
    assert outcome.timed_out is False
    assert outcome.exit_status != 0


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_CPU is POSIX-only")
def test_cpu_time_limit_kills_busy_loop_within_wall_timeout() -> None:
    # A tight busy loop with a generous wall-clock timeout but a tiny CPU
    # budget should be killed by the CPU limit, not the wall clock.
    outcome = run_code(
        _files("n = 0\nwhile True:\n    n += 1"),
        timeout_seconds=10.0,
        cpu_time_seconds=1,
    )
    assert outcome.timed_out is False
    assert outcome.exit_status != 0


def test_output_is_truncated_beyond_limit() -> None:
    outcome = run_code(_files("print('x' * 50_000)"))
    assert len(outcome.stdout) < 50_000
    assert "truncated" in outcome.stdout
