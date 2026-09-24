"""Sandboxed execution of student-submitted Python code.

Every run happens in a *fresh* subprocess — never in-process — because
student code is untrusted by definition (this is the whole point of the
platform: students write arbitrary code). A fresh subprocess means a crash,
an infinite loop, or a memory blowout in student code can never take down
the host application; it only kills the subprocess, which we're already
prepared to reap on timeout.

Resource limits are applied inside the child right before ``exec`` via
``preexec_fn``, but the two limits have different platform reach:

- ``RLIMIT_CPU`` (CPU-time) works on both Linux and macOS and is applied on
  both.
- ``RLIMIT_AS`` (address-space/memory) is applied on **Linux only**. On
  macOS, ``setrlimit(RLIMIT_AS, ...)`` fails for essentially any limit a
  student script would need (confirmed experimentally: it fails even at
  4 GiB) because the dynamic linker's shared-library mappings alone already
  reserve more *virtual* address space than that before user code ever
  runs — this is a platform characteristic of macOS's dyld shared cache,
  not a bug in this module, and there is no practical workaround short of
  a real OS-level sandbox (e.g. a container or `sandbox-exec` profile).
  Do not "fix" this by raising the limit until it stops failing; that
  defeats the point of the limit.
- Windows has no ``resource`` module at all, so neither limit applies
  there. Wall-clock timeout is the only containment on Windows today; real
  memory/CPU containment needs Job Objects (via ``pywin32`` or ctypes),
  which is deferred to a later hardening phase rather than adding a
  half-working shim now that would give a false sense of safety.

Wall-clock ``timeout_seconds`` is therefore the one limit that is fully
reliable on every platform, and callers should treat it as the primary
defense — the other two are best-effort, platform-dependent hardening on
top of it.
"""

from __future__ import annotations

import subprocess  # nosec B404 -- this module's entire job is running a subprocess safely
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

_IS_POSIX = sys.platform != "win32"
_SUPPORTS_MEMORY_LIMIT = sys.platform == "linux"

_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024  # 256 MiB
_DEFAULT_CPU_TIME_SECONDS = 5
_MAX_CAPTURED_OUTPUT_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Structured result of one sandboxed run."""

    stdout: str
    stderr: str
    exit_status: int | None
    duration_ms: int
    timed_out: bool


def _truncate(text: str, limit: int = _MAX_CAPTURED_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


def _posix_preexec(memory_limit_bytes: int, cpu_time_seconds: int) -> None:
    """Runs inside the child process, after fork, before exec.

    Import ``resource`` here (not at module scope) so this module still
    imports cleanly on Windows — the function is simply never called there.
    """
    import resource

    if _SUPPORTS_MEMORY_LIMIT:
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_time_seconds, cpu_time_seconds))


def run_code(
    files: dict[str, str],
    *,
    entry_filename: str = "main.py",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    memory_limit_bytes: int = _DEFAULT_MEMORY_LIMIT_BYTES,
    cpu_time_seconds: int = _DEFAULT_CPU_TIME_SECONDS,
) -> ExecutionOutcome:
    """Execute ``entry_filename`` from ``files`` in a sandboxed subprocess.

    Every file in ``files`` is written into the same fresh temp directory,
    so ``entry_filename`` can ``import`` its siblings the ordinary way —
    Python already puts a script's own directory on ``sys.path`` when run
    as ``python entry.py``, so no extra path wiring is needed here.

    The wall-clock ``timeout_seconds`` applies on every platform. The
    memory and CPU-time limits apply only on POSIX; see module docstring.
    """
    if entry_filename not in files:
        raise ValueError(f"entry_filename {entry_filename!r} is not in files")

    with tempfile.TemporaryDirectory(prefix="eaal-sandbox-") as tmp_dir:
        for filename, content in files.items():
            (Path(tmp_dir) / filename).write_text(content, encoding="utf-8")
        script_path = Path(tmp_dir) / entry_filename

        preexec_fn = None
        if _IS_POSIX:
            preexec_fn = lambda: _posix_preexec(  # noqa: E731
                memory_limit_bytes, cpu_time_seconds
            )

        start = monotonic()
        timed_out = False
        try:
            # nosec B603: no shell=True, argv is a fixed two-element list, and
            # script_path is a file this function just wrote to a fresh temp
            # dir — not attacker-controlled. Running untrusted *contents* of
            # that file is the entire point of this module; that risk is
            # mitigated by the timeout/rlimits above and by the OS-level
            # process boundary, not by avoiding subprocess.
            completed = subprocess.run(  # nosec B603
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=tmp_dir,
                preexec_fn=preexec_fn,  # POSIX only; ignored/unset on Windows
            )
            stdout, stderr, exit_status = (
                completed.stdout,
                completed.stderr,
                completed.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            exit_status = None
        duration_ms = int((monotonic() - start) * 1000)

    return ExecutionOutcome(
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        exit_status=exit_status,
        duration_ms=duration_ms,
        timed_out=timed_out,
    )
