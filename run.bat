@echo off
REM Starts CAVY on Windows. Just run: run.bat
REM
REM Activate your conda/venv environment first (e.g. `conda activate
REM eaal-platform`) in the same terminal before running this — unlike
REM run.sh, this script does not activate one for you, since conda's
REM install location isn't consistent across machines the way it is on
REM the one dev machine run.sh was written for.
REM
REM CAVY has no Windows-specific packaging step (no equivalent of
REM scripts/build_macos_app.py) — pywebview uses the WebView2 runtime,
REM which ships built in on current Windows 10/11; if it's missing,
REM Windows will prompt to install the redistributable the first time.

cd /d "%~dp0"
python -m eaal_platform.app
