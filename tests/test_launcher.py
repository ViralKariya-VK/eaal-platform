"""The packaged app's entry point."""

from __future__ import annotations

import sys

import pytest

from eaal_platform import launcher


def test_missing_output_streams_are_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A windowed Windows build starts with sys.stdout/sys.stderr set to None.

    uvicorn's logging setup then crashes on ``sys.stderr.isatty()``, so the
    hosted server never started. The launcher must give the app real streams.
    """
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    launcher._ensure_std_streams()

    assert sys.stdout is not None
    assert sys.stderr is not None
    assert sys.stderr.isatty() is False  # the call that used to crash
    sys.stdout.write("anything is fine")  # and writing must not fail
    sys.stdout.close()
    sys.stderr.close()


def test_existing_streams_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    before = (sys.stdout, sys.stderr)
    launcher._ensure_std_streams()
    assert (sys.stdout, sys.stderr) == before


def test_the_version_flag_reports_the_build(capsys: pytest.CaptureFixture[str]) -> None:
    from eaal_platform import buildinfo

    assert launcher.main.__module__ == "eaal_platform.launcher"
    old = sys.argv
    sys.argv = ["CAVY", launcher.VERSION_FLAG]
    try:
        assert launcher.main() == 0
    finally:
        sys.argv = old
    out = capsys.readouterr().out.strip()
    assert out == buildinfo.describe()
    assert out.startswith("CAVY 0.1.0 (commit ")
