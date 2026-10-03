"""Where this computer's CAVY should connect (stored per machine)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from eaal_platform.db.engine import default_db_path


def settings_path() -> Path:
    return default_db_path().parent / "client.json"


def normalise_url(raw: str) -> str:
    """Accept '192.168.1.5:8000' or a full URL; return 'http://host:port'."""
    url = raw.strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}"
    return url


def load_server_url() -> str | None:
    """The saved server address; ``EAAL_SERVER_URL`` overrides it for scripted setups."""
    env = os.environ.get("EAAL_SERVER_URL", "").strip()
    if env:
        return normalise_url(env)
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url = data.get("server_url") if isinstance(data, dict) else None
    return normalise_url(url) if isinstance(url, str) and url else None


def save_server_url(url: str | None) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"server_url": url}), encoding="utf-8")
