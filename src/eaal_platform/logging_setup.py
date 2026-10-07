"""Structured (one JSON object per line) logging for the app and the server.

Every line has a timestamp, level, the module that wrote it and a message, plus
any extra fields given with ``extra={...}``. Lines go to a rotating file in the
CAVY data folder (so a problem on a classroom server can be looked at later) and,
when there is a terminal, to the screen. Passwords, keys and tokens are never
passed to the logger; see the call sites.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from eaal_platform.db.engine import default_db_path

LOG_NAME = "cavy"
_MAX_BYTES = 1_000_000
_BACKUPS = 3
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def log_directory() -> Path:
    return default_db_path().parent / "logs"


def configure_logging(directory: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Set up the ``cavy`` logger once (calling it again changes nothing)."""
    logger = logging.getLogger(LOG_NAME)
    if getattr(logger, "_cavy_configured", False):
        return logger
    logger.setLevel(level)
    logger.propagate = False
    formatter = JsonFormatter()
    try:
        folder = directory or log_directory()
        folder.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            folder / "cavy.log", maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:  # a read-only data folder must not stop the app
        pass
    if sys.stderr is not None and sys.stderr.isatty():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        logger.addHandler(console)
    logger._cavy_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger(name: str) -> logging.Logger:
    """A logger under ``cavy``: ``get_logger(__name__)``."""
    return logging.getLogger(f"{LOG_NAME}.{name.removeprefix('eaal_platform.')}")
