#!/usr/bin/env python3
"""Build a minimal ``CAVY.app`` bundle so macOS shows the right Dock name/icon.

Running ``python -m eaal_platform.app`` directly launches a bare Python
process with no ``Info.plist`` of its own, so macOS's Dock and app switcher
fall back to the executable's own identity: "python3.11" and a generic
document icon. That isn't something fixable from *inside* the running
process — the Dock reads an app's name and icon from Launch Services,
which resolves them from a bundle's ``Info.plist``/``.icns`` before the
process ever starts, not from anything the process does at runtime.

This script builds that bundle: it converts ``assets/icon.png`` to an
``.icns`` (via macOS's own ``sips``/``iconutil`` — no extra dependencies),
writes an ``Info.plist`` naming the app "CAVY", and writes a tiny launcher
script that just re-execs the current Python interpreter with
``-m eaal_platform.app``. The result, ``CAVY.app`` in the project root, is
a real (if minimal) app bundle — not a substitute for proper distribution
packaging (a real release would use ``py2app``/``pyinstaller`` to also
bundle the interpreter and dependencies), but enough for local development
and testing to see CAVY's actual name and icon everywhere macOS shows them.

Run once (or whenever the icon changes) from the project root:

    python scripts/build_macos_app.py

Then launch with ``open CAVY.app`` instead of ``python -m eaal_platform.app``.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess  # nosec B404 -- shells out to macOS's own sips/iconutil only
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_PNG = PROJECT_ROOT / "src" / "eaal_platform" / "assets" / "icon.png"
APP_BUNDLE = PROJECT_ROOT / "CAVY.app"

# (iconset filename, pixel size) — the exact set macOS's iconset format
# expects; see `man iconutil`. Deliberately not derived from a loop over
# "logical" sizes doubled for @2x, since that produces sizes iconutil
# doesn't recognize (e.g. a 2048px "1024x1024@2x") — this is just the
# fixed, documented list.
_ICONSET_ENTRIES: tuple[tuple[str, int], ...] = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)

_LAUNCHER_TEMPLATE = """#!/bin/sh
# Deliberately NOT `exec`: replacing this process's image with the Python
# interpreter also replaces its on-disk executable path, and macOS's
# Launch Services derives an app's Dock name/icon from the *running
# process's* path matching something under a bundle's Contents/MacOS —
# once that's the interpreter's own path outside CAVY.app, the Dock falls
# straight back to "python3.11" again, defeating the whole bundle. Running
# Python as a child keeps this shell script itself as the tracked
# process, with its path staying under CAVY.app for as long as it runs.
"{python_executable}" -m eaal_platform.app &
child=$!
trap 'kill "$child" 2>/dev/null' TERM INT
wait "$child"
"""


def _build_icns(source_png: Path, dest_icns: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="cavy-iconset-") as tmp:
        iconset = Path(tmp) / "CAVY.iconset"
        iconset.mkdir()
        for filename, size in _ICONSET_ENTRIES:
            subprocess.run(  # nosec B603
                [
                    "sips",
                    "-z",
                    str(size),
                    str(size),
                    str(source_png),
                    "--out",
                    str(iconset / filename),
                ],
                check=True,
                capture_output=True,
            )
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest_icns)], check=True)  # nosec B603, B607


def main() -> int:
    if sys.platform != "darwin":
        print("This script only makes sense on macOS.", file=sys.stderr)
        return 1
    if not ICON_PNG.is_file():
        print(f"{ICON_PNG} not found — run scripts/generate_icon.py first.", file=sys.stderr)
        return 1

    if APP_BUNDLE.exists():
        shutil.rmtree(APP_BUNDLE)

    macos_dir = APP_BUNDLE / "Contents" / "MacOS"
    resources_dir = APP_BUNDLE / "Contents" / "Resources"
    macos_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)

    print("Converting icon.png to icon.icns...")
    _build_icns(ICON_PNG, resources_dir / "icon.icns")

    info_plist = {
        "CFBundleName": "CAVY",
        "CFBundleDisplayName": "CAVY",
        "CFBundleIdentifier": "org.cavy.platform",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "CAVY",
        "CFBundleIconFile": "icon",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    }
    with (APP_BUNDLE / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump(info_plist, f)

    launcher_path = macos_dir / "CAVY"
    launcher_path.write_text(_LAUNCHER_TEMPLATE.format(python_executable=sys.executable))
    launcher_path.chmod(0o755)

    print(f"Built {APP_BUNDLE}")
    print("Launch it with: open CAVY.app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
