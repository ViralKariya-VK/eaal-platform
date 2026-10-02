# CAVY — Pending Features

Tick each box as it ships. Sizes are rough estimates.

## Fixed along the way

- [x] Follow-up assessments (Transfer / Retention) were listed as ordinary
      labs for both students and professors, and inflated the dashboard's
      lab count. They now only appear under their parent lab.
- [x] Older database files are upgraded in place on launch (new
      `archived_at` column); verified against a copy of the real database.

- [x] App startup skipped the login screen and auto-logged in with a
      hardcoded account that only existed on one machine (crashed
      everywhere else). Startup now shows the login screen.

## 0. Decision (blocks section A and parts of F)

- [x] **Delivery model: shared lab computers** — one install per machine,
      several students signing in on it (decided). Consequences:
      - F1/F2 (Windows run + sandbox limits) are critical, not optional.
      - Sync (A) is lower priority: results stay on the lab machine. The
        professor still needs a way to collect results from several
        machines (export/import stopgap is probably enough to start).
      - B3 (keychain) is risky here: a saved key would be shared by every
        student on that OS account. Keep the key memory-only, or have the
        lab admin configure one key per machine.
      - New: students on one machine must not see each other's work or
        code files (sandbox working dirs, DB access, logs). To be audited.
      - New: a "signed in" session must end cleanly (sign-out clears AI
        key, chat history and open editor state) so the next student
        starts fresh.

## A. Classroom / multi-user

- [ ] **A1. Central data sync + admin panel** — a small server that each app syncs to (the admin panel for resetting professor passwords, D4, lives here), so
      a professor sees students from every device. (Stopgap option: student
      exports a result file, professor imports it.) — *L, 1–2 weeks (stopgap 2–3 days)*
- [ ] **A2. Class-wide students roster** — the Students screen now lists
      accounts on *this* computer (built for D2); a roster across devices
      needs A1. — *S*
- [ ] **A3. Cross-device lab reports** — reports today only show students
      who used the professor's own machine. Needs A1. — *S*

## B. AI assistant

- [x] **B1. AI provider settings** — Profile screen lets you switch between
      local Ollama and Groq; a Groq key is tested against Groq before it's
      accepted and is kept in memory only (re-entered each launch).
- [x] **B2. First-run AI setup** — after login, if the assistant isn't usable,
      a dialog explains why and offers Groq (paste a key) or Ollama (install
      steps + "Check again"). Shown once per launch; the chat panel also has a
      "Set up" link. Also fixes a real gap: Ollama running *without the model
      installed* used to be reported as "ready" (then every chat message
      failed); it now says so and shows the `ollama pull` command.
- [ ] **B3. Remember the Groq key** — the key is kept in memory only, so
      Groq-only users must re-enter it every time they open CAVY. Proper fix
      is opt-in storage in the OS keychain (macOS Keychain / Windows
      Credential Locker), not a plain file or the database. Needs the
      `keyring` package and testing on Windows. — *S-M*

## H. Student learning over time

- [x] **H1. My Progress dashboard (student)** — new sidebar item. Totals
      (sessions, labs, practice, time, average CIQ), CIQ-over-time chart,
      per-pillar latest/average/trend, strongest areas and room to grow
      (by signal), and a session history table linking to each session's
      CIQ breakdown. Sessions never scored before are scored on first open
      (then saved), so the first open can be slow. CIQ is the provisional
      equal-weight average. **Not done:** per-lab view, comparing a lab's
      Learning→Assessment stages, goals/streaks, a professor-side view of a
      single student's progress (needs the same data; small follow-up).

## C. Professor tools

- [x] **C1. Edit a lab** — My Labs → Edit. Title, cohort, description,
      difficulty and stage durations stay editable; a stage's AI mode locks
      once any student has started it (changing it later would change what
      their recorded evidence means).
- [x] **C2. Archive a lab** — hides it from students and blocks new work,
      keeps all submissions; "Show archived labs" + Restore on My Labs.
      (Archive rather than hard-delete, so student history is never lost.)
- [x] **C3. Export a lab report as CSV** — the button already existed but
      did nothing in the real desktop window (pywebview blocks browser-style
      downloads). Now saves through a native Save dialog. PDF export not done.
- [ ] **C4. Resources** — attach reference material (datasets, docs) to a
      lab; the Resources screen is currently a static empty message. — *M*

## D. Accounts

- [x] **D1. Change password** — Profile screen; asks for the current
      password, enforces the 8-character minimum (now also enforced in the
      backend, not just the sign-up form).
- [x] **D2. Password reset by a professor** — Students screen → Reset
      password gives the student a one-time temporary password they must
      replace at next sign-in. Local accounts only, until sync exists.
- [x] **D3. Fuller Profile screen** — name, role, email, enrolment number.
- [ ] **D4. Professor password reset** — *deferred, depends on A1.* Decided:
      an admin panel on the future server will reset professors' passwords.
      Until then a locked-out professor has no recovery path.

## E. Scoring quality

- [x] **E1. S3.1 line-by-line restatement scored as full understanding** —
      partly fixed. New rubric asks the model three yes/no judgements (does
      it explain *why*, does it only narrate steps, does it state something
      wrong) and the code caps the score to the rubric's own bands when the
      model says "narration only" (max 0.4) or "factually wrong" (max 0.1).
      Tested on 24 labelled explanations over three sets (6 original, 9
      held-out, 9 fresh): 23/24 land in their expected band; pooled Spearman
      r = 0.94 (was 0.85 on the original 6 alone). **Still fails:** an
      explanation that *names* the key idea without justifying it (e.g. "loop
      up to the square root" with no reason) scores 0.95. That looks like the
      ceiling of an 8B local model; a larger model (e.g. via Groq) is the
      likely fix. Also: the score doesn't separate "rigorous" from "correct
      but brief" (both ~0.95). Re-run: `python -m validation.run_llm_reliability --concept-only`.
- [ ] **E2. Separate S1.1 and S1.2** — they correlate at r = 0.97 from one
      shared rubric call. — *M*

## F. Platform & sandbox

- [ ] **F1. Run the full flow on a real Windows machine** — Windows support
      has only been verified by reading the code. — *unknown*
- [ ] **F2. Windows sandbox limits** — only a timeout applies on Windows;
      add CPU/memory limits via Job Objects. — *M*
- [ ] **F3. Bundle sandbox libraries** (numpy, pandas, matplotlib) into the
      release so labs that import them work out of the box. — *S*

## G. Packaging & release

- [ ] **G1. macOS installer** (PyInstaller/py2app) that bundles Python and
      dependencies; `CAVY.app` today embeds this machine's interpreter
      path. — *M*
- [ ] **G2. Windows installer.** — *M*
- [ ] **G3. Ship the Monaco editor inside the release** (today it needs
      `npm` via `scripts/fetch_monaco.py`). — *S*
- [ ] **G4. CI** — GitHub Actions running ruff, mypy and pytest on every
      push (no `.github/` yet). — *S*
