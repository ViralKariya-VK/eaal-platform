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
