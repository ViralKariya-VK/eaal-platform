#!/usr/bin/env python3
"""Fetch monaco-editor via npm and copy its min/vs build into the app's assets.

Run once (or whenever bumping the Monaco version) from the project root:

    python scripts/fetch_monaco.py

Without this, the app still runs — ``editor/factory.py`` falls back to the
native QPlainTextEdit-based editor whenever ``assets/monaco/vs/loader.js``
is missing. This script is what upgrades that fallback to the real Monaco
experience; it is never required for the app to start.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MONACO_VERSION = "0.52.0"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "src" / "eaal_platform" / "assets" / "monaco"


def main() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print(
            "npm not found on PATH. Install Node.js, or skip this script — the "
            "app will use its native fallback editor instead.",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="eaal-monaco-fetch-") as tmp:
        tmp_path = Path(tmp)
        print(f"Installing monaco-editor@{MONACO_VERSION} into a scratch directory...")
        subprocess.run(
            [
                npm,
                "install",
                f"monaco-editor@{MONACO_VERSION}",
                "--no-save",
                "--prefix",
                str(tmp_path),
            ],
            check=True,
        )

        source_min = tmp_path / "node_modules" / "monaco-editor" / "min" / "vs"
        if not source_min.is_dir():
            print(f"Expected build output not found at {source_min}", file=sys.stderr)
            return 1

        dest_vs = ASSETS_DIR / "vs"
        if dest_vs.exists():
            shutil.rmtree(dest_vs)
        shutil.copytree(source_min, dest_vs)
        print(f"Copied Monaco min/vs build to {dest_vs}")

    print("Done. The app will now use the bundled Monaco editor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
