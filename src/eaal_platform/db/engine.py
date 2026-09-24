"""Engine and session-factory management for the CAVY local SQLite store.

WAL (write-ahead logging) mode is enabled on every connection because the
event logger writes from a background thread (see events/logger.py) while
the UI thread may concurrently read for display — WAL allows a writer and
readers to proceed without blocking each other, which SQLite's default
rollback-journal mode does not.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from eaal_platform.db.models import Base

_DEFAULT_DB_NAME = "eaal.db"


def _default_data_dir() -> Path:
    """The OS-appropriate place for a desktop app's own local data.

    Deliberately not a relative path (e.g. ``./eaal_platform_data``) — that
    only happens to work when the app is launched from a terminal already
    sitting in the project directory. Launched any other way (double-clicked
    in Finder, opened from the Dock, a packaged ``.app`` bundle), the
    process's working directory is something else entirely — often a
    directory the app has no permission to write into — and a relative
    path would either write data in the wrong place or fail outright.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CAVY"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "CAVY"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "cavy"


def default_db_path() -> Path:
    """Resolve the SQLite file path.

    Overridable via the ``EAAL_DB_PATH`` environment variable so tests (and
    anything else that wants a specific location) don't have to monkeypatch
    this function directly.
    """
    override = os.environ.get("EAAL_DB_PATH")
    if override:
        return Path(override)
    return _default_data_dir() / _DEFAULT_DB_NAME


def _enable_wal(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_engine(db_path: Path | None = None) -> Engine:
    """Create a SQLAlchemy engine pointed at the given (or default) SQLite file.

    Creates the parent directory if needed, since this is a local-first
    desktop app and there is no server-side provisioning step to rely on.
    """
    path = db_path if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", future=True)
    event.listen(engine, "connect", _enable_wal)
    return engine


def init_db(engine: Engine) -> None:
    """Create all tables that don't already exist."""
    Base.metadata.create_all(engine)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to the given engine."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
