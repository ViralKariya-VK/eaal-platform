"""Shared pytest fixtures for CAVY tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """A fresh SQLite-backed engine in a temp directory, schema created."""
    engine = create_db_engine(tmp_path / "test.db")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_factory(db_engine: Engine) -> sessionmaker[OrmSession]:
    return create_session_factory(db_engine)


@pytest.fixture
def db_session(db_session_factory: sessionmaker[OrmSession]) -> Iterator[OrmSession]:
    with db_session_factory() as session:
        yield session
