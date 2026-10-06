@echo off
REM Double-click me on the SERVER computer. I print the admin panel's login (a fresh
REM password every time: the old one can't be read back, only replaced).
REM Works from the source folder (with Python) or from the installed CAVY app.
title CAVY admin password
cd /d "%~dp0"
echo.
set "CAVY_EXE="
if exist "%ProgramFiles%\CAVY\CAVY.exe" set "CAVY_EXE=%ProgramFiles%\CAVY\CAVY.exe"
if exist "%LocalAppData%\Programs\CAVY\CAVY.exe" set "CAVY_EXE=%LocalAppData%\Programs\CAVY\CAVY.exe"
if exist "src\eaal_platform\server\__main__.py" (
  set "PYTHONPATH=%~dp0src"
  if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m eaal_platform.server --reset-admin-password
  ) else (
    python -m eaal_platform.server --reset-admin-password
  )
) else if defined CAVY_EXE (
  "%CAVY_EXE%" --cavy-serve --reset-admin-password
) else (
  echo Could not find Python or the CAVY app here.
)
echo Open the admin panel at  http://127.0.0.1:8000/admin   (or http://THIS-COMPUTER-ADDRESS:8000/admin)
echo Sign in with the email and password shown above.
echo.
pause
