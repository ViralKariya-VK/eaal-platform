# CAVY

CAVY is a desktop platform (Windows + macOS) for coding education that
measures **CIQ (Cognitive Interaction Quotient)**: a framework of 3 pillars
(AI Utilization, Cognitive Engagement, Learning & Knowledge Development)
built from 14 behavioral signals, computed from a full event log of how a
student codes, asks AI for help, and responds to what it gives back.

Two students can submit identical correct code via completely different
learning processes — the platform's whole purpose is to distinguish those.

## Status

The full local workflow is in place: Labs -> Stages (Learning ->
Exploration -> Assessment, each with its own AI-assistance mode) ->
Workspace -> Submit -> CIQ Score panel, plus a standalone Practice mode.
Chat is wired to a real local Ollama model with the student's current code
and last error as context.

The signal-computation engine (`src/eaal_platform/signals/compute.py`)
computes all fourteen EAAL signals from the event log — behavioral
signals (S1.3–S1.5, S2.1–S2.4) via deterministic event analysis, code
adoption/modification (S1.3, S1.4, S2.3) by diffing AI-suggested code
against what the student kept, S1.6 (Adaptive AI Use) longitudinally
against the student's prior sessions, S3.2 by the final run's correctness,
S1.1/S1.2/S3.1 via structured low-temperature LLM rubric scoring against
the configured `AIProvider`, and S3.3/S3.4 by gating on a professor-authored
follow-on Transfer Task or Retention Check (the latter also requires a real
measured delay since the original session — see `db/models.py`'s
`AssessmentKind`). A signal still returns `None` with a stated reason
rather than a faked value whenever its specific evidence doesn't exist for
a given session (e.g. S3.3/S3.4 on an ordinary Lab session, or S1.5 before
a second AI interaction) — see `validation/` for the suite that checks
every signal's formula, gating logic, and (for the three LLM-rubric
signals) reliability/face-validity against the real configured model.

The app now has real local accounts and a Login screen with separate
Student and Teacher tabs (`auth.py` hashes passwords with
`hashlib.pbkdf2_hmac`; `Student` and `Professor` are deliberately separate
tables — see their docstrings in `db/models.py`). Logging in as a
professor reaches a distinct Professor Dashboard: a `Create New Session`
form that authors a real Lab (a `Task` with three `Stage`s, replacing
hand-edited demo content) and a `View Report` per lab showing submission
status and a provisional overall score per student.

Not yet built: a server or cross-device sync — every account and every
lab is local to the machine it was created on. This is deliberately
local-first for now — every table already carries a `synced_at` column so
a central-sync phase can be added later without a schema rewrite. Because
of this, the Professor Dashboard's reports only ever show students who
used *this* device; a real classroom rollout needs that sync phase first.

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
./run.sh
```

Windows (after activating your conda/venv environment in the same
terminal — `run.bat` doesn't activate one for you, since a conda install's
location isn't consistent across machines the way it is on the one dev
machine `run.sh`'s macOS branch was written for):

```bat
run.bat
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
  auth.py    password hashing for local Student/Professor accounts
  db/        SQLAlchemy models, engine/WAL setup, account creation +
             demo-seeding, the stage-unlock rule
  events/    background/batched append-only event logger
  sandbox/   subprocess code execution with timeouts + resource limits
  ai/        AIProvider interface, the Ollama adapter, prompt assembly
  signals/   the EAAL signal-computation engine (see Status above)
  api/       CavyApi — the JS/Python bridge; this is "the app" now
  web/       index.html + style.css + app.js — the entire frontend,
             a small hand-rolled router with no build step. A Login
             screen (Student/Teacher tabs) gates everything else; the
             sidebar and available screens differ by role from there.
  app.py     wires it all together and opens the window
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
