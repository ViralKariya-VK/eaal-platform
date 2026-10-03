"""Tests for the sandboxed code executor: success, timeout, and
resource-limit cases."""

from __future__ import annotations

import sys
from pathlib import Path

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


# -- packaged-app mode ---------------------------------------------------------------------


def test_launcher_runs_a_script_like_plain_python(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = tmp_path / "main.py"
    script.write_text("import helper\nprint(helper.VALUE * 2)\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("VALUE = 21\n", encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-m", "eaal_platform.launcher", "--cavy-run-script", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.stdout.strip() == "42"
    assert done.returncode == 0


def test_launcher_error_output_starts_at_the_students_file(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = tmp_path / "main.py"
    script.write_text("def f():\n    return 1 / 0\nf()\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "eaal_platform.launcher", "--cavy-run-script", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 1
    assert "ZeroDivisionError" in done.stderr
    assert "runpy" not in done.stderr
    assert "launcher.py" not in done.stderr
    assert "main.py" in done.stderr


def test_launcher_passes_through_exit_codes(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = tmp_path / "main.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "eaal_platform.launcher", "--cavy-run-script", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 3


def test_frozen_apps_run_student_code_through_themselves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eaal_platform.sandbox import executor

    monkeypatch.setattr(executor, "_IS_FROZEN", True)
    script = Path("x") / "main.py"  # separators differ between Windows and Mac/Linux
    command = executor._command_for(script)
    assert command[1:] == ["--cavy-run-script", str(script)]
    monkeypatch.setattr(executor, "_IS_FROZEN", False)
    assert executor._command_for(script)[1:] == [str(script)]
