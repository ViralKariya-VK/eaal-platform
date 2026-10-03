# Build the CAVY Windows installer (instructions for a Claude session)

**How to use this file:** open Claude Code on the **Windows** computer, then
paste everything from the line `=== START OF TASK ===` to the end into the
chat. The session needs no other context. (If you would rather not build on
Windows at all, see "Option B: let GitHub build it" at the very end.)

---

=== START OF TASK ===

You are on a **Windows 10/11 (64-bit)** computer. Your job: build the Windows
installer for **CAVY**, a classroom coding-education desktop app, prove that
the built app really works, and report back. You have shell access. Work step
by step, check each result before moving on, and **never claim something
works unless you ran it and saw it work**.

## What you are building

- Source: https://github.com/ViralKariya-VK/eaal-platform (branch **main**).
- It is a Python 3.11 app (pywebview window + a web UI) with a classroom
  server inside it. It must be packaged so that a student can install it with
  **no Python, no internet and no admin rights**.
- Tooling already in the repo: `scripts/build_release.py` (the build driver),
  `packaging/cavy.spec` (PyInstaller recipe), `packaging/cavy.iss` (Inno Setup
  script that makes the Setup.exe), `packaging/entry.py` (entry point).
- Expected output: **`dist\CAVY-0.1.0-Windows-Setup.exe`** (and the unpacked
  app in `dist\CAVY\CAVY.exe`). If Inno Setup is missing the script falls back
  to a `.zip`, which is acceptable only as a last resort.

Windows has **never been built or run before**. Expect to hit real problems.
Fix them minimally, and say exactly what you changed.

## Rules

1. Do not put secrets anywhere. The file `groq.txt` (if it exists) is private
   and gitignored: never read it, print it or commit it.
2. Do **not** push to `main`. If source changes are needed, make them on a new
   branch named `windows-build-fixes`, commit there, and tell the user. Never
   force-push.
3. Do not install anything unrelated to this build. Prefer per-user installs.
4. Keep a log: save the full output of the build to `build-windows.log`.
5. If you are stuck after two honest attempts at a problem, stop and report the
   exact error text instead of guessing.

## Step 1 - Prerequisites

Check each one first (`<tool> --version`); install only what is missing. In
PowerShell (winget ships with Windows 11 and recent Windows 10):

```powershell
git --version
python --version          # must be 3.11.x or 3.12.x, 64-bit
node --version            # needed only to download the code editor (Monaco)
winget install --id Git.Git -e
winget install --id Python.Python.3.11 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id JRSoftware.InnoSetup -e
```

Open a **new** terminal after installing so PATH updates. Inno Setup installs to
`C:\Program Files (x86)\Inno Setup 6\ISCC.exe` (the build script finds it there).
Confirm Python is 64-bit: `python -c "import struct; print(struct.calcsize('P')*8)"` prints 64.

The Microsoft **WebView2 runtime** is needed to *run* CAVY (Windows 10/11 already
include it). Check with:
`reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv`
If missing, install "Evergreen WebView2 Runtime" from Microsoft.

## Step 2 - Get the code and a clean environment

```powershell
cd $HOME\Documents
git clone https://github.com/ViralKariya-VK/eaal-platform.git
cd eaal-platform
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process Bypass
python -m pip install --upgrade pip
python -m pip install -e ".[server,sandbox-libs,build]" pillow
```

Sanity-check the source before building (this also tells us whether the
*code* works on Windows, separately from packaging):

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q --no-cov
```

Expected: about 296 passed, 1 skipped. **Record every failing test name and its
error**: failures here are real Windows bugs. Do not hide them. (A few tests
may be POSIX-specific; say which and why.)

## Step 3 - Build

```powershell
python scripts\build_release.py 2>&1 | Tee-Object build-windows.log
```

What it does: fetches the editor with npm, makes `build\icon.ico`, runs
PyInstaller on `packaging\cavy.spec`, runs a smoke test that executes a small
Python script **through the packaged app** (`CAVY.exe --cavy-run-script`) and
imports numpy, then runs Inno Setup. It takes roughly 3-10 minutes. Success ends
with `Built ...CAVY-0.1.0-Windows-Setup.exe`.

Note `scripts\build_release.py --package-only` redoes only the installer step
using an existing `dist\`.

### Known risks and what to do

| Symptom | Likely cause and fix |
|---|---|
| `ModuleNotFoundError: clr` / `pythonnet` at app start | pywebview's Windows backend. `pip install pythonnet`; make sure PyInstaller collects it: add `collect_all("pythonnet")` and `"clr"` / `"clr_loader"` to `hiddenimports`/`datas` in `packaging\cavy.spec`. |
| Window never appears, process exits silently | The app is built without a console. Rebuild once with `console=True` in `packaging\cavy.spec` (the `EXE(...)` block) to see the traceback, fix, then set it back to `False`. |
| `Failed to collect submodules for 'webview.platforms.android'` | Harmless warning. |
| `Hidden import 'msilib'/'winsound'... not found` | Harmless on Mac; on Windows a few POSIX-only stdlib names may warn. Ignore warnings, fix only errors. |
| Smoke test says numpy import failed | numpy's DLLs were not collected: confirm `collect_all("numpy")` ran; check Visual C++ runtime (`winget install Microsoft.VCRedist.2015+.x64`). |
| `ISCC.exe` not found | Install Inno Setup (Step 1) or add its folder to PATH. |
| Antivirus/Defender deletes `CAVY.exe` or blocks the build | Add an exclusion for the project folder temporarily, and tell the user. |
| `OSError: [WinError 206] path too long` | `git config --system core.longpaths true`, clone to a short path like `C:\src\cavy`. |
| Monaco/npm step fails | Not fatal: the app falls back to a plain text editor. Report it. |

## Step 4 - Prove the built app works

Do all of this on the **built** app, not the source tree. A windowed `.exe`
does not print to the console, so always redirect output.

```powershell
$app = (Resolve-Path dist\CAVY\CAVY.exe).Path

# 4a. The packaged app can run student code (stdlib + numpy + pandas)
@'
import json, random, collections, numpy as np, pandas as pd
print(json.dumps({"sum": int(np.arange(5).sum()), "rows": len(pd.DataFrame({"a":[1,2,3]}))}))
'@ | Set-Content -Encoding ascii $env:TEMP\cavy_t.py
Start-Process $app -ArgumentList "--cavy-run-script", "$env:TEMP\cavy_t.py" -Wait -NoNewWindow `
  -RedirectStandardOutput $env:TEMP\out.txt -RedirectStandardError $env:TEMP\err.txt
Get-Content $env:TEMP\out.txt, $env:TEMP\err.txt        # expect {"sum": 10, "rows": 3}

# 4b. The classroom server starts headless and serves the admin page
$p = Start-Process $app -ArgumentList "--cavy-serve","--port","8470","--db","$env:TEMP\cavy_t.db" -PassThru -WindowStyle Hidden
Start-Sleep 12
Invoke-RestMethod http://127.0.0.1:8470/api/health            # expect ok=True service=cavy
(Invoke-WebRequest http://127.0.0.1:8470/admin -UseBasicParsing).StatusCode   # expect 200
Stop-Process -Id $p.Id -Force

# 4c. The desktop window opens
$w = Start-Process $app -PassThru
Start-Sleep 15
if (-not $w.HasExited) { "window process still running - good" } else { "EXITED: investigate" }
```

For 4c also take a screenshot of the screen (or ask the user to look) to confirm
the CAVY **login screen** is actually visible; "process still running" alone is
not proof. Then close it (`Stop-Process -Id $w.Id`).

Next, **install it like a student would**: run
`dist\CAVY-0.1.0-Windows-Setup.exe` (SmartScreen will warn because the file is
unsigned: *More info -> Run anyway*), finish the wizard, launch CAVY from the
Start menu, and confirm in the UI:

1. Login screen shows. *Create an account* works (student).
2. Bottom of the login screen: **Host a server on this computer -> Start server**
   shows an address like `192.168.x.x:8000` (allow the Windows Firewall prompt
   on *Private networks*).
3. *Open admin panel* opens a browser at `/admin` and asks to create an administrator.
4. In the student workspace: write `print("hello")`, press **Run Code**, see `hello`;
   then run `while True: pass` and confirm it stops with a time-out message and
   the app stays responsive.
5. Resources screen opens; as a teacher, *Add resource* with a PDF works and
   **Open** launches the PDF in the default viewer.
6. Uninstall works (Settings -> Apps).

## Step 5 - Report

Reply with exactly this structure:

```
RESULT: SUCCESS | PARTIAL | FAILED
Installer: <full path>, size <MB>, SHA-256 <hash>   (Get-FileHash)
Source tests on Windows: <N passed / N failed>; failing tests: <names + 1-line reason>
Smoke test (4a): <output>
Headless server (4b): <health + admin status>
Window (4c): <opened? evidence>
Installed-app checks 1-6: <pass/fail each, with notes>
Changes made to the repository: <files + why> (branch windows-build-fixes, commit <hash>) or "none"
Problems still open: <list, with exact error text>
```

Finally, copy `build-windows.log` and the installer somewhere the user can
find them (tell them the path). Do not upload anything anywhere unless asked.

=== END OF TASK ===

---

## Option B: let GitHub build it (no Windows computer needed)

The repository has a workflow that builds both installers on GitHub's servers.

1. Open https://github.com/ViralKariya-VK/eaal-platform/actions
2. Choose **Build installers** (left list) -> **Run workflow** -> branch `main` -> **Run workflow**.
3. Wait about 15-25 minutes. Open the finished run and download the
   **CAVY-Windows** and **CAVY-macOS** artifacts from the bottom of the page.

Or from a terminal with the GitHub CLI (`gh auth login` once):

```bash
gh workflow run release.yml --ref main
gh run watch                                   # follow it
gh run download --name CAVY-Windows --dir dist-windows
```

The result is the same unsigned installer, so the same Windows SmartScreen
warning appears on first run (*More info -> Run anyway*). The GitHub build does
**not** run the "prove the built app works" checks in Step 4: do those on a
real Windows computer before handing the installer to students.
