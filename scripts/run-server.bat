@echo off
REM Starts the CAVY server (and its admin panel) on Windows.
REM Activate your environment first (e.g. .venv\Scripts\activate), then run: scripts\run-server.bat
cd /d "%~dp0.."
python -m eaal_platform.server %*
