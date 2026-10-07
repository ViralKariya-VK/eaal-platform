"""Server-wide switches an administrator can flip, stored in the database."""

from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.models import AppSetting

LAB_MODE = "lab_mode"


def get_flag(session_factory: sessionmaker[OrmSession], key: str, default: bool = True) -> bool:
    with session_factory() as db:
        row = db.get(AppSetting, key)
        if row is None:
            return default
        return row.value == "1"


def set_flag(session_factory: sessionmaker[OrmSession], key: str, value: bool) -> None:
    with session_factory() as db:
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value="1" if value else "0"))
        else:
            row.value = "1" if value else "0"
        db.commit()
