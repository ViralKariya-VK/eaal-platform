# PyInstaller recipe for CAVY (macOS .app and Windows folder). Build it with
# scripts/build_release.py rather than calling PyInstaller directly.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is provided by PyInstaller
PKG = ROOT / "src" / "eaal_platform"
VERSION = "0.1.0"

datas = [
    (str(PKG / "web"), "eaal_platform/web"),
    (str(PKG / "assets"), "eaal_platform/assets"),
    (str(PKG / "server" / "admin_ui"), "eaal_platform/server/admin_ui"),
]
binaries = []
hiddenimports = []

# The app, the server it can host, and the web stack behind that server.
for package in (
    "eaal_platform",
    "webview",
    "uvicorn",
    "fastapi",
    "starlette",
    "pydantic",
    "anyio",
    "openpyxl",
    "sqlalchemy.dialects.sqlite",
):
    hiddenimports += collect_submodules(package)

# Libraries that student code may import (see the sandbox): ship them whole.
for package in ("numpy", "pandas", "matplotlib"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Student code runs inside this same bundle (see launcher.py), so it can only
# import what is in here: include the whole standard library, not just the
# parts the app itself happens to use.
_SKIP = {"antigravity", "this", "idlelib", "tkinter", "turtledemo", "test", "lib2to3", "ensurepip"}
hiddenimports += sorted(
    name for name in sys.stdlib_module_names if not name.startswith("_") and name not in _SKIP
)

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = None
if sys.platform == "darwin" and (ROOT / "build" / "icon.icns").exists():
    icon = str(ROOT / "build" / "icon.icns")
elif sys.platform == "win32" and (ROOT / "build" / "icon.ico").exists():
    icon = str(ROOT / "build" / "icon.ico")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CAVY",
    console=False,  # no terminal window; the app has its own window
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CAVY")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="CAVY.app",
        icon=icon,
        bundle_identifier="org.cavy.platform",
        version=VERSION,
        info_plist={
            "CFBundleName": "CAVY",
            "CFBundleDisplayName": "CAVY",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # macOS asks before an app talks to other computers on the network.
            "NSLocalNetworkUsageDescription": (
                "CAVY connects to your classroom's CAVY server on the local network."
            ),
        },
    )
