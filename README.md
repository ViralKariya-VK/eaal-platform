# CAVY

CAVY is a desktop platform (Windows + macOS) for coding education that
measures **CIQ (Cognitive Interaction Quotient)**: a framework of 3 pillars
(AI Utilization, Cognitive Engagement, Learning & Knowledge Development)
built from 14 behavioral signals, computed from a full event log of how a
student codes, asks AI for help, and responds to what it gives back.

Two students can submit identical correct code via completely different
learning processes — the platform's whole purpose is to distinguish those.

## Status

CAVY is a working classroom platform for Windows and macOS:

- **Labs.** A professor creates a lab (three stages: Learning, Exploration, Assessment, each with
  its own AI-assistance mode), aims it at a course, year, division and batch, and attaches
  resources. A student works through the stages in one coding screen (Back/Next between them),
  and submits the whole lab once. Practice mode is separate and ungraded.
- **CIQ.** From the full event log of how a student codes and uses the AI, the engine in
  `signals/compute.py` computes the 14 EAAL signals (deterministic ones, code-adoption diffs, and
  three LLM-rubric ones), shown to the student as a progress dashboard and to the professor per
  student and per class.
- **One central server** (`python -m eaal_platform.server`, or "Host a server" inside the app) so a
  professor and many students on one network share the same data live, plus a browser **admin
  panel** at `/admin` (users, courses, approved emails, labs, resources, database browser, audit
  log, backup, email, AI assistant).
- **People and classes.** Only approved emails can sign up; teachers are created by the
  administrator. Courses (with level, department, years and an ID code) are set by the administrator;
  students pick theirs, professors pick the courses they teach, and every student of a course joins
  the class of each professor who teaches it. Students get their first login by email, and forgotten
  passwords are reset with an emailed code.
- **AI.** Students connect their own key (OpenAI, Claude, Gemini, Grok or Groq), kept only on their
  computer; the class assistant is set by the administrator or professor.
- **Lab mode.** While a lab is open the window is full screen, switching apps is blocked where the
  operating system allows it, leaving the window twice submits the lab, and pasted text is recorded
  (and checked against what the AI wrote).
- **Installers** for macOS (`.dmg`) and Windows (`.exe`), built and checked on GitHub.

What it is not yet: encrypted (HTTPS) traffic, offline queuing when the server is unreachable, code
signing of the installers, or a validated CIQ weighting (the overall number is a provisional
equal-weight average). See [docs/ROADMAP.md](docs/ROADMAP.md) and
[docs/DEMO_SETUP.md](docs/DEMO_SETUP.md) (including the honest limits).

## Project layout

```
README.md          you are here
pyproject.toml     dependencies and tool settings
docs/              guides: DEMO_SETUP.md (classroom setup), PERFORMANCE.md (measured speed),
                   WINDOWS_TESTING.md, ROADMAP.md
requirements.lock  the exact package versions the project is tested with
scripts/           everything you run by hand: launchers and installer builders (see scripts/README.md)
src/eaal_platform/ the application
    api/ client/ server/ db/ ai/ signals/ sandbox/ events/ web/ assets/
packaging/         recipes for the installers (PyInstaller, Inno Setup)
tests/             automated tests
validation/        research: validation of the 14 CIQ signals (the paper)
pilot_web/         research: the small web survey used for the pilot study
.github/           automatic builds and tests on GitHub
```

## Setup

```bash
conda create -n eaal-platform python=3.11
conda activate eaal-platform
python -m pip install -e ".[dev]"
```

Student code runs sandboxed in *this same* interpreter (see
`sandbox/executor.py`), so whatever a lab's task expects students to
`import` needs to be installed here too — install the common
data-analysis stack the demo labs use with:

```bash
python -m pip install -e ".[sandbox-libs]"
```

Fetches the Monaco (VS Code) editor into the app's assets (requires npm;
the app still runs without this, falling back to a plain textarea):

```bash
python scripts/fetch_monaco.py
```

Ollama, for the AI assistant, must be installed and running locally
(`ollama serve`) with a model pulled (defaults to `qwen3:8b` — on Windows,
`ollama pull qwen3:8b` from an ordinary terminal after installing Ollama
for Windows).

## Run

macOS / Linux:

```bash
scripts/run-app.sh
```

Windows (after activating your conda/venv environment in the same
terminal — `scripts\run-app.bat` doesn't activate one for you, since a conda install's
location isn't consistent across machines the way it is on the one dev
machine `run-app.sh`'s macOS branch was written for):

```bat
scripts\run-app.bat
```

Either way this runs the app as a plain Python process. On macOS this
means the Dock/menu bar shows it as "python3.11" with a generic icon
unless you build the real (if minimal) app bundle described below — this
step is macOS-only; there's no equivalent packaging step needed on
Windows, since `pywebview` there talks to Microsoft's WebView2 runtime,
which ships built into current Windows 10/11 (Windows itself will prompt
for the small redistributable installer on the rare machine that's missing
it). **Windows compatibility here has been verified by source/dependency
audit, not by running the app on an actual Windows machine — if you hit a
Windows-specific issue, it's real and should be reported/fixed, not
assumed away.**

```bash
python scripts/build_macos_app.py   # once, or whenever assets/icon.png changes
open CAVY.app
```

`CAVY.app` is gitignored — it embeds this machine's own conda
interpreter path and is meant to be rebuilt locally, not committed. This
is a minimal bundle for local use, not a substitute for real distribution
packaging (`py2app`/`pyinstaller` on macOS, `PyInstaller`/`briefcase` on
Windows), which a release would need to also bundle the interpreter and
dependencies so a recipient doesn't need Python installed at all.

## Reproducible install

`requirements.lock` pins every package (151 of them) to the versions the tests run against:

```bash
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

`pip-audit` finds no known vulnerabilities in that list.

## Logging and speed

The app and server write structured logs (one JSON object per line) to `logs/cavy.log` in the
CAVY data folder, rotated at 1 MB. `python scripts/benchmark.py` simulates a class working at
once; the latest numbers are in [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

## Quality gate

```bash
ruff check . && ruff format --check .
mypy src
pytest                       # unit tests + coverage gate (70% floor)
radon cc src -s -n C          # complexity, C-grade and above
radon mi src -s               # maintainability index

# CI-only (not pre-commit — slower):
bandit -r src
pip-audit

# Release-only:
mutmut run
```

`pre-commit install` wires Ruff, mypy, and a fast test subset into git
commits.

## Architecture

Python backend, web frontend, one native window — no Electron, no Tauri,
no separate build toolchain. [pywebview](https://pywebview.flowrl.com/)
opens a native OS webview (WKWebView on macOS, WebView2 on Windows — no
bundled Chromium) and exposes a single Python object,
`api/bridge.py`'s `CavyApi`, to the page's JavaScript as
`window.pywebview.api`. That's the entire interface between the two
sides — every button click in the frontend is a call to one of `CavyApi`'s
methods, which take and return plain JSON.

```
src/eaal_platform/
  auth.py          password hashing, temporary passwords
  logging_setup.py structured (JSON-lines) logging to a rotating file
  mailer.py        sending email (SMTP) for first logins and reset codes
  db/              SQLAlchemy models, engine/WAL setup, accounts, courses (academics.py),
                   resources, approvals, the stage-unlock rule
  events/          background/batched append-only event logger
  sandbox/         subprocess code execution with timeouts + resource limits
  ai/              AIProvider interface; Ollama and the hosted providers (OpenAI, Claude,
                   Gemini, Grok, Groq); prompt assembly
  signals/         the EAAL signal-computation engine
  api/             CavyApi: the JS/Python bridge; "the app" (every operation lives here)
  client/          the app's side: talks to the local data or a server, lab-mode lock-down
  server/          the central FastAPI server, onboarding, and the admin panel (admin_ui/)
  web/             index.html + style.css + app.js: the whole frontend (no build step)
  app.py           wires it all together and opens the window
```

`db/`, `events/`, `sandbox/`, and `ai/` are UI-agnostic and were carried
over unchanged from an earlier PySide6-based UI — only the presentation
layer changed. The frontend is a single-page app: `app.js` swaps
`#app`'s contents between five screens (Labs, Stages, Workspace,
Submission, CIQ Score) and never reloads the page. The code editor
(Monaco, or a plain `<textarea>` fallback if `scripts/fetch_monaco.py`
was never run) lives in `web/editor.js` behind a
`{getValue, setValue, onChange, dispose}` interface so `app.js` never
needs to know which one it got.

A "Lab" is just a `Task` row with `Stage` children (see `db/models.py`);
a `Task` with no stages is Practice. `db/bootstrap.py` seeds two demo
Labs on first launch so there's something to click through.
