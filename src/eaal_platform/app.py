"""CAVY application entry point.

Wires together the SQLite engine, the background event logger, and the
``CavyApi`` bridge, then opens a native webview pointed at the bundled
frontend. Kept deliberately thin — anything with logic belongs in a module
that can be unit-tested without a running webview (see ``api/bridge.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import webview

from eaal_platform.ai.ollama_provider import OllamaProvider
from eaal_platform.api.bridge import CavyApi
from eaal_platform.client.api import build_client_api
from eaal_platform.db.bootstrap import seed_demo_content
from eaal_platform.db.engine import create_db_engine, create_session_factory, init_db
from eaal_platform.events.logger import EventLogger
from eaal_platform.logging_setup import configure_logging

_INDEX_HTML = Path(__file__).resolve().parent / "web" / "index.html"
_ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.png"


def _set_macos_dock_identity() -> None:
    """Best-effort: give the Dock/menu bar CAVY's name and icon.

    The app isn't packaged as a real ``.app`` bundle yet, so without this
    macOS shows the Dock icon and app-switcher name of whatever launched
    it (``python3.11``, blank icon) instead of CAVY. This is a runtime
    workaround via pyobjc (already installed — pywebview's own macOS
    backend depends on it) rather than a substitute for real packaging;
    it's cosmetic, so any failure here is swallowed rather than blocking
    the app from starting.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = "CAVY"

        if _ICON_PATH.is_file():
            image = NSImage.alloc().initWithContentsOfFile_(str(_ICON_PATH))
            if image is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:  # nosec B110 -- cosmetic only, must never block startup
        pass


def _ask_where_to_save(suggested_name: str) -> str | None:
    """Show the OS "Save as" dialog; None if the user cancels.

    The webview can't download files on its own (pywebview blocks it by
    default), so exports are written by Python to a path chosen here.
    """
    window = webview.windows[0]
    chosen = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=suggested_name)
    if not chosen:
        return None
    return chosen if isinstance(chosen, str) else chosen[0]


def main() -> int:
    configure_logging()
    engine = create_db_engine()
    init_db(engine)
    session_factory = create_session_factory(engine)
    seed_demo_content(session_factory)

    event_logger = EventLogger(session_factory)
    event_logger.start()

    # Standalone data lives in the local database; if a server address is
    # configured (Profile / login screen), the same calls go to the server.
    local_api = CavyApi(session_factory, event_logger, ai_provider=OllamaProvider())
    api = build_client_api(local_api, _ask_where_to_save)

    webview.create_window(
        "CAVY",
        url=str(_INDEX_HTML),
        js_api=api,
        width=1280,
        height=820,
        min_size=(960, 640),
        background_color="#FAF6EF",
    )
    _set_macos_dock_identity()
    webview.start()
    api.shutdown()

    event_logger.stop()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
