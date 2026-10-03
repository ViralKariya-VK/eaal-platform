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

- [x] **A1. Central server + admin panel** — built (see [DEMO_SETUP.md](DEMO_SETUP.md)).
      `python -m eaal_platform.server` runs one shared database behind an
      HTTP API; each PC's app connects to it (login screen → *Connect to a
      server*). Student code still runs on the student's PC; results are
      recorded on the server. The admin panel (`/admin`) has: overview and
      who's signed in, user list with password reset for **students and
      professors** (this closes D4), labs with archive/restore, a read-only
      database browser with linked records and CSV export, per-session
      detail (events, AI chat, runs, code, scores), the shared AI
      assistant's settings, and a full database backup. Verified with two
      independent app instances against one server; **not yet verified on
      the real multi-PC network or on Windows.**
      Known limits: plain HTTP (trusted LAN only); open sign-up; first
      visitor to `/admin` becomes admin; Groq key lives in server memory;
      admin panel doesn't auto-refresh; the old local `synced_at` columns
      are unused now.
- [x] **A2. Class-wide students roster** — the Students screen now reads from
      the server, so it lists every student on every PC.
- [x] **A3. Cross-device lab reports** — reports read from the server, so a
      professor sees students from every device.
- [ ] **A4. Server hardening before any real rollout** — HTTPS, restricting
      who can create teacher accounts, per-IP login throttling for the app
      (only admin login is throttled today), server-side audit log, running
      the server as a service/auto-start.
- [ ] **A5. Offline tolerance** — if the server is unreachable mid-session
      the student's Run/Submit fail with an error; nothing is queued.

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
- [x] **C4. Resources + classes** — professors share files, links and written
      instructions; attach them to labs; share with the whole class or chosen
      students. A student is in **one professor's class** (professor adds
      unassigned students from *My Class*; admin can assign/move anyone). Only
      the owner and the owner's students can see a resource or download its
      file (checked on the server, including direct file requests). Executable
      file types are refused, links must be http/https, 15 MB limit. Verified
      in a live two-professor / two-student run, including live updates.
      **Not done:** labs themselves are still visible to every student (only
      resources are scoped to classes); PDF preview inside the app (files open
      in the computer's own viewer); upload progress / chunking for large files.

## D. Accounts

- [x] **D1. Change password** — Profile screen; asks for the current
      password, enforces the 8-character minimum (now also enforced in the
      backend, not just the sign-up form).
- [x] **D2. Password reset by a professor** — Students screen → Reset
      password gives the student a one-time temporary password they must
      replace at next sign-in. Local accounts only, until sync exists.
- [x] **D3. Fuller Profile screen** — name, role, email, enrolment number.
- [x] **D4. Professor password reset** — done via the admin panel (Users →
      Reset password); the professor must choose a new password at next
      sign-in. Verified end to end against a running server.

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

- [x] **F1. Run the full flow on a real Windows machine** — done; the user
      ran the [WINDOWS_TESTING.md](WINDOWS_TESTING.md) checklist and reported everything working.
      (Per-item results weren't recorded, and the `dev` test suite wasn't
      necessarily run on Windows.)
- [ ] **F2. Windows sandbox limits** — only a timeout applies on Windows;
      add CPU/memory limits via Job Objects. — *M*
- [x] **F3. Bundle sandbox libraries** (numpy, pandas, matplotlib) — included in
      the installer; student code runs through the packaged app itself.

## G. Packaging & release

- [x] **G1. macOS installer** — `python scripts/build_release.py` on a Mac builds
      a self-contained `CAVY.app` and a `.dmg` (Apple Silicon). No Python or
      internet needed to install. The built app's student-code runner was
      smoke-tested (stdlib + numpy). **Not yet verified:** opening the window
      from the installed `.app` on a clean Mac, and a hosted server inside it.
      Unsigned: first launch needs right-click → Open.
- [ ] **G2. Windows installer** — recipe written (PyInstaller + Inno Setup,
      built by GitHub Actions: `.github/workflows/release.yml`). **Never built
      or run yet**; expect first-build fixes (pywebview/WebView2 packaging
      is the likely snag). Unsigned: SmartScreen warning.
- [x] **G3. Ship the Monaco editor inside the release** — the build fetches it
      with npm and bundles it (falls back to the plain editor if npm is missing).
- [x] **G4. CI** — `.github/workflows/ci.yml` runs ruff, mypy and pytest on
      Linux, macOS and Windows. Written but not run yet (needs a push).
- [ ] **G5. Code signing / notarization** (Apple Developer + Windows
      certificate; paid) so installs show no warnings. Intel Mac build.
- [ ] **G6. Auto-update** — installers are one-shot; new versions are re-installed by hand.

## H. Live classroom (server mode)

- [x] **H2. Live updates** — long-poll change feed: labs published by a
      professor appear on student screens, submissions appear on the
      professor's Home / Reports, without refreshing. Measured ~35 ms from
      publish to appearing, locally. Screens never redraw while a dialog is
      open or someone is typing, and never the code workspace.
- [x] **H3. Admin: live control** — signed-in list with address and activity,
      sign one/everyone out (the person sees why), edit credentials, reset
      passwords, disable/enable, delete, add accounts, who's working on what,
      lab progress counts, audit log, auto-refreshing overview.
- [x] **H4. Host the server from the installed app** (no terminal).
- [ ] **H5. Per-student view for the professor** of a single student's
      progress (reuses My Progress data).
