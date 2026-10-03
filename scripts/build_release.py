#!/usr/bin/env python3
"""Build the installable CAVY for the computer you run this on.

    python scripts/build_release.py

- macOS   -> dist/CAVY-<version>-macOS-<arch>.dmg  (drag CAVY into Applications)
- Windows -> dist/CAVY-<version>-Windows-Setup.exe (needs Inno Setup; otherwise a .zip)

PyInstaller can't cross-compile: build the Mac installer on a Mac and the
Windows installer on Windows (the GitHub Actions workflow does both).
Everything the app needs, including Python and all libraries, is inside the
installer, so installing needs no internet.
"""

from __future__ import annotations

import argparse
import importlib.util
import platform
import shutil
import subprocess  # nosec B404 - this script's job is running build tools
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICON_PNG = ROOT / "src" / "eaal_platform" / "assets" / "icon.png"
MONACO_LOADER = ROOT / "src" / "eaal_platform" / "assets" / "monaco" / "vs" / "loader.js"
VERSION = "0.1.0"
FONT_SERIF = ROOT / "src" / "eaal_platform" / "assets" / "fonts" / "Lora-Variable.ttf"
FONT_SANS = ROOT / "src" / "eaal_platform" / "assets" / "fonts" / "Inter-Regular.ttf"

# Installer window (points). Icons sit on the two plates drawn in the background.
DMG_WINDOW = (660, 420)
DMG_APP_POS = (170, 200)
DMG_APPLICATIONS_POS = (490, 200)


def run(command: list[str], **kwargs: object) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, **kwargs)  # type: ignore[call-overload]  # nosec B603


def ensure_editor() -> None:
    """The code editor (Monaco) is not stored in git; fetch it so it ships in the installer."""
    if MONACO_LOADER.exists():
        return
    if shutil.which("npm") is None:
        print("WARNING: npm not found, so the installer will use the plain-text editor.")
        return
    run([sys.executable, str(ROOT / "scripts" / "fetch_monaco.py")])


def build_icon() -> None:
    BUILD.mkdir(exist_ok=True)
    if sys.platform == "darwin":
        spec = importlib.util.spec_from_file_location(
            "build_macos_app", ROOT / "scripts" / "build_macos_app.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._build_icns(ICON_PNG, BUILD / "icon.icns")
    elif sys.platform == "win32":
        from PIL import Image

        with Image.open(ICON_PNG) as image:
            image.save(BUILD / "icon.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])


def smoke_test(executable: Path) -> None:
    """Prove the *packaged* app can run student code (it re-launches itself to do so)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "main.py"
        script.write_text(
            "import random, json, collections, numpy as np\n"
            "print(json.dumps({'sum': int(np.arange(5).sum()), 'ok': True}))\n",
            encoding="utf-8",
        )
        done = subprocess.run(  # nosec B603
            [str(executable), "--cavy-run-script", str(script)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    if done.returncode != 0 or '"sum": 10' not in done.stdout:
        raise SystemExit(
            f"Smoke test FAILED (exit {done.returncode}).\n"
            f"stdout: {done.stdout}\nstderr: {done.stderr}"
        )
    print("Smoke test passed: the packaged app runs student code with numpy.")


def make_dmg_background(destination: Path) -> None:
    """Draw the installer window's background (a Retina-ready TIFF).

    Finder prints icon names in black or white depending on the viewer's
    light/dark mode, and we can't choose, so each icon sits on a mid-tone
    plate that keeps both readable.
    """
    from PIL import Image, ImageDraw, ImageFont

    scale = 2  # draw at 2x for Retina, then let tiffutil pair it with a 1x copy
    width, height = DMG_WINDOW

    def render(factor: int) -> Image.Image:
        image = Image.new("RGB", (width * factor, height * factor), "#faf6ef")
        draw = ImageDraw.Draw(image)

        def px(value: float) -> int:
            return int(value * factor)

        def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
            return ImageFont.truetype(str(path), px(size))

        # Header band
        draw.rectangle([0, 0, px(width), px(78)], fill="#0f1b2e")
        draw.text((px(32), px(14)), "Install CAVY", font=font(FONT_SERIF, 30), fill="#faf6ef")
        draw.text(
            (px(34), px(52)),
            "Drag CAVY into Applications",
            font=font(FONT_SANS, 14),
            fill="#e0a44a",
        )
        # Plates behind the two icons
        plate = "#747d92"
        for cx in (DMG_APP_POS[0], DMG_APPLICATIONS_POS[0]):
            draw.rounded_rectangle(
                [px(cx - 90), px(DMG_APP_POS[1] - 80), px(cx + 90), px(DMG_APP_POS[1] + 88)],
                radius=px(18),
                fill=plate,
            )
        # Arrow between them
        y = DMG_APP_POS[1] - 6
        x0, x1 = DMG_APP_POS[0] + 105, DMG_APPLICATIONS_POS[0] - 105
        draw.line([px(x0), px(y), px(x1 - 6), px(y)], fill="#e0a44a", width=px(5))
        draw.polygon(
            [(px(x1), px(y)), (px(x1 - 20), px(y - 15)), (px(x1 - 20), px(y + 15))], fill="#e0a44a"
        )
        # First-launch hint
        note = font(FONT_SANS, 12)
        lines = [
            (
                'If macOS says "CAVY Not Opened": click Done, then open System Settings >'
                " Privacy & Security,",
                "#3d3a31",
            ),
            (
                'scroll down and click "Open Anyway" next to CAVY. You only need to do this once.',
                "#3d3a31",
            ),
            (
                "Needs a Mac with Apple Silicon (M1 or newer). No internet needed to install.",
                "#8a8270",
            ),
        ]
        for k, (text, colour) in enumerate(lines):
            draw.text((px(32), px(height - 70 + 22 * k)), text, font=note, fill=colour)
        return image

    with tempfile.TemporaryDirectory() as tmp:
        one, two = Path(tmp) / "bg.png", Path(tmp) / "bg@2x.png"
        render(1).save(one, dpi=(72, 72))
        render(scale).save(two, dpi=(144, 144))
        run(["tiffutil", "-cathidpicheck", str(one), str(two), "-out", str(destination)])


def make_dmg(app: Path, dmg: Path) -> None:
    import dmgbuild

    BUILD.mkdir(exist_ok=True)
    background = BUILD / "dmg-background.tiff"
    make_dmg_background(background)
    icon = BUILD / "icon.icns"
    dmg.unlink(missing_ok=True)
    dmgbuild.build_dmg(
        str(dmg),
        "CAVY",
        settings={
            "format": "UDZO",
            "files": [str(app)],
            "symlinks": {"Applications": "/Applications"},
            "background": str(background),
            "icon": str(icon) if icon.exists() else None,
            "icon_size": 112,
            "text_size": 13,
            # Finder counts the 28 pt title bar inside the window size.
            "window_rect": ((200, 140), (DMG_WINDOW[0], DMG_WINDOW[1] + 28)),
            "icon_locations": {
                "CAVY.app": DMG_APP_POS,
                "Applications": DMG_APPLICATIONS_POS,
            },
            "default_view": "icon-view",
            "show_status_bar": False,
            "show_tab_view": False,
            "show_toolbar": False,
            "show_pathbar": False,
            "show_sidebar": False,
        },
    )


def package_macos() -> Path:
    app = DIST / "CAVY.app"
    smoke_test(app / "Contents" / "MacOS" / "CAVY")
    arch = platform.machine().replace("arm64", "AppleSilicon").replace("x86_64", "Intel")
    dmg = DIST / f"CAVY-{VERSION}-macOS-{arch}.dmg"
    make_dmg(app, dmg)
    return dmg


def package_windows() -> Path:
    smoke_test(DIST / "CAVY" / "CAVY.exe")
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if iscc is None:
        for candidate in (
            Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
            Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
        ):
            if candidate.exists():
                iscc = str(candidate)
    if iscc is None:
        print("Inno Setup not found; making a zip instead (install Inno Setup for a Setup.exe).")
        archive = shutil.make_archive(str(DIST / f"CAVY-{VERSION}-Windows"), "zip", DIST / "CAVY")
        return Path(archive)
    run([iscc, str(ROOT / "packaging" / "cavy.iss")])
    return DIST / f"CAVY-{VERSION}-Windows-Setup.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the CAVY installer for this computer.")
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="skip the slow app build and redo just the installer from the existing dist/",
    )
    args = parser.parse_args()
    if sys.platform not in ("darwin", "win32"):
        print("Installers are built for macOS and Windows only.", file=sys.stderr)
        return 1
    ensure_editor()
    build_icon()
    if not args.package_only:
        shutil.rmtree(DIST, ignore_errors=True)
        _freeze()
    result = package_macos() if sys.platform == "darwin" else package_windows()
    size_mb = result.stat().st_size / 1_000_000
    print(f"\nBuilt {result}  ({size_mb:.0f} MB)")
    return 0


def _freeze() -> None:
    """Turn the app into a self-contained folder / .app with PyInstaller."""
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(ROOT / "packaging" / "cavy.spec"),
            "--noconfirm",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD / "pyinstaller"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
