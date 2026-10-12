"""Clearing a database: back it up, then remove everything except the administrators' own settings.

For starting a class from a clean slate (removing the test labs, students and sessions) without
touching who can sign in to the admin panel, the mail account, or the lab-mode switch.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, text

from eaal_platform.db.models import Base

# Tables that are left alone by a wipe: the administrators, the mail settings and the app settings.
KEEP_TABLES = ("admins", "email_settings", "app_settings")


def backup_database(db_path: Path, folder: Path | None = None) -> Path:
    """A consistent copy of a SQLite file (safe even while it is open) in ``folder``."""
    target_dir = folder or db_path.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{db_path.stem}-before-clean-{datetime.now():%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(db_path)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return target


def wipe_data(engine: Engine, keep: tuple[str, ...] = KEEP_TABLES) -> dict[str, int]:
    """Delete every row of every table except ``keep``; returns how many rows each table had."""
    removed: dict[str, int] = {}
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in keep:
                continue
            count = connection.execute(text(f"SELECT COUNT(*) FROM {table.name}")).scalar_one()  # nosec B608
            if count:
                connection.execute(table.delete())
                removed[table.name] = int(count)
        connection.execute(text("PRAGMA foreign_keys=ON"))
    return removed
