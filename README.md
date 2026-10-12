<h1 align="center">🧠 CAVY</h1>
<p align="center"><b>The Cognitive Interaction Quotient.</b><br>
Same code. Different thinking. Now you can tell the difference.</p>

<p align="center">
  <a href="https://github.com/ViralKariya-VK/eaal-platform/releases"><img alt="Download" src="https://img.shields.io/badge/download-macOS%20%2B%20Windows-1b2c45?style=for-the-badge"></a>
  <a href="https://www.figma.com/design/1rHhK4KU4ndumQBopIkcT1/CAVY-Platform-Wireframe?node-id=0-1"><img alt="Figma" src="https://img.shields.io/badge/design-Figma-f2a541?style=for-the-badge"></a>
  <a href="docs/CAVY-Application-Walkthrough.pdf"><img alt="Walkthrough" src="https://img.shields.io/badge/tour-every%20screen-1f7a8c?style=for-the-badge"></a>
</p>

<p align="center"><img src="docs/img/student-workspace.jpg" alt="The CAVY coding workspace: a lab, an editor, a result and an AI assistant" width="880"></p>

---

## 🎬 The plot

Two students hand in the exact same, perfectly correct linked-list reversal.

One of them poked at it, asked the AI *why* the pointer kept getting lost, fixed it, ran it, and could explain it afterwards.
The other pasted the answer, hit submit, and went for chai. ☕

Your gradebook sees two identical green ticks. 🟢🟢

**CAVY sees the difference.** It lets students use an AI assistant (that is the whole point), quietly records
*how* they used it, and turns that trail into a score: the **Cognitive Interaction Quotient**.

> *In the age of AI, we are measuring the answers, but we have stopped measuring the thinker.*

It is a research instrument and a real, installable classroom platform at the same time. The research question
shaped the software, and the software exists so the question can finally be tested on real students.

## 🔭 The research bit (the serious part, we promise it is short)

CAVY builds on the **AIQ** framework for human-AI collaboration (Ganuthula and Balaraman, 2025) and on the
automation-bias and over-reliance literature (Alon-Barkat and Busuioc, 2023; Klingbeil et al., 2024; Glickman and
Sharot, 2025). It moves measurement from **outcomes** to **process**:

- Students solve labs where AI is available but optional.
- Every prompt, reply, edit, run and submission is logged as an immutable, timestamped event.
- The scores are *pure functions of that log*, so they are reproducible and checkable.

The numbers that make the case: 88% of UK undergraduates used generative AI for assessments in 2025 (up from 53% a
year earlier, HEPI) and 84% of developers use or plan to use AI tools (Stack Overflow, 2025). Detectors judge text,
proctoring treats AI as a crime, coding platforms grade output, and LMS tools count clicks. Nobody measures the collaboration.

**Our two hypotheses**

1. People with generative AI finish faster but show lower cognitive independence than people without it.
2. Feedback on AI reliance changes cognitive independence over time.

### 14 signals, 3 pillars

Every signal says **"none, and why"** when it has no evidence. It never invents a number.

| Pillar | Signals |
|---|---|
| 🤝 **P1 · AI utilization**: how well is AI used? | Help-seeking calibration · Student grounding · Response utilization · Modification and verification · Follow-up engagement · Adaptive AI use |
| 🧩 **P2 · Cognitive engagement**: how actively do they think? | Independent initiation · Reasoning continuity · Problem-solving agency · Evidence-based error recovery |
| 🌱 **P3 · Learning and knowledge development**: what sticks? | Conceptual understanding · Knowledge application · Knowledge transfer · Retention and recall |

Most are deterministic (did you try before you asked? did you edit what the AI gave you, then run it?). Three are scored
by a language model against a written rubric. The overall CIQ is a provisional equal-weight average, honestly labelled as such.

**Receipts.** A separate suite ([`validation/`](validation/)) found 53/53 known-answer trials exact, 10/10 "no evidence"
cases returning *none*, 88/88 recomputations bit-identical, and the three model-scored signals agreeing with hand-labelled
examples (Spearman r = 0.84 to 0.87). What it does *not* prove is that the signals predict real learning. That needs a study
with students, which is the next step ([report](validation/report/VALIDATION_REPORT.pdf)).

## 👀 Peek inside

<table>
<tr>
<td width="50%"><img src="docs/img/ciq-score.jpg" alt="The CIQ score page"><br><sub>🎓 <b>Students</b> see their CIQ and the evidence behind every signal.</sub></td>
<td width="50%"><img src="docs/img/teacher-report.jpg" alt="A lab report"><br><sub>🧑‍🏫 <b>Teachers</b> see who submitted, the scores, and can open anyone's real work.</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/img/teacher-class.jpg" alt="A class by division and batch"><br><sub>🏫 <b>Classes</b> follow programme, year, division and batch, like a real college.</sub></td>
<td width="50%"><img src="docs/img/admin-overview.jpg" alt="The admin overview"><br><sub>🛠️ <b>Admins</b> run the whole server from a browser tab.</sub></td>
</tr>
</table>

Every screen is in the [walkthrough PDF](docs/CAVY-Application-Walkthrough.pdf) (demo data, scripted AI), and it all started as
[Figma wireframes](https://www.figma.com/design/1rHhK4KU4ndumQBopIkcT1/CAVY-Platform-Wireframe?node-id=0-1).

## 🎒 What it does

- 🧪 **Labs with three stages.** Learning, Exploration, Assessment, each with its own AI-assistance level. Back and Next
  between stages, one submit at the end, and an "explain it in your own words" check before it closes.
- 🔒 **Lab mode.** Full screen, key lock, one warning if you wander off, automatic submit the second time, and pasted text
  is recorded (and compared with what the AI wrote). The admin can switch it off.
- 🎓 **It speaks college.** Programmes (MSc and BSc in Data Science, Statistics and Data Science, Applied Statistics and
  Analytics), **SAP IDs**, admission year, **divisions, batches and roll numbers**. The year of study and the semester follow
  the calendar (the academic year starts on 1 July), so everyone moves up by themselves. Load a whole class from one
  spreadsheet ([how](docs/COLLEGE_SETUP.md)).
- 🔑 **Sign in with a SAP ID** (or an email), choose your own password at first login, and never retype your course or batch.
- 🤖 **Bring your own AI.** A local model (Ollama) works offline; OpenAI, Claude, Gemini, Grok and Groq work with a key that
  lives only in the student's computer memory.
- 🏠 **One class server.** A professor and a room full of students share live data on one network, with a browser admin panel.
- 🔔 **It tells you about updates.** A small notice when a newer version exists, and a server and its apps must run
  **exactly the same version** or they politely refuse to talk ([how](docs/RELEASING.md)).
- 📦 **Real installers** for macOS and Windows, built and smoke-tested by GitHub Actions.

## 🚀 Try it in 60 seconds

**Just use it.** Grab the installer for your system from [Releases](https://github.com/ViralKariya-VK/eaal-platform/releases).
It is not code-signed yet, so your computer will frown once on first launch (the release notes say how to tell it you trust it).
For a classroom: one computer hosts the server, everyone else types its address. Details in [docs/DEMO_SETUP.md](docs/DEMO_SETUP.md).

**Run it from source.**

```bash
conda create -n eaal-platform python=3.11
conda activate eaal-platform
python -m pip install -r requirements.lock     # the exact versions the tests run on
python -m pip install -e . --no-deps
python -m eaal_platform.app                     # the desktop app
python -m eaal_platform.server                  # the class server (admin panel at /admin)
```

Want the local AI? `ollama serve`, then `ollama pull qwen3:8b`. Want the nice editor? `python scripts/fetch_monaco.py`
(it politely falls back to a plain box without it). Lost your admin password?
`get-admin-password.command` (Mac) or `get-admin-password.bat` (Windows) hands you a new one.

## 🧱 Under the hood

Python on the inside, web on the outside, one native window. [pywebview](https://pywebview.flowrl.com/) shows the HTML front
end in the system's web view and gives it a single Python object, `CavyApi`. FastAPI runs the class server and the admin
panel; SQLite (WAL) with SQLAlchemy 2 keeps an append-only event log.

```
src/eaal_platform/
  api/       CavyApi: the one door every screen walks through
  client/    local data or a server, lab-mode lock-down
  server/    FastAPI class server, onboarding by email, the admin panel
  db/        models, accounts, programmes, the academic calendar, the student-list importer
  signals/   the 14 CIQ signals, computed from the event log
  events/    background, batched event logger (typing never waits for the disk)
  sandbox/   student code runs in its own process with a time limit
  ai/        provider interface: Ollama and the hosted providers
  web/       the front end (no build step, no framework drama)
validation/  the research: do the signals measure what they claim?
```

Why each choice (and the alternatives we weighed) is in the [software report](docs/Audit%20Report.pdf).

## 🧾 The receipts (quality)

| | |
|---|---|
| ✅ Tests | 550+ passing, 89% line and branch coverage |
| 🧹 Static checks | Ruff 0, mypy strict 0, Bandit 0, pip-audit clean on 151 pinned packages |
| 🧬 Mutation testing | 83% of mutants killed in the scoring engine |
| ⚡ Speed | 30 students at once: about 800 calls a second, none failed; 100 students: none failed ([details](docs/PERFORMANCE.md)) |
| 🔍 QA | a screen-by-screen audit at five window sizes, with fixes ([audit](docs/QA_AUDIT.md)) |

```bash
ruff check . && ruff format --check . && mypy src && pytest
python scripts/benchmark.py --students 30
python scripts/import_roster.py data/Students.xlsx --dry-run     # check a class list
```

`pre-commit install` runs Ruff, mypy and a fast test subset on every commit.

## 🐛 Known gremlins (we would rather tell you)

- Traffic on the class network is plain HTTP, and the installers are not code-signed.
- `api/bridge.py` is one very large class. It is next in line for a haircut.
- Lab mode cannot block every escape hatch (Ctrl+Alt+Del says hello), and Windows needs more real-machine testing.
- The signals are validated for correctness and repeatability on constructed cases, not yet against real learning outcomes.
  One model-scored signal once gave full marks to a line-by-line restatement of the code. We reported it instead of hiding it.

## 🗺️ What is next

Subjects per semester and **teaching assignments** (which professor takes which batch for which practical), a lab given to
both batches at once with a time for each, a "start the new academic year" button, HTTPS, signed installers, a pilot in one
real class, and validating the signals against real outcomes. See [docs/ROADMAP.md](docs/ROADMAP.md).

## 👯 The squad

**Group 04, NMIMS:** Viral Kariya · Vanshika Ahuja · Yashika Parmar · Carol Maria

Shipping a new version? [docs/RELEASING.md](docs/RELEASING.md) is the short recipe.

## 📚 Standing on the shoulders of

- Ganuthula, V. R. R., and Balaraman, K. K. (2025). Artificial intelligence quotient framework for measuring human
  collaboration with artificial intelligence. *Discover Artificial Intelligence, 5*, 268.
- Klingbeil, A., Grützner, C., and Schreck, P. (2024). Trust and reliance on AI: an experimental study on the extent and
  costs of over-reliance on AI. *Computers in Human Behavior, 160*, 108352.
- Alon-Barkat, S., and Busuioc, M. (2023). Human-AI interactions in public sector decision making: "automation bias" and
  "selective adherence" to algorithmic advice. *Journal of Public Administration Research and Theory.*
- Glickman, M., and Sharot, T. (2025). How human-AI feedback loops alter human perceptual, emotional and social judgements.
  *Nature Human Behaviour, 9*(2), 345-359.
- Zheng, J., et al. (2025). Do students rely on AI? Analysis of student-ChatGPT conversations from a field study.
- Higher Education Policy Institute (2025). *Student Generative AI Survey 2025.*
- Stack Overflow (2025). *Developer Survey 2025.*
