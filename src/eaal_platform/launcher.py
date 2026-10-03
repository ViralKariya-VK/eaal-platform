"""Entry point for the packaged app (and ``python -m eaal_platform.launcher``).

A packaged CAVY has no separate Python to run student code with: its own
executable *is* the interpreter. So the sandbox starts a second copy of the
app with ``--cavy-run-script <file>``, which lands here, runs that one file
exactly as ``python <file>`` would, and exits, without ever loading the
window code. ``--cavy-serve`` runs the classroom server without a window;
``--cavy-version`` prints which build this is.
Everything else starts the normal app.
"""

from __future__ import annotations

import os
import runpy
import sys
import traceback

RUN_SCRIPT_FLAG = "--cavy-run-script"
SERVE_FLAG = "--cavy-serve"
VERSION_FLAG = "--cavy-version"


def run_student_script(path: str) -> int:
    """Run ``path`` as ``__main__``, printing errors the way plain ``python`` would."""
    sys.argv = [path]
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            return int(exc.code or 0)
        print(exc.code, file=sys.stderr)
        return 1
    except BaseException as exc:
        # Hide our own frames (runpy, this module) so the traceback starts at their file.
        tb = exc.__traceback__
        while tb is not None and os.path.abspath(tb.tb_frame.f_code.co_filename) != os.path.abspath(
            path
        ):
            tb = tb.tb_next
        traceback.print_exception(type(exc), exc, tb or exc.__traceback__)
        return 1
    return 0


def _ensure_std_streams() -> None:
    """A windowed (no-console) build starts with ``sys.stdout``/``sys.stderr`` as None.

    uvicorn's logging setup calls ``sys.stderr.isatty()``, which then crashes, so
    the server never starts. Point missing streams at the null device instead.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def main() -> int:
    _ensure_std_streams()
    if len(sys.argv) >= 2 and sys.argv[1] == VERSION_FLAG:
        from eaal_platform.buildinfo import describe

        print(describe())
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == RUN_SCRIPT_FLAG:
        return run_student_script(sys.argv[2])
    if len(sys.argv) >= 2 and sys.argv[1] == SERVE_FLAG:
        # Headless server (no window): `CAVY --cavy-serve [--port N] [--db FILE]`.
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from eaal_platform.server.__main__ import main as run_server

        return run_server()
    from eaal_platform.app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
