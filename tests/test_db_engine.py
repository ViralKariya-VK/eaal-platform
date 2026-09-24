"""Tests for the default SQLite data path (``db/engine.py``).

Only the path *composition* is tested here — never actually creating a
database at a real per-OS user-data location from a test run. The engine
itself (WAL mode, table creation, ...) is already exercised indirectly by
every other test via the ``db_engine`` fixture, which always passes an
explicit ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eaal_platform.db.engine import default_db_path


def test_env_override_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EAAL_DB_PATH", "/tmp/somewhere/custom.db")
    assert default_db_path() == Path("/tmp/somewhere/custom.db")


def test_macos_default_is_under_application_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EAAL_DB_PATH", raising=False)
    monkeypatch.setattr("sys.platform", "darwin")
    path = default_db_path()
    assert path == Path.home() / "Library" / "Application Support" / "CAVY" / "eaal.db"


def test_windows_default_uses_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EAAL_DB_PATH", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", "C:\\Users\\student\\AppData\\Roaming")
    path = default_db_path()
    assert path == Path("C:\\Users\\student\\AppData\\Roaming") / "CAVY" / "eaal.db"


def test_linux_default_uses_xdg_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EAAL_DB_PATH", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/student/.local/share")
    path = default_db_path()
    assert path == Path("/home/student/.local/share") / "cavy" / "eaal.db"


def test_default_db_path_never_depends_on_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative path here would silently break under any launch method
    (Dock, Finder, a packaged .app) whose working directory isn't the
    project checkout — see the ``_default_data_dir`` docstring."""
    monkeypatch.delenv("EAAL_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_db_path().is_absolute()
