# Screen-by-screen QA audit

Date: 10 October 2026. Scope: the desktop app's web front end (`src/eaal_platform/web`), the browser admin
panel (`src/eaal_platform/server/admin_ui`) and the Python code behind them.

## How it was tested

- The real front end was served in a browser, driven by the real `ClientApi` over a seeded database
  (courses, professors, students with finished work, a lab, resources). The server-mode screens (first login,
  forgot password, sign-up on an approved email) were driven against a real classroom server.
- Playwright walked every screen below at five window sizes: **960x640** (the app's minimum), **1280x820**,
  **1920x1080**, **768x1024** and **390x844**. At each stop it ran automated checks: console errors, failed
  requests, horizontal overflow, clipped text, controls without accessible names, small targets, text contrast,
  duplicate ids, `lang`, visible `h1`.
- Forms and actions were exercised by hand-written probes (empty, invalid, boundary and hostile input, double
  clicks, Escape and Enter, markup in titles, names and chat) and the **result** was checked in the data, not
  only on screen.
- Project checks after the changes: `ruff` clean, `ruff format` clean, `mypy --strict` clean, **479 tests pass**
  (22 added), coverage 89%.

## Defects found, fixed and verified

| # | Severity | Where | Problem | Fix | Verified |
|---|---|---|---|---|---|
| 1 | High | Admin > Users | The **Change** link next to a student's teachers did nothing. `admin.js` contained a stale second copy of `showUsers`, `approvedForm` and `showImportReport`; the stale copy won and its handler sent the old single-professor request. | Removed the duplicate block; the teachers dialog is wired in the surviving function. | Dialog opens, two professors saved, shown in the list. |
| 2 | High | Create / edit lab, follow-ups | A stage time of -5, 0.5, 601, 99999 or "abc", a blank title or a 5,000-character title was accepted. | Server checks (`_clean_title`, `_clean_minutes`): title 1-200 characters on one line, time empty or 1-600 whole minutes. The form shows the message before sending. | 7 refused values, 7 accepted values, edit path and follow-ups covered by new tests; checked in the UI. |
| 3 | High | Create lab, follow-up, choose courses | A double click on **Create Lab** made two labs; same for follow-ups. | `runOnce()` keeps the button disabled until the request ends and shows errors from a failed request. | Double click now creates exactly one. |
| 4 | High | Workspace | **Reset** wiped the student's code with no warning and no undo. | A confirm dialog when the code differs from the starter code; none when nothing would be lost. | Pristine reset immediate; reset with work asks; Escape keeps the code. |
| 5 | Medium | Workspace | A "More actions (coming soon)" button that did nothing. | Removed (the feature does not exist). | Gone from the toolbar. |
| 6 | Medium | All dialogs | No dialog semantics, no Escape, no focus trap, focus not returned. | One shared enhancer for the app (and `modal()` in the admin panel): `role="dialog"`, `aria-modal`, labelled by its heading, focus moves in, Tab stays in, Escape cancels, focus goes back. The lab-mode warning stays un-dismissable. | Checked on confirm, delete, reset, teachers and focus-warning dialogs. |
| 7 | Medium | Login and sign-up | On a short or narrow window the card was centred so its top was cut off and unreachable; below 640 px the sign-up form ran off the screen. | The card scrolls from the top; the hero hides on narrow windows; field pairs stack. | Card top at 40 px at 960x640; no overflow at 390 px. |
| 8 | Medium | Workspace | At the minimum window the toolbar squeezed its labels ("Next: Exploratior") and below 900 px the layout broke. | Toolbar wraps; panels stack on narrow windows; the sidebar becomes a top strip; wide tables scroll sideways inside their card instead of crushing their text. | Screens re-captured at all sizes; no overflow. |
| 9 | Medium | My class | Each division's table chose its own column widths, so names, emails and buttons did not line up. | Fixed shared column widths. | Visual check at 1280 and 1920. |
| 10 | Medium | Contrast | Muted text was 3.5:1 on cream; teal on pale teal 4.3:1; editor status text 2.9:1. | Darker muted and teal tokens (5.4:1, 5.8:1), a lighter editor status colour (6:1). | Contrast check clean. |
| 11 | Medium | Keyboard | No visible focus ring on the dark bars; the fallback editor had `outline: none`. | One amber/teal `:focus-visible` style app-wide and in the admin panel. | Checked on login, workspace, dialogs. |
| 12 | Medium | Screen readers | Chat box, send button, code box, explanation box, file inputs and search had no accessible name; errors were not announced; the page had no `lang`; several screens had no `h1`. | Labels added; error lines get `role="alert"`, toasts `role="status"`; `lang="en"`; page titles are `h1` (the login hero text is no longer a second `h1`). | Automated check clean. |
| 13 | Low | Layout | The required star sat on its own line; forms stretched to 1,800 px at 1920; a long unbroken word spilled out of the chat; admin Database page overflowed on narrow screens. | Inline star, form width cap, `overflow-wrap`, `min-width: 0` and scroll containers. | Re-captured. |
| 14 | Low | Workspace timer | It counted by subtracting 1 each second, which drifts if the window is throttled, and showed nothing at zero. | Counts to a fixed end time; at zero the label reads "Time is up". | Waited out a 1-minute lab. |
| 15 | Low | Wording | A blank approved email said "“” isn't a valid email address". The reset-code box had no `autocomplete`. | "Enter an email address."; `autocomplete="one-time-code"`. | New test. |
| 16 | Low | Small targets | Text links and some buttons were 14-25 px tall. | Padding grows the hit area without moving the layout; now 26-32 px. | Automated check. |
| 17 | Low | Other | Favicon request 404 in a browser; no reduced-motion support. | Empty favicon link; `prefers-reduced-motion` rules. | Console clean. |

## Behaviour checked and found correct (no change needed)

Wrong/empty login, Enter to submit, student account used on the teacher tab, duplicate and invalid sign-up,
password change (wrong current, mismatch, short), AI key check with a bad key, provider switch, markup in lab
titles, topics, descriptions and chat (nothing executed), empty and very long chat messages, an infinite loop
in student code (stopped by the time limit), End Session, Back to Lab and resume, stage Back/Next, submit
confirmation and the concept check, the lab-mode warning then automatic submission on the second departure,
archive and restore, report export, follow-up create and edit, link validation (only http/https), resource
file/link/instructions, remove/reset-password confirmations, admin sign-in, approved emails (empty, invalid,
duplicate), courses (empty, 0 and 99 years), lab-mode switch persisting across a reload, email settings
validation, first login and forgot-password validation, unreachable-server message.

## Screen checklist

| Area | Screens and dialogs | Status |
|---|---|---|
| Auth | login (both tabs), create account, connect to server, host a server, forgot password (2 steps), first login | Tested; forgot-password and first login against a real server |
| Student | home, lab stages, lab-mode rules, workspace (stage 1, after run, stage 2, stage 3 restricted), submit confirm, explanation, submitted, CIQ score, my progress, practice, resources, profile | Tested at 5 sizes |
| Teacher | home, my labs, create lab, AI draft, edit lab, my class, choose courses, student progress, student work, reports, lab report, add follow-up, resources and add-resource dialog, profile | Tested at 5 sizes |
| Admin | sign-in, overview, approved emails, users (+ teachers, add account), courses and classes (+ add), labs, resources, database, audit log, AI assistant, email, backup | Tested at 3 sizes; sign-in, users, approved emails, courses, lab mode and email settings also by hand |

## Not covered or only partly verified

- **Native window behaviour**: real full-screen and key lock, the operating system's Save As and file pickers, and
  Windows WebView2 rendering. The harness runs in a browser, so these are unverified.
- **Email delivery**: first-login and reset emails could not be sent from here (no mail account). The screens and
  validation were tested; a real code was not received.
- **Real AI providers**: the assistant was scripted; only the "key rejected" path was exercised against a live service.
- **Screen readers**: names, roles and live regions are in place and checked automatically, but nobody listened
  with VoiceOver or NVDA.
- **Narrow windows**: the desktop app cannot be made smaller than 960x640, so the 768 and 390 layouts matter
  mainly for the browser admin panel; they were improved but are not a supported size for the app.
- The remaining small targets (26-30 px: the CIQ pill, "View code" links, the chat tabs, admin row buttons) meet
  the WCAG 2.2 minimum of 24 px but not the more generous 32 px used as the audit threshold.
- There are no automated JavaScript tests in the repository; the front-end changes were verified by the browser
  runs above, the Python changes by the unit tests.
