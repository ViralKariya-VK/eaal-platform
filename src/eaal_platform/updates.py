"""Finding out whether a newer CAVY has been released.

Releases live on GitHub (the repository is public, so no key is needed). The app asks for the
list of releases, picks the highest version, and compares it with its own. It never downloads or
installs anything: the person clicks through to the download and runs the installer themselves.

Rules this module keeps:

- Offline, blocked or slow: report ``ok: False``, never raise, never block for long.
- Ask at most about twice a day (the answer is kept in a small file in the data folder).
- Only ever point at addresses on this project's own GitHub page.
- A person can postpone the notice for a version; it comes back on the next version.
"""

from __future__ import annotations

import json
import platform
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from eaal_platform.buildinfo import VERSION
from eaal_platform.db.engine import default_db_path
from eaal_platform.logging_setup import get_logger

log = get_logger(__name__)

REPO = "ViralKariya-VK/eaal-platform"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=15"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
_TRUSTED_PREFIX = f"https://github.com/{REPO}/"
_CHECK_EVERY_SECONDS = 12 * 3600
_SNOOZE_SECONDS = 3 * 24 * 3600
_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

Version = tuple[int, int, int]


def parse_version(text: object) -> Version | None:
    """``"v0.2.1"`` or ``"0.2.1"`` -> ``(0, 2, 1)``; anything else -> ``None``."""
    match = _VERSION_RE.match(str(text or "").strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def is_newer(candidate: object, current: object) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


_SAME_VERSION = "Every computer must run the same version."


def describe_mismatch(app_version: object, server_version: object) -> str:
    """What to tell a person whose app and server do not run the same version."""
    app, server = parse_version(app_version), parse_version(server_version)
    if server is None:
        return (
            f"This server runs an older CAVY than this app ({app_version}). The computer that "
            f"hosts the server needs to be updated. {_SAME_VERSION}"
        )
    if app is None or app < server:
        return (
            f"This server runs CAVY {server_version}, but this app is "
            f"{app_version or 'an older version'}. Update this app to {server_version}. "
            f"{_SAME_VERSION}"
        )
    return (
        f"This app is CAVY {app_version}, but the server runs {server_version}. The computer "
        f"that hosts the server needs to be updated to {app_version}. {_SAME_VERSION}"
    )


def pick_asset(
    assets: list[dict[str, Any]], system: str | None = None, machine: str | None = None
) -> str | None:
    """The download for this computer, or ``None`` if the release has none for it."""
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    names = [(str(a.get("name", "")), str(a.get("browser_download_url", ""))) for a in assets]
    names = [(n, u) for n, u in names if u.startswith(_TRUSTED_PREFIX)]
    if system.startswith("win"):
        wanted = [(n, u) for n, u in names if n.lower().endswith("-setup.exe")]
    elif system == "darwin":
        wanted = [(n, u) for n, u in names if n.lower().endswith(".dmg")]
        arch = "applesilicon" if machine in ("arm64", "aarch64") else "intel"
        exact = [(n, u) for n, u in wanted if arch in n.lower()]
        wanted = exact or wanted
    else:
        wanted = []
    return wanted[0][1] if wanted else None


def latest_release(http: httpx.Client) -> dict[str, Any] | None:
    """The newest published release (pre-releases count), or ``None`` when there is none."""
    response = http.get(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    best: tuple[Version, dict[str, Any]] | None = None
    for release in response.json():
        if not isinstance(release, dict) or release.get("draft"):
            continue
        version = parse_version(release.get("tag_name"))
        if version is not None and (best is None or version > best[0]):
            best = (version, release)
    return best[1] if best else None


def _state_path() -> Path:
    return default_db_path().parent / "update_check.json"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        log.warning("could not save the update-check file", extra={"path": str(path)})


def _summary(release: dict[str, Any]) -> dict[str, Any]:
    tag = str(release.get("tag_name", ""))
    page = str(release.get("html_url") or "")
    if not page.startswith(_TRUSTED_PREFIX):
        page = RELEASES_PAGE
    notes = str(release.get("body") or "").strip()
    return {
        "latest": tag.lstrip("v"),
        "page": page,
        "download": pick_asset(release.get("assets") or []),
        "notes": notes[:600],
    }


def check_for_update(
    current: str = VERSION,
    *,
    http: httpx.Client | None = None,
    force: bool = False,
    now: Callable[[], float] = time.time,
    state_file: Path | None = None,
) -> dict[str, Any]:
    """Is a newer CAVY out? Never raises.

    Returns ``{"ok": True, "current", "latest", "update_available", "snoozed", "page", "download",
    "notes"}`` or ``{"ok": False, "current", "error"}`` when GitHub could not be reached.
    """
    path = state_file or _state_path()
    state = _load_state(path)
    cached = state.get("release") if isinstance(state.get("release"), dict) else None
    fresh = cached is not None and now() - float(state.get("checked_at", 0)) < _CHECK_EVERY_SECONDS
    if not (fresh and not force):
        client = http or httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
        try:
            release = latest_release(client)
        except (httpx.HTTPError, ValueError) as exc:
            log.info("update check failed", extra={"error": str(exc)})
            if cached is None:
                return {
                    "ok": False,
                    "current": current,
                    "error": "Couldn't reach GitHub to check for updates.",
                }
        else:
            if release is None:
                return {
                    "ok": True,
                    "current": current,
                    "latest": current,
                    "update_available": False,
                    "snoozed": False,
                    "page": RELEASES_PAGE,
                    "download": None,
                    "notes": "",
                }
            cached = _summary(release)
            state["release"], state["checked_at"] = cached, now()
            _save_state(path, state)
        finally:
            if http is None:
                client.close()
    if cached is None:  # unreachable: every path above either sets it or returns
        return {"ok": False, "current": current, "error": "No release information."}
    available = is_newer(cached["latest"], current)
    snoozed = state.get("snoozed") or {}
    postponed = (
        available
        and snoozed.get("version") == cached["latest"]
        and now() < float(snoozed.get("until", 0))
    )
    return {
        "ok": True,
        "current": current,
        "update_available": available,
        "snoozed": bool(postponed),
        **cached,
    }


def snooze(
    version: str, *, now: Callable[[], float] = time.time, state_file: Path | None = None
) -> None:
    """Hide the notice for this version for a few days."""
    path = state_file or _state_path()
    state = _load_state(path)
    state["snoozed"] = {"version": version, "until": now() + _SNOOZE_SECONDS}
    _save_state(path, state)
