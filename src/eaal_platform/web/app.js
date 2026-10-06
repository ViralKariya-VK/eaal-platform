// CAVY frontend: a small hand-rolled router over window.pywebview.api.
//
// No framework and no build step, deliberately — this app has a handful of
// screens and one shared editor widget; a router/vdom library would add a
// build pipeline for no real benefit at this size. Each screen is a
// render*() function that sets #app's innerHTML and wires event handlers.
// The header and sidebar are rendered once (see init()) and never rebuilt
// by setScreen() — only the active nav highlight changes per screen.

const CODE_EDIT_DEBOUNCE_MS = 800;

const app = document.getElementById("app");
let currentEditor = null;
let debounceTimer = null;
let workspaceTimerId = null;
let workspaceFiles = {};
let activeFilename = "main.py";

const ICONS = {
  home: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9.5 10 3l7 6.5"/><path d="M5 8.5V17h10V8.5"/></svg>',
  practice: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7 6 3 10l4 4"/><path d="M13 6l4 4-4 4"/></svg>',
  resources: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M6 2.5h6l3 3V17a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 17V3a.5.5 0 0 1 .5-.5Z"/><path d="M8 9h5M8 12h5"/></svg>',
  profile: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="10" cy="7" r="3"/><path d="M4 17c1-3.5 4-5 6-5s5 1.5 6 5"/></svg>',
  back: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4 6 10l6 6"/></svg>',
  clock: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="7"/><path d="M10 6.5v3.7l2.6 1.6"/></svg>',
  person: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="7" r="2.6"/><path d="M4.5 16.5c1-3 3.5-4.3 5.5-4.3s4.5 1.3 5.5 4.3"/></svg>',
  check: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="8" fill="none"/><path d="M6.5 10.3l2.3 2.2L14 8"/></svg>',
  file: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M6 2.5h6l3 3V17a.5.5 0 0 1-.5.5h-9A.5.5 0 0 1 5 17V3a.5.5 0 0 1 .5-.5Z"/></svg>',
  arrowRight: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10h12M11 5l5 5-5 5"/></svg>',
  send: '<svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 10l16-7-6 16-3-6-7-3Z"/></svg>',
  sparkle: '<svg viewBox="0 0 20 20" fill="currentColor"><path d="M10 2l1.6 4.9L16.5 8.5l-4.9 1.6L10 15l-1.6-4.9L3.5 8.5l4.9-1.6Z"/></svg>',
  lock: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><rect x="5" y="9" width="10" height="7" rx="1.5"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/></svg>',
  chart: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 16V9M10 16V4M16 16v-6"/></svg>',
  plus: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M10 4v12M4 10h12"/></svg>',
  download: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3v10M6 9l4 4 4-4"/><path d="M4 16h12"/></svg>',
  layers: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M10 3 3 7l7 4 7-4-7-4Z"/><path d="M3 11l7 4 7-4"/></svg>',
  people: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="7" cy="7" r="2.4"/><path d="M2.5 16c.7-2.6 2.4-4 4.5-4s3.8 1.4 4.5 4"/><circle cx="14" cy="7.5" r="2"/><path d="M12.5 12.3c1.8.2 3 1.4 3.5 3.7"/></svg>',
  refresh: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M16 10a6 6 0 1 1-2-4.5"/><path d="M16 3v3.5h-3.5"/></svg>',
  save: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M4 3h9l3 3v11a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 4 17V3Z"/><path d="M7 3v4h6V3M6.5 12h7v5h-7z"/></svg>',
  moreHoriz: '<svg viewBox="0 0 20 20" fill="currentColor"><circle cx="4" cy="10" r="1.4"/><circle cx="10" cy="10" r="1.4"/><circle cx="16" cy="10" r="1.4"/></svg>',
  expand: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7 3H3v4M13 3h4v4M3 13v4h4M17 13v4h-4"/></svg>',
};

function icon(name) {
  return `<span class="icon">${ICONS[name] || ""}</span>`;
}

function api() {
  return window.pywebview.api;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

// Small, deliberately limited Markdown renderer for AI chat replies — just
// enough for what local models actually produce (fenced code, inline code,
// bold/italic, headings, bullet lists). Operates on already-escaped text,
// so the only "tags" it ever introduces are the ones it inserts itself;
// nothing in the source text can inject markup.
function renderInlineMarkdown(escapedText) {
  return escapedText
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");
}

function renderMarkdown(rawText) {
  const lines = escapeHtml(rawText).split("\n");
  let html = "";
  let inCode = false;
  let codeLines = [];
  let listItems = [];

  const flushList = () => {
    if (listItems.length) {
      html += `<ul>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`;
      listItems = [];
    }
  };

  for (const line of lines) {
    if (/^```/.test(line)) {
      if (inCode) {
        html += `<pre class="md-code">${codeLines.join("\n")}</pre>`;
        codeLines = [];
      } else {
        flushList();
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) {
      flushList();
      html += `<p class="md-heading">${renderInlineMarkdown(heading[1])}</p>`;
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      listItems.push(bullet[1]);
      continue;
    }
    flushList();
    if (line.trim() !== "") {
      html += `<p>${renderInlineMarkdown(line)}</p>`;
    }
  }
  flushList();
  if (inCode && codeLines.length) {
    html += `<pre class="md-code">${codeLines.join("\n")}</pre>`;
  }
  return html;
}

// Screens that can refresh themselves when the server reports news set this
// just before calling setScreen(); every other screen (above all the code
// workspace, which must never be redrawn under someone typing) gets none.
let nextRefresh = null;
let currentRefresh = null;
let pendingScroll = null;

function setScreen(html, activeNav, options) {
  if (labMode && !(options && options.keepSidebar)) stopLabMode();
  currentRefresh = nextRefresh;
  nextRefresh = null;
  if (currentEditor) {
    currentEditor.dispose();
    currentEditor = null;
  }
  clearTimeout(debounceTimer);
  clearInterval(workspaceTimerId);
  workspaceTimerId = null;
  document.getElementById("headerExtra").innerHTML = "";
  app.innerHTML = html;
  if (pendingScroll !== null) {
    app.scrollTop = pendingScroll;
    pendingScroll = null;
  }
  if (!options || !options.keepSidebar) {
    renderSidebar();
  }
  setActiveNav(activeNav);
}

// -- Auth: login / create account -----------------------------------------

let loginRole = "student";
let serverMode = false; // true when this app is connected to a CAVY server

// pywebview rejects with an Error-like object when Python raises.
function errorText(err) {
  if (!err) return "Something went wrong.";
  const text = typeof err === "string" ? err : err.message || "";
  // Some bridges prefix the Python exception class ("PermissionError: ...").
  return text.replace(/^[A-Za-z]+Error:\s*/, "") || "Something went wrong.";
}

window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  if (isSignedOutError(event.reason)) handleSignedOut(event.reason);
  else showToast(errorText(event.reason));
});


function authHero() {
  return `
    <div class="auth-hero">
      <div>
        <span class="brand-mark">CAVY</span>
        <div class="tagline">Cognitive Interaction Quotient</div>
        <h1>Collaboration has a score now.</h1>
        <p>CAVY doesn't just grade what you submit — it measures your CIQ: how well you question, edit, and reason alongside AI, session by session.</p>
      </div>
      <div class="auth-stats">
        <div><strong>3</strong><span>Pillars measured</span></div>
        <div><strong>14</strong><span>Behavioral signals</span></div>
      </div>
    </div>`;
}

// -- Server connection (login screen) ----------------------------------------

function renderServerLine() {
  const line = document.getElementById("serverLine");
  if (!line) return;
  Promise.all([api().get_server_settings(), api().get_hosting()]).then(([settings, hosting]) => {
    serverMode = settings.mode === "server";
    const text = serverMode
      ? `Connected to <strong>${escapeHtml(settings.url)}</strong>`
      : "Working on this computer only";
    const hostText = hosting.running
      ? `Hosting a server &middot; <button class="auth-link" id="hostButton">Manage</button>`
      : `<button class="auth-link" id="hostButton">Host a server on this computer</button>`;
    line.innerHTML = `${text} &middot; <button class="auth-link" id="serverChangeButton">${serverMode ? "Change server" : "Connect to a server"}</button><br />${hostText}`;
    document
      .getElementById("serverChangeButton")
      .addEventListener("click", () => showServerModal(settings));
    document.getElementById("hostButton").addEventListener("click", () => showHostModal(hosting));
  });
}

function showHostModal(hosting) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const running = hosting.running;
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>${running ? "This computer is hosting CAVY" : "Host a classroom server"}</h2>
      ${
        running
          ? `<p class="muted">Other computers connect with this address (they type it into <em>Connect to a server</em> on their login screen):</p>
             <div class="temp-password">${hosting.addresses.map(escapeHtml).join("<br />")}</div>
             <p class="muted">Keep this computer on, on the same network, and keep CAVY open. Closing CAVY stops the server for everyone.</p>
             <p class="muted">Data is stored at <code>${escapeHtml(hosting.database)}</code>.</p>`
          : `<p class="muted">Run the classroom server on this computer. Students and teachers on other computers on the same Wi-Fi or network connect to it, and everything they do is stored here. You'll manage accounts and see live activity in the admin panel.</p>
             <p class="muted">Your computer may ask whether to allow CAVY to accept network connections. Choose <strong>Allow</strong> (on Windows, tick <em>Private networks</em>).</p>
             <label class="modal-field">Port <input type="text" id="hostPort" value="8000" /></label>`
      }
      <p class="auth-error" id="hostError">${escapeHtml(hosting.error || "")}</p>
      <div class="modal-actions">
        <button class="ghost" id="hostClose">Close</button>
        ${
          running
            ? `<button class="ghost" id="hostStop">Stop server</button><button class="primary" id="hostAdmin">Open admin panel</button>`
            : `<button class="primary" id="hostStart">Start server</button>`
        }
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const done = () => {
    overlay.remove();
    renderServerLine();
  };
  document.getElementById("hostClose").addEventListener("click", done);
  const errorEl = document.getElementById("hostError");
  const start = document.getElementById("hostStart");
  if (start) {
    start.addEventListener("click", () => {
      start.disabled = true;
      start.textContent = "Starting…";
      api()
        .start_hosting(Number(document.getElementById("hostPort").value) || 8000)
        .then((result) => {
          overlay.remove();
          if (!result.ok) {
            showHostModal({ ...result, running: false });
            return;
          }
          showHostModal(result);
          renderServerLine();
        })
        .catch((err) => {
          start.disabled = false;
          start.textContent = "Start server";
          errorEl.textContent = errorText(err);
        });
    });
  }
  const stop = document.getElementById("hostStop");
  if (stop) {
    stop.addEventListener("click", () => {
      api()
        .stop_hosting()
        .then(() => {
          overlay.remove();
          renderServerLine();
          showToast("Server stopped.");
        });
    });
  }
  const admin = document.getElementById("hostAdmin");
  if (admin) {
    admin.addEventListener("click", () => {
      api()
        .open_admin_panel()
        .then((result) => {
          if (!result.ok) errorEl.textContent = result.error;
        });
    });
  }
}

function showServerModal(settings) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>CAVY server</h2>
      <p class="muted">Enter the address your teacher or lab admin gave you, for example <code>192.168.1.20:8000</code>. Leave it empty to work on this computer only.</p>
      <label class="modal-field">Server address
        <input type="text" id="serverUrlInput" placeholder="192.168.1.20:8000" value="${escapeHtml(settings.url || "")}" />
      </label>
      <p class="auth-error" id="serverError"></p>
      <div class="modal-actions">
        <button class="ghost" id="serverCancel">Cancel</button>
        <button class="primary" id="serverSave">Connect</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const input = document.getElementById("serverUrlInput");
  input.focus();
  document.getElementById("serverCancel").addEventListener("click", () => overlay.remove());
  const save = () => {
    const button = document.getElementById("serverSave");
    button.disabled = true;
    button.textContent = "Connecting…";
    api()
      .set_server(input.value)
      .then((result) => {
        button.disabled = false;
        button.textContent = "Connect";
        if (!result.ok) {
          document.getElementById("serverError").textContent = result.error;
          return;
        }
        overlay.remove();
        renderServerLine();
        showToast(result.mode === "server" ? "Connected to the server." : "Using this computer only.");
      })
      .catch((err) => {
        button.disabled = false;
        button.textContent = "Connect";
        document.getElementById("serverError").textContent = errorText(err);
      });
  };
  document.getElementById("serverSave").addEventListener("click", save);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") save();
  });
}

function showLogin() {
  stopLabMode();
  stopLiveUpdates();
  document.getElementById("root").classList.add("auth-mode");
  document.getElementById("authScreen").innerHTML = `
    ${authHero()}
    <div class="auth-panel">
      <div class="auth-card">
        <div class="icon-tile">${icon("sparkle")}</div>
        <h2>Welcome back</h2>
        <p class="subtitle">Sign in to pick up your session where you left it.</p>
        ${loginNotice ? `<p class="notice notice-warn">${escapeHtml(loginNotice)}</p>` : ""}
        <div class="role-toggle">
          <button class="${loginRole === "student" ? "active" : ""}" data-role="student">Student</button>
          <button class="${loginRole === "professor" ? "active" : ""}" data-role="professor">Teacher</button>
        </div>
        <div class="auth-form">
          <label>Email <input type="text" id="loginEmail" placeholder="you@school.edu" /></label>
          <label>Password <input type="password" id="loginPassword" placeholder="••••••••" /></label>
          <p class="auth-error" id="loginError"></p>
          <button class="primary auth-submit" id="loginButton">Log in</button>
        </div>
        <div class="auth-footer">
          New to CAVY? <button class="auth-link" id="goToCreateAccount">Create an account</button>
        </div>
        <div class="server-line" id="serverLine"></div>
      </div>
    </div>
  `;

  document.querySelectorAll(".role-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      loginRole = btn.dataset.role;
      showLogin();
    });
  });
  renderServerLine();

  document.getElementById("goToCreateAccount").addEventListener("click", showCreateAccount);

  const doLogin = () => {
    const email = document.getElementById("loginEmail").value.trim();
    const password = document.getElementById("loginPassword").value;
    const errorEl = document.getElementById("loginError");
    if (!email || !password) {
      errorEl.textContent = "Enter your email and password.";
      return;
    }
    api()
      .login(loginRole, email, password)
      .then((result) => {
        if (result.ok) {
          enterApp(result.role, result.name, result.must_change_password);
        } else {
          errorEl.textContent = result.error || "Login failed.";
        }
      })
      .catch((err) => {
        errorEl.textContent = errorText(err);
      });
  };
  document.getElementById("loginButton").addEventListener("click", doLogin);
  document.getElementById("loginPassword").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });
}

// -- Course / year / division / batch dropdowns (sign-up, new lab, class groups) ------------
//
// An administrator maintains the lists; a course sets how many years it has, and
// the division and batch lists are the ones allowed for that course.

function cohortSelectOptions(items, label) {
  return `<option value="">${escapeHtml(label)}</option>` +
    items.map((i) => `<option value="${escapeHtml(String(i.value))}">${escapeHtml(i.label)}</option>`).join("");
}

// Fills `<prefix>Year / Division / Batch` from the course chosen in `<prefix>Course`.
function wireCohortSelects(prefix, courses, { current = {}, anyLabels = false, onChange = null } = {}) {
  const el = (name) => document.getElementById(`${prefix}${name}`);
  const sync = (keep) => {
    const course = courses.find((c) => String(c.id) === el("Course").value);
    const fill = (name, items, label, selected, hideWhenEmpty) => {
      const select = el(name);
      select.innerHTML = cohortSelectOptions(items, label);
      select.disabled = !course;
      const box = select.closest("label");
      if (box && hideWhenEmpty) box.hidden = !!course && items.length === 0;
      if (selected !== null && selected !== undefined && selected !== "") select.value = String(selected);
    };
    fill("Year", course ? course.year_options : [], anyLabels ? "Any year" : "Choose year", keep ? current.year : null, false);
    fill("Division", course ? course.divisions.map((d) => ({ value: d.name, label: d.name })) : [], anyLabels ? "Any division" : "Choose division", keep ? current.division : null, true);
    fill("Batch", course ? course.batches.map((b) => ({ value: b.name, label: b.name })) : [], anyLabels ? "Any batch" : "Choose batch", keep ? current.batch : null, true);
    if (onChange) onChange();
  };
  el("Course").addEventListener("change", () => sync(false));
  ["Year", "Division", "Batch"].forEach((name) => el(name).addEventListener("change", () => onChange && onChange()));
  if (current.course_id) el("Course").value = String(current.course_id);
  sync(true);
}

function readCohort(prefix) {
  const value = (name) => document.getElementById(`${prefix}${name}`).value;
  return {
    course_id: value("Course") ? Number(value("Course")) : null,
    year: value("Year") ? Number(value("Year")) : null,
    division: value("Division") || null,
    batch: value("Batch") || null,
  };
}

function showCreateAccount() {
  document.getElementById("root").classList.add("auth-mode");
  document.getElementById("authScreen").innerHTML = `
    ${authHero()}
    <div class="auth-panel">
      <div class="auth-card">
        <div class="icon-tile">${icon("sparkle")}</div>
        <h2>Create an account</h2>
        <p class="subtitle">Set up your CAVY login.</p>
        <div class="role-toggle">
          <button class="${loginRole === "student" ? "active" : ""}" data-role="student">Student</button>
          <button class="${loginRole === "professor" ? "active" : ""}" data-role="professor">Teacher</button>
        </div>
        ${
          serverMode
            ? `<p class="notice" style="margin-top:12px;">${
                loginRole === "student"
                  ? "Use the university email address your administrator approved. Other emails can't create an account."
                  : "Teacher accounts are set up by your administrator. Use the email address they added for you."
              }</p>`
            : ""
        }
        <div class="auth-form">
          <label>Full Name <input type="text" id="createName" placeholder="Your name" /></label>
          <label>Email <input type="text" id="createEmail" placeholder="you@school.edu" /></label>
          <label>Password <input type="password" id="createPassword" placeholder="At least 8 characters" /></label>
          ${
            loginRole === "student"
              ? `<div id="cohortFields"></div>`
              : ""
          }
          <p class="auth-error" id="createError"></p>
          <button class="primary auth-submit" id="createAccountButton">Create Account</button>
        </div>
        <div class="auth-footer">
          Already have an account? <button class="auth-link" id="goToLogin">Log in</button>
        </div>
      </div>
    </div>
  `;

  document.querySelectorAll(".role-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      loginRole = btn.dataset.role;
      showCreateAccount();
    });
  });

  document.getElementById("goToLogin").addEventListener("click", showLogin);

  // Students say where they study. The lists come from the administrator; with
  // none set up the old optional enrolment number is shown instead.
  let signupCourses = [];
  if (loginRole === "student") {
    api()
      .get_academic_options()
      .then((courses) => {
        signupCourses = courses || [];
        const host = document.getElementById("cohortFields");
        if (!host) return;
        host.innerHTML = signupCourses.length
          ? `<label>Course <select id="signupCourse">${cohortSelectOptions(signupCourses.map((c) => ({ value: c.id, label: c.name })), "Choose your course")}</select></label>
             <div class="field-pair">
               <label>Year <select id="signupYear"></select></label>
               <label>Roll Number <input type="text" id="signupRoll" placeholder="e.g. 27" /></label>
             </div>
             <div class="field-pair">
               <label>Division <select id="signupDivision"></select></label>
               <label>Batch <select id="signupBatch"></select></label>
             </div>`
          : `<label>Enrolment No. <span class="muted">(optional)</span> <input type="text" id="createEnrollment" placeholder="e.g. BSC2026007" /></label>`;
        if (signupCourses.length) wireCohortSelects("signup", signupCourses);
      })
      .catch(() => {
        const host = document.getElementById("cohortFields");
        if (host) host.innerHTML = `<p class="muted">Couldn't load the course list. Check the server connection and try again.</p>`;
      });
  }

  document.getElementById("createAccountButton").addEventListener("click", () => {
    const name = document.getElementById("createName").value.trim();
    const email = document.getElementById("createEmail").value.trim();
    const password = document.getElementById("createPassword").value;
    const errorEl = document.getElementById("createError");
    if (!name || !email || !password) {
      errorEl.textContent = "All fields are required.";
      return;
    }
    if (password.length < 8) {
      errorEl.textContent = "Password must be at least 8 characters.";
      return;
    }
    const enrollmentField = document.getElementById("createEnrollment");
    const enrollmentNo = enrollmentField ? enrollmentField.value.trim() || null : null;
    let cohort = null;
    if (loginRole === "student" && signupCourses.length) {
      cohort = { ...readCohort("signup"), roll_number: document.getElementById("signupRoll").value.trim() || null };
      const needsDivision = !document.getElementById("signupDivision").closest("label").hidden;
      const needsBatch = !document.getElementById("signupBatch").closest("label").hidden;
      const missing =
        !cohort.course_id ? "Choose your course."
        : !cohort.year ? "Choose your year."
        : needsDivision && !cohort.division ? "Choose your division."
        : needsBatch && !cohort.batch ? "Choose your batch."
        : !cohort.roll_number ? "Enter your roll number."
        : null;
      if (missing) {
        errorEl.textContent = missing;
        return;
      }
    }
    errorEl.textContent = "";
    api()
      .create_account(loginRole, name, email, password, enrollmentNo, cohort)
      .then((result) => {
        if (!result.ok) {
          errorEl.textContent = result.error || "Could not create account.";
          return;
        }
        return api()
          .login(loginRole, email, password)
          .then((loginResult) => enterApp(loginResult.role, loginResult.name));
      })
      .catch((err) => {
        errorEl.textContent = errorText(err);
      });
  });
}

// -- Sidebar -------------------------------------------------------

let currentRole = null; // "student" | "professor" — set by enterApp() after login
let currentUserName = null;

const STUDENT_NAV_ITEMS = [
  { key: "home", label: "Home", icon: "home", action: showLabs },
  { key: "progress", label: "My Progress", icon: "chart", action: showMyProgress },
  { key: "practice", label: "Practice", icon: "practice", action: startPractice },
  { key: "resources", label: "Resources", icon: "resources", action: showResources },
  { key: "profile", label: "Profile", icon: "profile", action: showProfile },
];

const PROFESSOR_NAV_ITEMS = [
  { key: "home", label: "Home", icon: "home", action: showProfessorHome },
  { key: "my-labs", label: "My Labs", icon: "layers", action: showMyLabs },
  { key: "students", label: "My Class", icon: "people", action: showStudents },
  { key: "reports", label: "Reports", icon: "chart", action: showReportsList },
  { key: "resources", label: "Resources", icon: "resources", action: showResources },
  { key: "profile", label: "Profile", icon: "profile", action: showProfile },
];

function activeNavItems() {
  return currentRole === "professor" ? PROFESSOR_NAV_ITEMS : STUDENT_NAV_ITEMS;
}

function sidebarFooter() {
  return currentRole === "professor"
    ? "Teach. Assess.<br />Create Impact."
    : "Better questions.<br />A smarter you.";
}

function renderSidebar() {
  const items = activeNavItems()
    .map(
      (item) => `
    <li>
      <button class="nav-item" data-nav="${item.key}">${icon(item.icon)}${item.label}</button>
    </li>`
    )
    .join("");

  document.getElementById("sidebar").innerHTML = `
    <ul class="nav-list">${items}</ul>
    <div class="sidebar-footer">${sidebarFooter()}</div>
  `;

  activeNavItems().forEach((item) => {
    document
      .querySelector(`.nav-item[data-nav="${item.key}"]`)
      .addEventListener("click", item.action);
  });
}

function setActiveNav(key) {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.nav === key);
  });
}

// -- Sidebar: workspace context -------------------------------------------

function renderWorkspaceSidebar(info, stages, navigate, leave) {
  const backAction = leave || (info.is_stage ? () => showStages(info.task_id) : showLabs);
  const backLabel = info.is_stage ? "Back to Lab" : "Back to Home";

  const taskNav = stages
    ? `
      <div class="sidebar-section-label">Stages</div>
      <ul class="task-nav-list">
        ${stages
          .map((stage, index) => {
            const isCurrent = stage.id === info.stage_id;
            const dot = stage.unlocked
              ? isCurrent
                ? icon("check")
                : index + 1
              : icon("lock");
            return `
              <li>
                <button class="task-nav-item ${isCurrent ? "current" : ""}" data-stage-id="${stage.id}" data-unlocked="${stage.unlocked}" ${isCurrent ? "disabled" : ""}>
                  <span class="step-dot">${dot}</span>
                  ${STAGE_LABELS[stage.stage_type] || stage.stage_type}
                </button>
              </li>`;
          })
          .join("")}
      </ul>`
    : "";

  document.getElementById("sidebar").innerHTML = `
    <button class="back-link" id="sidebarBackButton">${icon("back")}${backLabel}</button>
    <div class="sidebar-scroll">
      <div class="sidebar-lab-title">
        <h2>${escapeHtml(info.task_title.split(" — ")[0])}</h2>
      </div>
      ${taskNav}
      <div class="sidebar-section-label">Resources</div>
      <div id="sessionResources" class="session-resources"><p class="muted">Loading…</p></div>
    </div>
    <div class="sidebar-footer">Learn. Think.<br />Collaborate. Grow.</div>
  `;

  document.getElementById("sidebarBackButton").addEventListener("click", backAction);
  if (stages) {
    document
      .querySelectorAll(".task-nav-item[data-unlocked='true']:not([disabled])")
      .forEach((btn) => {
        btn.addEventListener("click", () => navigate(Number(btn.dataset.stageId)));
      });
  }
  loadSessionResources(info);
}

// What the student's professor shared, inside the coding screen: first what is
// attached to this lab, then everything else shared with them.
function loadSessionResources(info) {
  const box = document.getElementById("sessionResources");
  if (!box) return;
  Promise.all([api().get_lab_resources(info.task_id), api().get_student_resources()])
    .then(([forLab, everything]) => {
      const target = document.getElementById("sessionResources");
      if (!target) return;
      const labIds = new Set(forLab.map((r) => r.id));
      const others = everything.filter((r) => !labIds.has(r.id));
      const byId = Object.fromEntries([...forLab, ...others].map((r) => [r.id, r]));
      const item = (r) => `
        <li><button class="resource-item" data-resource-id="${r.id}" title="${escapeHtml(r.title)}">
          <span class="kind-badge kind-${r.kind.toLowerCase()}">${RESOURCE_KIND_LABEL[r.kind] || r.kind}</span>
          <span class="resource-item-title">${escapeHtml(r.title)}</span>
        </button></li>`;
      const group = (heading, list) =>
        list.length ? `<div class="resource-group">${heading}</div><ul class="resource-list">${list.map(item).join("")}</ul>` : "";
      target.innerHTML =
        forLab.length || others.length
          ? group("For this lab", forLab) + group("From your professor", others)
          : `<p class="muted">Nothing has been shared with you yet.</p>`;
      target.querySelectorAll(".resource-item").forEach((button) => {
        button.addEventListener("click", () => {
          const resource = byId[Number(button.dataset.resourceId)];
          if (resource) runResourceAction(resource, DEFAULT_RESOURCE_ACTION[resource.kind] || "open");
        });
      });
    })
    .catch(() => {
      const target = document.getElementById("sessionResources");
      if (target) target.innerHTML = `<p class="muted">Resources can't be loaded right now.</p>`;
    });
}

// -- Header: session timer / end session -----------------------------------

function formatClock(totalSeconds) {
  const clamped = Math.max(0, totalSeconds);
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function renderWorkspaceHeaderExtra(durationMinutes, onEndSession, elapsedSeconds = 0) {
  const headerExtra = document.getElementById("headerExtra");
  if (!durationMinutes) {
    headerExtra.innerHTML = `<button class="danger" id="endSessionButton">End Session</button>`;
    document.getElementById("endSessionButton").addEventListener("click", onEndSession);
    return;
  }

  let remaining = Math.max(0, durationMinutes * 60 - elapsedSeconds);
  headerExtra.innerHTML = `
    <div class="timer" id="sessionTimer">
      <span class="timer-label">Time Remaining</span>
      <span class="timer-value">${formatClock(remaining)}</span>
    </div>
    <button class="danger" id="endSessionButton">End Session</button>
  `;
  document.getElementById("endSessionButton").addEventListener("click", onEndSession);

  workspaceTimerId = setInterval(() => {
    remaining -= 1;
    const timerEl = document.getElementById("sessionTimer");
    if (!timerEl) {
      clearInterval(workspaceTimerId);
      return;
    }
    timerEl.querySelector(".timer-value").textContent = formatClock(remaining);
    timerEl.classList.toggle("low", remaining <= 60);
    if (remaining <= 0) clearInterval(workspaceTimerId);
  }, 1000);
}

// -- Resources -------------------------------------------------------------------
//
// Professors share files, links and written instructions with their own class
// (or chosen students in it) and can attach them to labs. Students see what
// their professor shared, on the Resources screen and on each lab's page.

const RESOURCE_KIND_LABEL = { FILE: "File", LINK: "Link", NOTE: "Instructions" };
const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

function formatBytes(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return isNaN(d) ? "" : d.toLocaleDateString();
}

function resourceCard(r, manage) {
  const labs = r.labs.length
    ? r.labs.map((l) => `<span class="chip">${escapeHtml(l.title)}</span>`).join(" ")
    : "";
  let detail = "";
  if (r.kind === "FILE") detail = `${escapeHtml(r.filename || "file")} &middot; ${formatBytes(r.size_bytes)}`;
  if (r.kind === "LINK") detail = escapeHtml(r.url || "");
  let actions = "";
  if (r.kind === "FILE") {
    actions = `<button class="primary" data-act="open">Open</button><button class="ghost" data-act="save">Save as…</button>`;
  } else if (r.kind === "LINK") {
    actions = `<button class="primary" data-act="link">Open link</button>`;
  } else {
    actions = `<button class="primary" data-act="read">Read</button>`;
  }
  if (manage) {
    actions += `<button class="ghost" data-act="edit">Edit</button><button class="ghost danger-text" data-act="delete">Delete</button>`;
  }
  const sharing = manage
    ? `<div class="muted resource-meta">${r.audience_all ? "Shared with your whole class" : `Shared with ${r.student_ids.length} selected student${r.student_ids.length === 1 ? "" : "s"}`}</div>`
    : "";
  return `
    <div class="card resource-card" data-resource-id="${r.id}">
      <div class="resource-head">
        <span class="kind-badge kind-${r.kind.toLowerCase()}">${RESOURCE_KIND_LABEL[r.kind] || r.kind}</span>
        <h3>${escapeHtml(r.title)}</h3>
      </div>
      ${r.description ? `<p class="muted resource-desc">${escapeHtml(r.description)}</p>` : ""}
      ${detail ? `<p class="resource-detail">${detail}</p>` : ""}
      ${labs ? `<div class="resource-labs">${labs}</div>` : ""}
      <div class="muted resource-meta">${manage ? "" : `From ${escapeHtml(r.owner || "your teacher")} &middot; `}${shortDate(r.updated_at || r.created_at)}</div>
      ${sharing}
      <div class="resource-actions">${actions}</div>
    </div>`;
}

const DEFAULT_RESOURCE_ACTION = { FILE: "open", LINK: "link", NOTE: "read" };

// Open / save / follow / read one resource (shared by the Resources screens and the coding screen).
function runResourceAction(resource, act) {
  const run = (promise) =>
    promise.then((result) => {
      if (result && result.ok === false && !result.cancelled) {
        showToast(result.error || "That didn't work.");
      }
    });
  if (labMode && ["open", "save", "link"].includes(act)) {
    pauseLabFocusCheck();
    showToast("Opening it outside CAVY. Close it and come back to the lab; the clock is not counting this.");
  }
  if (act === "open") run(api().open_resource(resource.id));
  else if (act === "save") run(api().save_resource(resource.id));
  else if (act === "link") run(api().open_link(resource.url));
  else if (act === "read") showNoteModal(resource);
}

function bindResourceCards(container, resources, handlers = {}) {
  const byId = Object.fromEntries(resources.map((r) => [r.id, r]));
  container.querySelectorAll(".resource-card").forEach((card) => {
    const resource = byId[Number(card.dataset.resourceId)];
    if (!resource) return;
    card.querySelectorAll("[data-act]").forEach((button) => {
      button.addEventListener("click", () => {
        const act = button.dataset.act;
        if (act === "edit" && handlers.onEdit) handlers.onEdit(resource);
        else if (act === "delete" && handlers.onDelete) handlers.onDelete(resource);
        else runResourceAction(resource, act);
      });
    });
  });
}

function showNoteModal(resource) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-card" style="max-width:640px;">
      <h2>${escapeHtml(resource.title)}</h2>
      <div class="note-body">${escapeHtml(resource.body || "")}</div>
      <div class="modal-actions"><button class="primary" id="noteClose">Close</button></div>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("noteClose").addEventListener("click", () => overlay.remove());
}

function showResources() {
  if (currentRole === "professor") showProfessorResources();
  else showStudentResources();
}

let resourceLabFilter = "";

function showStudentResources() {
  Promise.all([api().get_student_resources(), api().get_my_teacher()]).then(([resources, teacher]) => {
    const labs = new Map();
    resources.forEach((r) => r.labs.forEach((l) => labs.set(l.id, l.title)));
    const filter = labs.has(Number(resourceLabFilter)) ? resourceLabFilter : "";
    const shown = filter
      ? resources.filter((r) => r.labs.some((l) => String(l.id) === filter))
      : resources;
    let body;
    if (!teacher) {
      body = `<div class="card"><p class="muted">You haven't been added to a teacher's class yet, so there is nothing shared with you. Ask your teacher to add you (they can do it from the My Class screen).</p></div>`;
    } else if (!resources.length) {
      body = `<div class="card"><p class="muted">${escapeHtml(teacher)} hasn't shared anything with you yet. New material appears here by itself.</p></div>`;
    } else {
      body = `<div class="resource-grid">${shown.map((r) => resourceCard(r, false)).join("")}</div>`;
    }
    nextRefresh = { topics: ["resources", "accounts"], run: () => showStudentResources() };
    setScreen(
      `
      <div class="page-heading"><div><h1>Resources</h1>
        <p class="subtitle">${teacher ? `Shared with you by ${escapeHtml(teacher)}.` : "Reference material for your labs."}</p></div>
        ${
          labs.size
            ? `<label class="inline-filter">Lab <select id="resourceLabFilter"><option value="">All resources</option>${[...labs]
                .map(([id, title]) => `<option value="${id}" ${String(id) === filter ? "selected" : ""}>${escapeHtml(title)}</option>`)
                .join("")}</select></label>`
            : ""
        }
      </div>
      ${body}`,
      "resources"
    );
    const select = document.getElementById("resourceLabFilter");
    if (select) {
      select.addEventListener("change", () => {
        resourceLabFilter = select.value;
        showStudentResources();
      });
    }
    bindResourceCards(app, shown);
  });
}

function showProfessorResources() {
  api()
    .get_my_resources()
    .then((resources) => {
      nextRefresh = { topics: ["resources"], run: () => showProfessorResources() };
      setScreen(
        `
        <div class="page-heading"><div><h1>Resources</h1>
          <p class="subtitle">Share files, links and instructions with your students, and attach them to labs.</p></div>
          <button class="primary" id="addResource">${icon("plus")}Add resource</button>
        </div>
        ${
          resources.length
            ? `<div class="resource-grid">${resources.map((r) => resourceCard(r, true)).join("")}</div>`
            : `<div class="card"><p class="muted">Nothing shared yet. Use <b>Add resource</b> to upload a file, add a link, or write instructions. Only students in your class can see it.</p></div>`
        }`,
        "resources"
      );
      document.getElementById("addResource").addEventListener("click", () =>
        showResourceForm(null, showProfessorResources)
      );
      bindResourceCards(app, resources, {
        onEdit: (resource) => showResourceForm(resource, showProfessorResources),
        onDelete: (resource) =>
          confirmModal({
            title: `Delete "${resource.title}"?`,
            message: "Students will no longer see it. This can't be undone.",
            confirmLabel: "Delete",
            danger: true,
            onConfirm: () =>
              api()
                .delete_resource(resource.id)
                .then((result) => {
                  if (!result.ok) showToast(result.error || "Couldn't delete it.");
                  showProfessorResources();
                }),
          }),
      });
    });
}

function showResourceForm(existing, onSaved) {
  Promise.all([api().get_students(), api().get_professor_labs()]).then(([students, labs]) => {
    const editing = !!existing;
    let kind = editing ? existing.kind : "FILE";
    let picked = null; // {name, type, size, base64}
    const chosenStudents = new Set(existing ? existing.student_ids : []);
    const chosenLabs = new Set(existing ? existing.task_ids : []);
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-card resource-form">
        <h2>${editing ? "Edit resource" : "Add a resource"}</h2>
        ${
          editing
            ? ""
            : `<div class="role-toggle" id="kindTabs">
                 <button data-kind="FILE" class="active">File</button>
                 <button data-kind="LINK">Link</button>
                 <button data-kind="NOTE">Instructions</button>
               </div>`
        }
        <label class="modal-field">Title <input type="text" id="resTitle" value="${escapeHtml(existing ? existing.title : "")}" maxlength="300" /></label>
        <label class="modal-field">Description (optional) <input type="text" id="resDesc" value="${escapeHtml(existing && existing.description ? existing.description : "")}" /></label>
        <div id="kindFile" class="modal-field">
          <span>${editing ? `Current file: <b>${escapeHtml(existing.filename || "")}</b> (${formatBytes(existing.size_bytes)}). Choose another to replace it.` : "File (PDF, document, slides, image, data… up to 15 MB)"}</span>
          <input type="file" id="resFile" />
        </div>
        <label class="modal-field" id="kindLink">Web address <input type="text" id="resUrl" placeholder="https://…" value="${escapeHtml(existing && existing.url ? existing.url : "")}" /></label>
        <label class="modal-field" id="kindNote">Instructions <textarea id="resBody" rows="6">${escapeHtml(existing && existing.body ? existing.body : "")}</textarea></label>

        <div class="modal-field"><b>Who can see it</b>
          <label class="inline-check"><input type="radio" name="aud" value="all" ${!existing || existing.audience_all ? "checked" : ""} /> My whole class (${students.length} student${students.length === 1 ? "" : "s"})</label>
          <label class="inline-check"><input type="radio" name="aud" value="some" ${existing && !existing.audience_all ? "checked" : ""} /> Only selected students</label>
          <div class="check-list" id="studentList" style="display:none;">
            ${
              students.length
                ? students.map((s) => `<label class="inline-check"><input type="checkbox" data-student="${s.id}" ${chosenStudents.has(s.id) ? "checked" : ""} /> ${escapeHtml(s.name)} <span class="muted">${escapeHtml(s.email || "")}</span></label>`).join("")
                : `<p class="muted">You have no students in your class yet. Add some on the Students screen first.</p>`
            }
          </div>
        </div>

        <div class="modal-field"><b>Attach to labs (optional)</b>
          <div class="check-list">
            ${
              labs.length
                ? labs.map((l) => `<label class="inline-check"><input type="checkbox" data-lab="${l.id}" ${chosenLabs.has(l.id) ? "checked" : ""} /> ${escapeHtml(l.title)}</label>`).join("")
                : `<p class="muted">You haven't created any labs yet.</p>`
            }
          </div>
        </div>
        <p class="auth-error" id="resError"></p>
        <div class="modal-actions">
          <button class="ghost" id="resCancel">Cancel</button>
          <button class="primary" id="resSave">${editing ? "Save changes" : "Add resource"}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const show = (id, on) => (document.getElementById(id).style.display = on ? "" : "none");
    const syncKind = () => {
      show("kindFile", kind === "FILE");
      show("kindLink", kind === "LINK");
      show("kindNote", kind === "NOTE");
    };
    const syncAudience = () =>
      show("studentList", overlay.querySelector('input[name="aud"]:checked').value === "some");
    syncKind();
    syncAudience();
    overlay.querySelectorAll('input[name="aud"]').forEach((r) => r.addEventListener("change", syncAudience));
    overlay.querySelectorAll("#kindTabs button").forEach((button) =>
      button.addEventListener("click", () => {
        kind = button.dataset.kind;
        overlay.querySelectorAll("#kindTabs button").forEach((b) => b.classList.toggle("active", b === button));
        syncKind();
      })
    );
    const errorEl = document.getElementById("resError");
    document.getElementById("resCancel").addEventListener("click", () => overlay.remove());

    document.getElementById("resFile").addEventListener("change", (event) => {
      const file = event.target.files[0];
      picked = null;
      errorEl.textContent = "";
      if (!file) return;
      if (file.size > MAX_UPLOAD_BYTES) {
        errorEl.textContent = `That file is ${formatBytes(file.size)}. The limit is 15 MB.`;
        event.target.value = "";
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const text = String(reader.result);
        picked = { name: file.name, type: file.type, size: file.size, base64: text.slice(text.indexOf(",") + 1) };
        const title = document.getElementById("resTitle");
        if (!title.value.trim()) title.value = file.name.replace(/\.[^.]+$/, "");
      };
      reader.onerror = () => (errorEl.textContent = "That file couldn't be read.");
      reader.readAsDataURL(file);
    });

    document.getElementById("resSave").addEventListener("click", () => {
      const spec = {
        kind,
        title: document.getElementById("resTitle").value,
        description: document.getElementById("resDesc").value,
        audience_all: overlay.querySelector('input[name="aud"]:checked').value === "all",
        student_ids: [...overlay.querySelectorAll("[data-student]:checked")].map((c) => Number(c.dataset.student)),
        task_ids: [...overlay.querySelectorAll("[data-lab]:checked")].map((c) => Number(c.dataset.lab)),
      };
      if (kind === "LINK") spec.url = document.getElementById("resUrl").value;
      if (kind === "NOTE") spec.body = document.getElementById("resBody").value;
      if (kind === "FILE") {
        if (picked) {
          spec.filename = picked.name;
          spec.mime_type = picked.type;
          spec.data_base64 = picked.base64;
        } else if (!editing) {
          errorEl.textContent = "Choose a file to upload.";
          return;
        } else {
          spec.filename = existing.filename;
        }
      }
      const button = document.getElementById("resSave");
      button.disabled = true;
      button.textContent = "Saving…";
      const call = editing ? api().update_resource(existing.id, spec) : api().create_resource(spec);
      call
        .then((result) => {
          if (!result.ok) {
            errorEl.textContent = result.error || "Couldn't save it.";
            button.disabled = false;
            button.textContent = editing ? "Save changes" : "Add resource";
            return;
          }
          overlay.remove();
          showToast(editing ? "Resource updated." : "Resource added.");
          onSaved();
        })
        .catch((err) => {
          errorEl.textContent = errorText(err);
          button.disabled = false;
          button.textContent = editing ? "Save changes" : "Add resource";
        });
    });
  });
}

function showProfile() {
  api()
    .get_profile()
    .then((profile) => {
      const roleLabel = profile.role === "professor" ? "Teacher" : "Student";
      setScreen(
        `
    <div class="page-heading"><div><h1>Profile</h1><p class="subtitle">Your CAVY account.</p></div></div>
    ${
      profile.must_change_password
        ? `<div class="notice notice-warn" style="max-width:420px;">Your password was reset by your teacher. Please choose a new one below before you continue.</div>`
        : ""
    }
    <div class="card" style="max-width:420px;">
      <p><b>${escapeHtml(profile.name)}</b> <span class="pill pill-neutral">${roleLabel}</span></p>
      <p class="muted" style="margin-top:6px;">${escapeHtml(profile.email || "")}</p>
      ${
        profile.course
          ? `<p class="muted">${escapeHtml([profile.course, profile.year_label, profile.division && "Div " + profile.division, profile.batch && "Batch " + profile.batch].filter(Boolean).join(" · "))}</p>`
          : ""
      }
      ${profile.roll_number ? `<p class="muted">Roll No. ${escapeHtml(profile.roll_number)}</p>` : profile.enrollment_no ? `<p class="muted">Enrolment No. ${escapeHtml(profile.enrollment_no)}</p>` : ""}
      <p class="muted" id="teacherLine" style="margin-top:6px;"></p>
      <button class="danger" id="logoutButton" style="margin-top:16px;">Log Out</button>
    </div>
    <div class="card" style="max-width:420px; margin-top:16px;">
      <h3 style="margin-top:0;">Change Password</h3>
      <div class="auth-form" style="margin-top:12px;">
        <label>Current password <input type="password" id="currentPassword" autocomplete="current-password" /></label>
        <label>New password <input type="password" id="newPassword" placeholder="At least 8 characters" autocomplete="new-password" /></label>
        <label>Repeat new password <input type="password" id="repeatPassword" autocomplete="new-password" /></label>
        <p class="auth-error" id="passwordError"></p>
        <button class="primary" id="changePasswordButton">Update Password</button>
      </div>
    </div>
    <div class="card" style="max-width:420px; margin-top:16px;">
      <h3 style="margin-top:0;">AI Assistant</h3>
      <p class="muted" id="aiStatusLine" style="margin:6px 0 14px;">Checking the assistant&hellip;</p>
      <div id="aiConnectHost"></div>
      <button class="ghost" id="aiForgetButton" style="margin-top:12px; display:none;">Remove my key from this computer</button>
    </div>`,
        "profile"
      );
      document.getElementById("logoutButton").addEventListener("click", () => {
        api()
          .logout()
          .then(() => showLogin());
      });
      setUpPasswordChange();
      setUpAiSettings();
      if (profile.role === "student") {
        api()
          .get_my_teacher()
          .then((teacher) => {
            const el = document.getElementById("teacherLine");
            if (el) el.textContent = teacher ? `Your teacher: ${teacher}` : "You aren't in a teacher's class yet.";
          });
      }
    });
}

function setUpPasswordChange() {
  const errorEl = document.getElementById("passwordError");
  const button = document.getElementById("changePasswordButton");
  button.addEventListener("click", () => {
    const current = document.getElementById("currentPassword").value;
    const next = document.getElementById("newPassword").value;
    const repeat = document.getElementById("repeatPassword").value;
    errorEl.className = "auth-error";
    if (!current || !next) {
      errorEl.textContent = "Fill in your current and new password.";
      return;
    }
    if (next !== repeat) {
      errorEl.textContent = "The new passwords don't match.";
      return;
    }
    button.disabled = true;
    api()
      .change_password(current, next)
      .then((result) => {
        if (result.ok) {
          showToast("Password updated.");
          showProfile();
        } else {
          errorEl.textContent = result.error || "Couldn't update the password.";
        }
      })
      .finally(() => {
        button.disabled = false;
      });
  });
}

// -- AI assistant setup (shown once per launch if the assistant isn't usable) ----------

let aiSetupPromptShown = false;

// -- Connecting an AI assistant ----------------------------------------------------
//
// One form, used in the setup dialog and on Profile: pick a provider, paste a key,
// check it (the provider says which models that key can use), pick a model, connect.
// A student's key stays on their own computer; it is used from here and forgotten
// when they sign out.

function maybePromptAiSetup() {
  if (aiSetupPromptShown) return;
  aiSetupPromptShown = true;
  api()
    .get_ai_settings()
    .then((settings) => {
      // Offer setup only when there is no working assistant at all.
      if (!settings.available) showAiSetupModal(settings);
    });
}

function describeAi(settings) {
  if (!settings || !settings.provider) return "No assistant is connected.";
  const who =
    settings.scope === "personal"
      ? "your own key"
      : serverMode
        ? "the class assistant"
        : "this app";
  const what = `${settings.provider}${settings.model ? " · " + settings.model : ""}`;
  return settings.available
    ? `Using ${what} (${who}). Ready.`
    : `${what} (${who}) isn't available: ${settings.problem || "not reachable right now."}`;
}

async function renderAiConnect(container, onConnected) {
  const info = await api().get_ai_providers();
  const providers = info.providers;
  const noteFor = () =>
    info.personal
      ? "Your key stays on this computer. It is used only from here, is never sent to the server, and is forgotten when you sign out."
      : serverMode
        ? "This assistant is shared by the whole class and is also used to score sessions. The key is kept in the server's memory only."
        : "The key is kept in memory only, until you close CAVY.";
  container.innerHTML = `
    <label class="modal-field">Assistant
      <select id="aiProvider">${providers
        .map((p) => `<option value="${p.key}">${escapeHtml(p.label)}</option>`)
        .join("")}</select>
    </label>
    <div id="aiKeyBlock">
      <label class="modal-field">API key
        <input type="password" id="aiKey" autocomplete="off" />
      </label>
      <p class="muted" id="aiHelp" style="margin:6px 0 0;"></p>
      <button class="ghost" id="aiCheck" style="margin-top:8px;">Check key</button>
    </div>
    <div id="aiOllama" style="display:none;">
      <p class="muted">Runs on this computer. Needs Ollama installed and running, with a model pulled
      (<code>ollama pull qwen3:8b</code>).</p>
    </div>
    <label class="modal-field" id="aiModelBlock" style="display:none;">Model
      <select id="aiModel"></select>
    </label>
    <p class="muted" id="aiNote" style="margin-top:10px;">${escapeHtml(noteFor())}</p>
    <p class="auth-error" id="aiErr"></p>
    <button class="primary" id="aiConnect" disabled>Connect</button>`;

  const $ = (id) => container.querySelector(`#${id}`);
  const current = () => providers.find((p) => p.key === $("aiProvider").value);
  const hasModelList = (p) => p.key !== "groq" && p.key !== "ollama";
  const reset = () => {
    $("aiModelBlock").style.display = "none";
    $("aiModel").innerHTML = "";
    $("aiErr").textContent = "";
    const p = current();
    $("aiConnect").disabled = !(p.key === "ollama" || (p.key === "groq" && $("aiKey").value.trim()));
  };
  const showProvider = () => {
    const p = current();
    $("aiKeyBlock").style.display = p.needs_key ? "" : "none";
    $("aiOllama").style.display = p.needs_key ? "none" : "";
    $("aiCheck").style.display = hasModelList(p) ? "" : "none";
    $("aiKey").placeholder = p.key_hint;
    $("aiKey").value = "";
    $("aiHelp").innerHTML = p.needs_key
      ? `Get a key at <button class="auth-link" id="aiHelpLink">${escapeHtml(p.help_url.replace("https://", ""))}</button>`
      : "";
    const link = $("aiHelpLink");
    if (link) link.addEventListener("click", () => api().open_link(p.help_url));
    $("aiConnect").textContent = p.key === "ollama" ? "Check & connect" : "Connect";
    reset();
  };
  $("aiProvider").addEventListener("change", showProvider);
  $("aiKey").addEventListener("input", reset); // a changed key must be checked again

  $("aiCheck").addEventListener("click", () => {
    const p = current();
    const button = $("aiCheck");
    $("aiErr").textContent = "";
    button.disabled = true;
    button.textContent = "Checking…";
    api()
      .check_ai_key(p.key, $("aiKey").value)
      .then((result) => {
        if (!result.ok) {
          $("aiErr").textContent = result.error;
          return;
        }
        $("aiModel").innerHTML = result.models
          .map((m) => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`)
          .join("");
        $("aiModelBlock").style.display = "";
        $("aiConnect").disabled = false;
        showToast("Key accepted. Choose a model, then Connect.");
      })
      .catch((err) => ($("aiErr").textContent = errorText(err)))
      .finally(() => {
        button.disabled = false;
        button.textContent = "Check key";
      });
  });

  $("aiConnect").addEventListener("click", () => {
    const p = current();
    const button = $("aiConnect");
    $("aiErr").textContent = "";
    button.disabled = true;
    button.textContent = "Connecting…";
    api()
      .set_ai_provider(p.key, $("aiKey").value, hasModelList(p) ? $("aiModel").value : "")
      .then((result) => {
        if (result.ok === false || result.available === false) {
          $("aiErr").textContent = result.error || result.problem || "Couldn't connect.";
          return;
        }
        $("aiKey").value = "";
        showToast("Assistant connected.");
        onConnected(result);
      })
      .catch((err) => ($("aiErr").textContent = errorText(err)))
      .finally(() => {
        button.disabled = false;
        button.textContent = p.key === "ollama" ? "Check & connect" : "Connect";
        if (!button.isConnected) return;
        reset();
      });
  });
  showProvider();
}

function showAiSetupModal(settings, onConnected = () => {}) {
  document.querySelectorAll(".ai-setup-overlay").forEach((el) => el.remove());
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay ai-setup-overlay";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>Connect an AI assistant</h2>
      <p class="muted">${escapeHtml(settings.problem || "No assistant is connected yet.")}</p>
      <div id="aiConnectHost" style="margin-top:12px;"></div>
      <div class="modal-actions"><button class="ghost" id="setupLater">Not now</button></div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector("#setupLater").addEventListener("click", () => overlay.remove());
  renderAiConnect(overlay.querySelector("#aiConnectHost"), () => {
    overlay.remove();
    onConnected();
  });
}

function setUpAiSettings() {
  const statusLine = document.getElementById("aiStatusLine");
  const host = document.getElementById("aiConnectHost");
  const forget = document.getElementById("aiForgetButton");
  const refresh = () =>
    api()
      .get_ai_settings()
      .then((settings) => {
        statusLine.textContent = describeAi(settings);
        forget.style.display = settings.scope === "personal" ? "" : "none";
      });
  forget.addEventListener("click", () =>
    api()
      .forget_my_ai_key()
      .then(() => {
        showToast("Your key was removed from this computer.");
        refresh();
      })
  );
  renderAiConnect(host, () => refresh());
  refresh();
}

// -- Professor: dashboard -------------------------------------------------------

function renderFilterBar(labs, idPrefix, selected) {
  selected = selected || {};
  const uniq = (values) => [...new Set(values.filter(Boolean))];
  const optionList = (values, allLabel, selectedValue) =>
    `<option value="">${allLabel}</option>` +
    values
      .map(
        (v) =>
          `<option value="${escapeHtml(v)}" ${v === selectedValue ? "selected" : ""}>${escapeHtml(v)}</option>`
      )
      .join("");

  return `
    <div class="card filter-bar">
      <label>Course
        <select id="${idPrefix}FilterCourse">${optionList(uniq(labs.map((l) => l.course)), "All Courses", selected.course)}</select>
      </label>
      <label>Division
        <select id="${idPrefix}FilterDivision">${optionList(uniq(labs.map((l) => l.division)), "All Divisions", selected.division)}</select>
      </label>
      <label>Batch
        <select id="${idPrefix}FilterBatch">${optionList(uniq(labs.map((l) => l.batch)), "All Batches", selected.batch)}</select>
      </label>
      <label>Lab / Assessment
        <select id="${idPrefix}FilterLab">${optionList(uniq(labs.map((l) => l.title)), "All Labs", selected.title)}</select>
      </label>
      <button class="primary" id="${idPrefix}ApplyFilterButton">Apply Filter</button>
    </div>`;
}

function wireFilterBar(labs, idPrefix, onApply) {
  document.getElementById(`${idPrefix}ApplyFilterButton`).addEventListener("click", () => {
    onApply({
      course: document.getElementById(`${idPrefix}FilterCourse`).value,
      division: document.getElementById(`${idPrefix}FilterDivision`).value,
      batch: document.getElementById(`${idPrefix}FilterBatch`).value,
      title: document.getElementById(`${idPrefix}FilterLab`).value,
    });
  });
}

function filterLabs(labs, filters) {
  return labs.filter(
    (lab) =>
      (!filters.course || lab.course === filters.course) &&
      (!filters.division || lab.division === filters.division) &&
      (!filters.batch || lab.batch === filters.batch) &&
      (!filters.title || lab.title === filters.title)
  );
}

function labsTableRows(labs, manage) {
  const rows = labs
    .map(
      (lab) => `
    <tr>
      <td>${escapeHtml(lab.title)}${lab.archived ? ` <span class="pill pill-neutral">Archived</span>` : ""}</td>
      <td>${escapeHtml(lab.course || "—")}</td>
      <td>${escapeHtml(lab.division || "—")}</td>
      <td>${escapeHtml(lab.batch || "—")}</td>
      <td><div class="row-actions">
        <button class="view-report-button" data-task-id="${lab.id}">${icon("file")}View Report</button>
        ${
          manage
            ? `<button class="ghost edit-lab-button" data-task-id="${lab.id}">Edit</button>
               <button class="ghost archive-lab-button" data-task-id="${lab.id}" data-title="${escapeHtml(lab.title)}" data-archived="${lab.archived ? "1" : "0"}">${lab.archived ? "Restore" : "Archive"}</button>`
            : ""
        }
      </div></td>
    </tr>`
    )
    .join("");
  return (
    rows ||
    `<tr><td colspan="5" class="muted" style="padding:16px;">No labs yet — create one to get started.</td></tr>`
  );
}

let showArchivedLabs = false;

function renderLabsScreen(navKey, heading, subtitle, eyebrow, showCreateButton) {
  api()
    .get_professor_labs(showCreateButton && showArchivedLabs)
    .then((allLabs) => {
      const render = (labs) => {
        nextRefresh = {
          topics: ["labs", "submissions"],
          run: () => renderLabsScreen(navKey, heading, subtitle, eyebrow, showCreateButton),
        };
        setScreen(
          `
          <div class="page-heading">
            <div>${eyebrow ? `<div class="eyebrow">${escapeHtml(eyebrow)}</div>` : ""}<h1>${escapeHtml(heading)}</h1><p class="subtitle">${escapeHtml(subtitle)}</p></div>
          </div>
          ${renderFilterBar(allLabs, "labs")}
          ${
            showCreateButton
              ? `<label class="inline-check"><input type="checkbox" id="showArchivedToggle" ${showArchivedLabs ? "checked" : ""} /> Show archived labs</label>`
              : ""
          }
          <div class="card" style="padding:0; overflow:hidden;">
            <table class="data-table">
              <thead>
                <tr><th>Lab</th><th>Course</th><th>Division</th><th>Batch</th><th>Action</th></tr>
              </thead>
              <tbody>${labsTableRows(labs, showCreateButton)}</tbody>
            </table>
          </div>
          ${
            showCreateButton
              ? `<div class="centered-cta"><button class="primary" id="createSessionButton">${icon("plus")}Create New Session</button></div>`
              : ""
          }
        `,
          navKey
        );

        wireFilterBar(allLabs, "labs", (filters) => render(filterLabs(allLabs, filters)));
        if (showCreateButton) {
          document
            .getElementById("createSessionButton")
            .addEventListener("click", () => showCreateSession());
        }
        document.querySelectorAll(".view-report-button").forEach((btn) => {
          btn.addEventListener("click", () => showLabReport(Number(btn.dataset.taskId)));
        });
        document.querySelectorAll(".edit-lab-button").forEach((btn) => {
          btn.addEventListener("click", () => showEditSession(Number(btn.dataset.taskId)));
        });
        document.querySelectorAll(".archive-lab-button").forEach((btn) => {
          btn.addEventListener("click", () => {
            const taskId = Number(btn.dataset.taskId);
            const restoring = btn.dataset.archived === "1";
            const refresh = () => renderLabsScreen(navKey, heading, subtitle, eyebrow, showCreateButton);
            if (restoring) {
              api().unarchive_lab(taskId).then(refresh);
              return;
            }
            confirmModal({
              title: `Archive "${btn.dataset.title}"?`,
              message:
                "Students will no longer see it and can't start it. Everything already submitted is kept, and you can restore it any time from Show archived labs.",
              confirmLabel: "Archive",
              danger: true,
              onConfirm: () => api().archive_lab(taskId).then(refresh),
            });
          });
        });
        const archivedToggle = document.getElementById("showArchivedToggle");
        if (archivedToggle) {
          archivedToggle.addEventListener("change", () => {
            showArchivedLabs = archivedToggle.checked;
            renderLabsScreen(navKey, heading, subtitle, eyebrow, showCreateButton);
          });
        }
      };
      render(allLabs);
    });
}

function statCard(label, value, iconName) {
  return `
    <div class="card stat-card">
      <div class="stat-card-icon">${icon(iconName)}</div>
      <div>
        <div class="stat-card-value">${escapeHtml(value)}</div>
        <div class="stat-card-label">${escapeHtml(label)}</div>
      </div>
    </div>`;
}

function recentActivityRows(activity) {
  if (!activity.length) {
    return `<p class="muted" style="padding:16px;">No submissions yet — activity will show up here once students submit assessments.</p>`;
  }
  return `
    <table class="data-table">
      <thead><tr><th>Student</th><th>Lab</th><th>Score</th><th>Submitted</th></tr></thead>
      <tbody>
        ${activity
          .map(
            (row) => `
          <tr>
            <td>${escapeHtml(row.student_name)}</td>
            <td>${escapeHtml(row.lab_title)}</td>
            <td>${row.score == null ? "—" : escapeHtml(row.score)}</td>
            <td>${row.submitted_at ? escapeHtml(new Date(row.submitted_at).toLocaleString()) : "—"}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

function showProfessorHome() {
  api()
    .get_professor_dashboard_summary()
    .then((summary) => {
      nextRefresh = { topics: ["labs", "submissions", "activity"], run: () => showProfessorHome() };
      setScreen(
        `
        <div class="page-heading">
          <div><div class="eyebrow">Professor Dashboard</div><h1>Welcome back!</h1><p class="subtitle">Here's what's happening across your labs.</p></div>
        </div>
        <div class="stat-card-grid">
          ${statCard("Total Labs", summary.total_labs, "layers")}
          ${statCard("Students Engaged", summary.total_students, "people")}
          ${statCard("Average Score", summary.average_score == null ? "—" : `${summary.average_score}%`, "chart")}
          ${statCard("Pending Submissions", summary.pending_submissions, "clock")}
        </div>
        <div class="page-heading" style="margin-top:28px;"><h2>Recent Activity</h2></div>
        <div class="card" style="padding:0; overflow:hidden;">
          ${recentActivityRows(summary.recent_activity)}
        </div>
        `,
        "home"
      );
    });
}

function showMyLabs() {
  renderLabsScreen(
    "my-labs",
    "My Labs",
    "Manage your labs, view reports, and create new sessions.",
    "My Labs",
    true
  );
}

function showReportsList() {
  renderLabsScreen("reports", "Reports", "Select a lab to view its report.", "Reports", false);
}

// The class as a tree: course > year > division and batch, each with its students.
function classTreeHtml(students, studentRow) {
  const byKey = (list, key) => {
    const groups = new Map();
    list.forEach((item) => {
      const k = key(item);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(item);
    });
    return [...groups.entries()];
  };
  const noCourse = "No course set";
  const courses = byKey(students, (s) => s.course || noCourse).sort(([a], [b]) =>
    a === noCourse ? 1 : b === noCourse ? -1 : a.localeCompare(b)
  );
  const average = (list) => {
    const scores = list.map((s) => s.overall_ciq).filter((v) => v !== null);
    return scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null;
  };
  const summary = (label, list, level) =>
    `<summary class="tree-${level}"><span>${escapeHtml(label)}</span><span class="muted">${list.length} student${list.length === 1 ? "" : "s"}${average(list) === null ? "" : ` &middot; average CIQ ${average(list)}`}</span></summary>`;
  const table = (list) => `
    <div class="card tree-table"><table class="data-table">
      <thead><tr><th>Name</th><th>Email</th><th>Roll No.</th><th>Submitted</th><th>CIQ</th><th>Action</th></tr></thead>
      <tbody>${list.sort((a, b) => a.name.localeCompare(b.name)).map(studentRow).join("")}</tbody>
    </table></div>`;
  return courses
    .map(([course, inCourse]) => {
      const years = byKey(inCourse, (s) => s.year || 0).sort(([a], [b]) => a - b);
      return `<details class="tree-node" open>${summary(course, inCourse, "course")}
        ${years
          .map(([year, inYear]) => {
            const groups = byKey(inYear, (s) => `${s.division ? "Division " + s.division : "No division"} · ${s.batch ? "Batch " + s.batch : "No batch"}`).sort(([a], [b]) => a.localeCompare(b));
            return `<details class="tree-node tree-child" open>${summary(year ? inYear[0].year_label : "Year not set", inYear, "year")}
              ${groups.map(([label, list]) => `<div class="tree-group"><div class="tree-group-label">${escapeHtml(label)} <span class="muted">· ${list.length}</span></div>${table(list)}</div>`).join("")}
            </details>`;
          })
          .join("")}
      </details>`;
    })
    .join("");
}

function placeLine(item) {
  if (!item.course) return "—";
  return [item.course, item.year_label, item.division && `Div ${item.division}`, item.batch && `Batch ${item.batch}`]
    .filter(Boolean)
    .join(" · ");
}

function showStudents() {
  Promise.all([
    api().get_class_overview(),
    api().get_unassigned_students(),
    api().get_my_courses(),
    api().get_class_groups(),
  ]).then(([students, unassigned, myCourses, groups]) => {
    nextRefresh = null;
    const studentRow = (student) => `
      <tr>
        <td><button class="link-button view-progress" data-student-id="${student.id}" title="See ${escapeHtml(student.name)}'s progress">${escapeHtml(student.name)}</button>${student.must_change_password ? ` <span class="pill pill-amber">Must change password</span>` : ""}</td>
        <td>${escapeHtml(student.email || "—")}</td>
        <td>${escapeHtml(student.roll_number || student.enrollment_no || "—")}</td>
        <td>${student.sessions_submitted}</td>
        <td>${
          student.overall_ciq === null
            ? `<span class="muted">—</span>`
            : `<button class="ciq-pill view-progress" data-student-id="${student.id}" title="See the CIQ graph">${Math.round(student.overall_ciq)}</button>`
        }</td>
        <td class="row-actions-cell"><div class="row-actions">
          <button class="ghost reset-password-button" data-student-id="${student.id}" data-name="${escapeHtml(student.name)}">Reset password</button>
          <button class="ghost remove-student-button" data-student-id="${student.id}" data-name="${escapeHtml(student.name)}">Remove</button>
        </div></td>
      </tr>`;
    const rows = classTreeHtml(students, studentRow);
    const addable = unassigned
      .map(
        (s) => `<label class="inline-check"><input type="checkbox" data-add="${s.id}" /> ${escapeHtml(s.name)} <span class="muted">${escapeHtml(placeLine(s))}${s.roll_number ? " · Roll " + escapeHtml(s.roll_number) : ""}</span></label>`
      )
      .join("");
    nextRefresh = { topics: ["accounts", "submissions"], run: () => showStudents() };
    setScreen(
      `
    <div class="page-heading"><div><h1>My class</h1><p class="subtitle">The students in your class. Only you (and an administrator) can manage them or share resources with them.</p></div></div>
    ${rows || `<div class="card"><p class="muted">Nobody in your class yet. Add a group or students below.</p></div>`}
    <div class="page-heading" style="margin-top:28px;"><div><h2>Add a whole group</h2>
      <p class="subtitle">Choose a course (and a year, division or batch) and everyone in it who isn't in another class joins yours. Students of that group who sign up later join automatically.</p></div></div>
    <div class="card" id="groupCard">
      ${
        myCourses.length
          ? `<div class="group-form">
               <label>Course <select id="groupCourse">${cohortSelectOptions(myCourses.map((c) => ({ value: c.id, label: c.name })), "Choose a course")}</select></label>
               <label>Year <select id="groupYear"></select></label>
               <label>Division <select id="groupDivision"></select></label>
               <label>Batch <select id="groupBatch"></select></label>
             </div>
             <p class="muted" id="groupPreview" style="margin:10px 0 0;">Choose a course to see who would be added.</p>
             <button class="primary" id="addGroup" style="margin-top:12px;" disabled>Add this group to my class</button>`
          : `<p class="muted">No course has been assigned to you yet. Ask your administrator to assign you the courses you teach; then you can add their students here.</p>`
      }
      ${
        groups.length
          ? `<h3 style="margin:18px 0 6px;">Groups in your class</h3>
             <ul class="group-list">${groups
               .map(
                 (g) => `<li><span>${escapeHtml(g.label)} <span class="muted">· ${g.students} student${g.students === 1 ? "" : "s"} now</span></span>
                   <button class="ghost remove-group-button" data-group-id="${g.id}" title="Stop adding new students from this group (the ones already in your class stay)">Stop adding</button></li>`
               )
               .join("")}</ul>`
          : ""
      }
    </div>
    <div class="page-heading" style="margin-top:28px;"><div><h2>Add individual students</h2>
      <p class="subtitle">Students who have an account but aren't in anyone's class yet${
        myCourses.length ? ", in the courses you teach" : ""
      }. A student can be in one class at a time.</p></div></div>
    <div class="card">
      ${
        unassigned.length
          ? `<div class="check-list">${addable}</div><button class="primary" id="addToClass" style="margin-top:12px;">Add selected to my class</button>`
          : `<p class="muted">Every student with an account is already in a class. New students appear here after they sign up.</p>`
      }
    </div>`,
      "students"
    );
    if (myCourses.length) {
      const addGroupButton = document.getElementById("addGroup");
      const preview = document.getElementById("groupPreview");
      const showPreview = () => {
        const group = readCohort("group");
        addGroupButton.disabled = true;
        if (!group.course_id) {
          preview.textContent = "Choose a course to see who would be added.";
          return;
        }
        api()
          .preview_class_group(group)
          .then((result) => {
            if (!result.ok) {
              preview.textContent = result.error;
              return;
            }
            preview.textContent =
              `${result.matching} student${result.matching === 1 ? "" : "s"} in this group: ` +
              `${result.to_add} would be added, ${result.already_yours} already in your class, ` +
              `${result.in_other_class} in another professor's class.`;
            addGroupButton.disabled = false;
          });
      };
      wireCohortSelects("group", myCourses, { anyLabels: true, onChange: showPreview });
      addGroupButton.addEventListener("click", () => {
        api()
          .add_class_group(readCohort("group"))
          .then((result) => {
            if (!result.ok) showToast(result.error || "Couldn't add the group.");
            else
              showToast(
                `${result.added} student${result.added === 1 ? "" : "s"} added` +
                  (result.in_other_class ? `; ${result.in_other_class} already belong to another class.` : ".")
              );
            showStudents();
          });
      });
    }
    document.querySelectorAll(".remove-group-button").forEach((btn) => {
      btn.addEventListener("click", () => {
        api()
          .remove_class_group(Number(btn.dataset.groupId))
          .then((result) => {
            if (!result.ok) showToast(result.error || "Couldn't remove the group.");
            showStudents();
          });
      });
    });
    const addButton = document.getElementById("addToClass");
    if (addButton) {
      addButton.addEventListener("click", () => {
        const ids = [...document.querySelectorAll("[data-add]:checked")].map((c) => Number(c.dataset.add));
        if (!ids.length) {
          showToast("Tick the students to add.");
          return;
        }
        api()
          .add_students_to_class(ids)
          .then((result) => {
            if (!result.ok) showToast(result.error || "Couldn't add them.");
            else showToast(`${result.added} student${result.added === 1 ? "" : "s"} added.`);
            showStudents();
          });
      });
    }
    document.querySelectorAll(".view-progress").forEach((btn) => {
      btn.addEventListener("click", () => showStudentProgress(Number(btn.dataset.studentId)));
    });
    document.querySelectorAll(".remove-student-button").forEach((btn) => {
      btn.addEventListener("click", () => {
        confirmModal({
          title: `Remove ${btn.dataset.name} from your class?`,
          message:
            "They will no longer see your resources. Their work and scores are kept. Another professor (or you again) can add them later.",
          confirmLabel: "Remove",
          danger: true,
          onConfirm: () =>
            api()
              .remove_student_from_class(Number(btn.dataset.studentId))
              .then((result) => {
                if (!result.ok) showToast(result.error || "Couldn't remove them.");
                showStudents();
              }),
        });
      });
    });
    document.querySelectorAll(".reset-password-button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const studentId = Number(btn.dataset.studentId);
        confirmModal({
          title: `Reset ${btn.dataset.name}'s password?`,
          message:
            "They'll get a temporary password and will be asked to choose a new one the next time they sign in. Their current password stops working immediately.",
          confirmLabel: "Reset password",
          danger: true,
          onConfirm: () =>
            api()
              .reset_student_password(studentId)
              .then((result) => {
                if (result.ok) {
                  showTemporaryPassword(btn.dataset.name, result.temporary_password, showStudents);
                } else {
                  showToast(result.error || "Couldn't reset the password.");
                }
              }),
        });
      });
    });
  });
}

function showTemporaryPassword(studentName, temporaryPassword, onClose) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>Temporary password for ${escapeHtml(studentName)}</h2>
      <p class="muted">Give this to the student now &mdash; it won't be shown again. They'll be asked to replace it when they sign in.</p>
      <div class="temp-password" id="tempPassword">${escapeHtml(temporaryPassword)}</div>
      <div class="modal-actions">
        <button class="ghost" id="copyTempPassword">Copy</button>
        <button class="primary" id="closeTempPassword">Done</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("copyTempPassword").addEventListener("click", () => {
    navigator.clipboard?.writeText(temporaryPassword).then(
      () => showToast("Copied."),
      () => showToast("Couldn't copy — select the password and copy it manually.")
    );
  });
  document.getElementById("closeTempPassword").addEventListener("click", () => {
    overlay.remove();
    onClose();
  });
}

// -- Professor: create session -------------------------------------------------------

const STAGE_CONFIG = [
  { type: "LEARNING", label: "Learning", defaultMinutes: 20 },
  { type: "EXPLORATION", label: "Exploration", defaultMinutes: 20 },
  { type: "ASSESSMENT", label: "Assessment", defaultMinutes: 20 },
];

const AI_MODE_OPTIONS = [
  { value: "FULL", label: "Full Assistance" },
  { value: "RESTRICTED", label: "Restricted Assistance" },
  { value: "NONE", label: "No Assistance" },
];

function showEditSession(taskId) {
  api()
    .get_lab(taskId)
    .then((lab) => showCreateSession(lab));
}

function showCreateSession(existing = null) {
  api()
    .get_my_courses()
    .then((courses) => renderCreateSession(existing, courses || []))
    .catch(() => renderCreateSession(existing, []));
}

function renderCreateSession(existing, courses) {
  const editing = existing !== null;
  const savedStage = (type) => (existing ? existing.stages.find((s) => s.stage_type === type) : null);
  const stageCards = STAGE_CONFIG.map((stage) => {
    const saved = savedStage(stage.type);
    const duration = saved ? saved.duration_minutes ?? "" : stage.defaultMinutes;
    const selectedMode = saved
      ? saved.ai_assistance_mode
      : stage.type === "ASSESSMENT"
        ? "RESTRICTED"
        : "FULL";
    const locked = saved && saved.mode_locked;
    return `
      <div class="card stage-config-card">
        <h3>${stage.label}</h3>
        <label>
          Duration (minutes)
          <input type="number" min="1" class="stage-duration" data-stage="${stage.type}" value="${duration}" />
        </label>
        <label>
          AI Mode
          <select class="stage-mode" data-stage="${stage.type}" ${locked ? "disabled" : ""}>
            ${AI_MODE_OPTIONS.map(
              (opt) =>
                `<option value="${opt.value}" ${opt.value === selectedMode ? "selected" : ""}>${opt.label}</option>`
            ).join("")}
          </select>
          ${locked ? `<span class="muted">Locked — students have already started this stage.</span>` : ""}
        </label>
      </div>`;
  }).join("");

  setScreen(
    `
    <button class="back-link" id="backButton">${icon("back")}Back to ${editing ? "My Labs" : "Dashboard"}</button>
    <div class="page-heading"><div><h1>${editing ? "Edit Session" : "Create New Session"}</h1><p class="subtitle">${editing ? "Change this lab's details, timing and AI settings." : "Set up a new lab with learning, exploration, and assessment stages."}</p></div></div>
    <div class="card">
      <h3 style="margin-bottom:14px;">Session Details</h3>
      <div class="form-grid">
        <label class="span-2">Session Title <span class="required">*</span>
          <input type="text" id="sessionTitle" placeholder="Enter session title" />
        </label>
        ${
          courses.length
            ? `<label>Course <span class="required">*</span>
                <select id="sessionCourse">${cohortSelectOptions(courses.map((c) => ({ value: c.id, label: c.name })), "Choose a course")}</select>
              </label>
              <label>Topic
                <input type="text" id="sessionTopic" placeholder="Enter topic" />
              </label>
              <label>Year <select id="sessionYear"></select></label>
              <label>Division <select id="sessionDivision"></select></label>
              <label>Batch <select id="sessionBatch"></select></label>
              <p class="muted span-2" style="margin:0;">Only students in this course (and the year, division and batch you pick) will get this lab.</p>`
            : serverMode
              ? `<label>Topic
                  <input type="text" id="sessionTopic" placeholder="Enter topic" />
                </label>
                <p class="muted span-2" style="margin:0;">No course has been assigned to you yet, so this lab goes to your own class. Ask your administrator to assign you a course to aim labs at a year, division or batch.</p>`
              : `<label>Course
                  <input type="text" id="sessionCourse" placeholder="e.g. B.Sc. Data Science" />
                </label>
                <label>Topic
                  <input type="text" id="sessionTopic" placeholder="Enter topic" />
                </label>
                <label>Division
                  <input type="text" id="sessionDivision" placeholder="e.g. A" />
                </label>
                <label>Batch
                  <input type="text" id="sessionBatch" placeholder="e.g. 2026" />
                </label>`
        }
        <label>Difficulty
          <select id="sessionDifficulty">
            <option value="Easy">Easy</option>
            <option value="Medium" selected>Medium</option>
            <option value="Hard">Hard</option>
          </select>
        </label>
        <label class="span-2">Description
          <textarea id="sessionDescription" rows="4" placeholder="Describe the problem students will solve..."></textarea>
        </label>
        <div class="span-2 ai-draft-row"><button class="ghost" id="draftDescription" type="button">${icon("sparkle")}Draft with AI</button><span class="muted" id="draftStatus">Write a few words about the problem first, or leave it empty to use just the title and topic.</span></div>
      </div>
    </div>
    <div class="card" style="margin-top:16px;">
      <h3 style="margin-bottom:4px;">Learning Workflow</h3>
      <p class="muted" style="margin-bottom:14px;">Configure the duration and AI mode for each stage of the session.</p>
      <div class="stage-config-grid">${stageCards}</div>
    </div>
    <div class="button-row" style="margin-top:16px;">
      <button class="primary" id="createLabButton">${editing ? "Save Changes" : "Create Lab"} ${icon("arrowRight")}</button>
      <span class="muted" id="createLabError"></span>
    </div>
  `,
    "professor"
  );

  document.getElementById("backButton").addEventListener("click", showMyLabs);
  wireAiDraft(
    document.getElementById("draftDescription"),
    document.getElementById("draftStatus"),
    document.getElementById("sessionDescription"),
    () => ({
      title: document.getElementById("sessionTitle").value.trim(),
      topic: document.getElementById("sessionTopic").value.trim(),
      difficulty: document.getElementById("sessionDifficulty").value,
    })
  );
  document.getElementById("createLabButton").addEventListener("click", () => {
    const title = document.getElementById("sessionTitle").value.trim();
    const errorEl = document.getElementById("createLabError");
    if (!title) {
      errorEl.textContent = "Session Title is required.";
      return;
    }
    errorEl.textContent = "";

    const stages = STAGE_CONFIG.map((stage) => ({
      duration_minutes:
        Number(document.querySelector(`.stage-duration[data-stage="${stage.type}"]`).value) ||
        null,
      ai_assistance_mode: document.querySelector(`.stage-mode[data-stage="${stage.type}"]`).value,
    }));

    const text = (id) => {
      const field = document.getElementById(id);
      return field ? field.value.trim() || null : null;
    };
    let target;
    if (courses.length) {
      target = readCohort("session");
      if (!target.course_id) {
        errorEl.textContent = "Choose which course this lab is for.";
        return;
      }
    } else {
      target = { course: text("sessionCourse"), division: text("sessionDivision"), batch: text("sessionBatch") };
    }
    const payload = {
      title,
      ...target,
      topic: document.getElementById("sessionTopic").value.trim() || null,
      description: document.getElementById("sessionDescription").value.trim() || null,
      difficulty: document.getElementById("sessionDifficulty").value,
      stages,
    };

    if (!editing) {
      api()
        .create_lab(payload)
        .then((result) => {
          if (result && result.ok === false) errorEl.textContent = result.error || "Couldn't create the lab.";
          else showMyLabs();
        });
      return;
    }
    // A locked stage's <select> is disabled, so send back the mode it already has.
    payload.stages.forEach((stage, index) => {
      if (existing.stages[index].mode_locked) {
        stage.ai_assistance_mode = existing.stages[index].ai_assistance_mode;
      }
    });
    api()
      .update_lab(existing.id, payload)
      .then((result) => {
        if (result.ok) showMyLabs();
        else errorEl.textContent = result.error || "Couldn't save the changes.";
      });
  });

  if (courses.length) wireCohortSelects("session", courses, { current: editing ? existing : {}, anyLabels: true });

  if (editing) {
    const set = (id, value) => {
      const field = document.getElementById(id);
      if (field) field.value = value || "";
    };
    set("sessionTitle", existing.title);
    if (!courses.length) {
      set("sessionCourse", existing.course);
      set("sessionDivision", existing.division);
      set("sessionBatch", existing.batch);
    }
    set("sessionTopic", existing.topic);
    set("sessionDescription", existing.description);
    if (existing.difficulty) document.getElementById("sessionDifficulty").value = existing.difficulty;
  }
}

// -- Professor: lab report -------------------------------------------------------

function exportReport(taskId, button) {
  const idleLabel = button.innerHTML;
  button.disabled = true;
  api()
    .export_lab_report(taskId)
    .then((result) => {
      if (result.ok) showToast(`Report saved to ${result.path}`);
      else if (!result.cancelled) showToast(result.error || "Couldn't save the report.");
    })
    .finally(() => {
      button.disabled = false;
      button.innerHTML = idleLabel;
    });
}

// A small notice that fades on its own — used for results with no screen of their own.
function showToast(message) {
  document.querySelectorAll(".toast").forEach((el) => el.remove());
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}

// In-app confirmation. (window.confirm() isn't reliable inside a pywebview window.)
function confirmModal({ title, message, confirmLabel, danger, onConfirm }) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>${escapeHtml(title)}</h2>
      <p class="muted">${escapeHtml(message)}</p>
      <div class="modal-actions">
        <button class="ghost" id="confirmCancel">Cancel</button>
        <button class="${danger ? "danger" : "primary"}" id="confirmOk">${escapeHtml(confirmLabel)}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("confirmCancel").addEventListener("click", () => overlay.remove());
  document.getElementById("confirmOk").addEventListener("click", () => {
    overlay.remove();
    onConfirm();
  });
}

// Wires a "Draft with AI" button: sends the title/topic/difficulty and whatever is already
// in the description box (a rough idea, or a draft to improve) and puts the result in the box.
function wireAiDraft(button, status, textarea, getSpec) {
  button.addEventListener("click", () => {
    const spec = { ...getSpec(), notes: textarea.value.trim() };
    if (!spec.title && !spec.notes) {
      status.textContent = "Enter a title (or a few notes) first.";
      return;
    }
    button.disabled = true;
    status.textContent = "Writing\u2026";
    api()
      .draft_problem_description(spec)
      .then((result) => {
        if (result.ok) {
          textarea.value = result.text;
          status.textContent = "Drafted. Read it through and edit it before saving.";
        } else {
          status.textContent = result.error || "The AI couldn't draft that.";
        }
      })
      .catch((err) => {
        status.textContent = errorText(err);
      })
      .finally(() => {
        button.disabled = false;
      });
  });
}

// Create a follow-up (pass ``sourceTaskId``) or edit one (pass ``followup``).
function showFollowupModal({ sourceTaskId = null, followup = null, onDone }) {
  const editing = followup !== null;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>${editing ? "Edit Follow-Up Assessment" : "Add Follow-Up Assessment"}</h2>
      <p class="muted">${editing ? "Change the title, the problem, the AI mode or the time." : "Spin off a Transfer Task or Retention Check linked to this Lab."}</p>
      ${
        editing
          ? ""
          : `<label>Kind
        <select id="followupKind">
          <option value="TRANSFER">Transfer Task (a different problem, same concept)</option>
          <option value="RETENTION">Retention Check (same concept, attempted later)</option>
        </select>
      </label>`
      }
      <label>Title
        <input type="text" id="followupTitle" placeholder="e.g. Recursion Basics — Transfer Task" />
      </label>
      <label>Problem Description
        <textarea id="followupDescription" rows="5" placeholder="Describe the task the student will attempt..."></textarea>
      </label>
      <div class="ai-draft-row"><button class="ghost" id="followupDraft">${icon("sparkle")}Draft with AI</button><span class="muted" id="followupDraftStatus"></span></div>
      <label>AI Assistance
        <select id="followupAiMode" ${editing && followup.mode_locked ? "disabled" : ""}>
          <option value="RESTRICTED">Restricted</option>
          <option value="NONE">None</option>
          <option value="FULL">Full</option>
        </select>
        ${editing && followup.mode_locked ? `<span class="muted">Locked — students have already started it.</span>` : ""}
      </label>
      <label>Duration (minutes)
        <input type="number" id="followupDuration" value="20" min="1" />
      </label>
      <p class="muted" id="followupError" style="color:var(--danger-text, #b3261e);"></p>
      <div class="modal-actions">
        <button class="ghost" id="followupCancel">Cancel</button>
        <button class="primary" id="followupCreate">${editing ? "Save" : "Create"}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const value = (id) => document.getElementById(id).value;
  if (editing) {
    document.getElementById("followupTitle").value = followup.title || "";
    document.getElementById("followupDescription").value = followup.description || "";
    document.getElementById("followupAiMode").value = followup.ai_assistance_mode || "RESTRICTED";
    document.getElementById("followupDuration").value = followup.duration_minutes || "";
  }
  wireAiDraft(
    document.getElementById("followupDraft"),
    document.getElementById("followupDraftStatus"),
    document.getElementById("followupDescription"),
    () => ({ title: value("followupTitle").trim() })
  );

  document.getElementById("followupCancel").addEventListener("click", () => overlay.remove());
  document.getElementById("followupCreate").addEventListener("click", () => {
    const title = value("followupTitle").trim();
    const errorEl = document.getElementById("followupError");
    if (!title) {
      errorEl.textContent = "A title is required.";
      return;
    }
    const fields = {
      title,
      description: value("followupDescription").trim(),
      ai_assistance_mode: value("followupAiMode"),
      duration_minutes: Number(value("followupDuration")) || null,
    };
    const request = editing
      ? api().update_followup_assessment(followup.id, fields)
      : api().create_followup_assessment({ ...fields, source_task_id: sourceTaskId, kind: value("followupKind") });
    request.then((result) => {
      if (result && result.ok === false) {
        errorEl.textContent = result.error || "That didn't work.";
        return;
      }
      overlay.remove();
      onDone();
    });
  });
}

function followupAssessmentSummary(followups) {
  const kindLabel = { TRANSFER: "Transfer Task", RETENTION: "Retention Check" };
  const rows = followups
    .map(
      (f) => `
    <div class="card followup-summary-card">
      <span class="pill pill-amber">${kindLabel[f.assessment_kind] || f.assessment_kind}</span>
      <b>${escapeHtml(f.title)}</b>
      <span class="muted">${f.submitted_count} submitted</span>
      <span class="spacer"></span>
      <button class="ghost followup-view" data-task-id="${f.id}">View submissions</button>
      <button class="ghost followup-edit" data-task-id="${f.id}">Edit</button>
    </div>`
    )
    .join("");
  return `
    <div class="page-heading" style="margin-top:28px;">
      <div><h2>Follow-Up Assessments</h2><p class="subtitle">Transfer tasks and retention checks spun off from this Lab.</p></div>
      <button id="addFollowupButton">${icon("plus")}Add Follow-Up</button>
    </div>
    ${rows || `<p class="muted">None yet — add a Transfer Task or Retention Check to collect S3.3/S3.4 evidence.</p>`}`;
}

function showLabReport(taskId, parentTaskId = null) {
  Promise.all([
    api().get_lab_report(taskId),
    api().get_professor_labs(),
    api().get_followup_assessments(taskId),
  ]).then(([report, allLabs, followups]) => {
    const rows = report.rows
      .map(
        (row) => `
        <tr>
          <td><button class="link-button view-work" data-student-id="${row.student_id}" title="See their submitted code and output">${escapeHtml(row.student_name)}</button></td>
          <td>${escapeHtml(row.enrollment_no || "—")}</td>
          <td>${row.score === null ? "—" : row.score}</td>
          <td>${row.submitted_at ? escapeHtml(new Date(row.submitted_at).toLocaleString()) : "—"}</td>
          <td><span class="pill ${row.status === "Submitted" ? "pill-ready" : "pill-danger"}">${row.status}</span>${row.note ? `<div class="muted report-note">${escapeHtml(row.note)}</div>` : ""}</td>
        </tr>`
      )
      .join("");

    const meta = [report.course, `Batch: ${report.batch || "—"}`, `Division: ${report.division || "—"}`]
      .filter(Boolean)
      .join(" | ");

    nextRefresh = { topics: ["submissions", "activity"], run: () => showLabReport(taskId) };
    setScreen(
      `
        <button class="back-link" id="backButton">${icon("back")}${parentTaskId ? "Back to the lab report" : "Back to Dashboard"}</button>
        <div class="page-heading">
          <div><h1>${escapeHtml(report.task_title)}</h1><p class="subtitle">${escapeHtml(meta)}${parentTaskId ? " &middot; follow-up assessment" : ""}</p></div>
          <button id="exportReportButton">${icon("download")}Export Report</button>
        </div>
        <div class="card report-summary">
          <div class="summary-lab-info">
            <h3>${escapeHtml(report.task_title)}</h3>
            <p class="muted">${escapeHtml(report.course || "—")}</p>
            <p class="muted">Batch: ${escapeHtml(report.batch || "—")} &middot; Division: ${escapeHtml(report.division || "—")}</p>
          </div>
          <div class="summary-stats">
            <div class="summary-stat"><strong>${report.total_students}</strong><span>Total Students</span></div>
            <div class="summary-stat"><strong>${report.submitted_count}</strong><span>Submitted</span></div>
            <div class="summary-stat"><strong>${report.not_submitted_count}</strong><span>Not Submitted</span></div>
            <div class="summary-stat"><strong>${report.average_score === null ? "—" : report.average_score}</strong><span>Average Score</span></div>
          </div>
        </div>
        ${renderFilterBar(allLabs, "report", {
          course: report.course,
          division: report.division,
          batch: report.batch,
          title: report.task_title,
        })}
        <div class="card" style="padding:0; overflow:hidden;">
          <table class="data-table">
            <thead>
              <tr><th>Student Name</th><th>Enrolment No.</th><th>Score</th><th>Submitted On</th><th>Status</th></tr>
            </thead>
            <tbody>
              ${rows || `<tr><td colspan="5" class="muted" style="padding:16px;">No attempts yet.</td></tr>`}
            </tbody>
          </table>
        </div>
        ${parentTaskId ? "" : followupAssessmentSummary(followups)}
      `,
      "reports"
    );

    document.getElementById("backButton").addEventListener("click", () =>
      parentTaskId ? showLabReport(parentTaskId) : showMyLabs()
    );
    document.querySelectorAll(".view-work").forEach((button) => {
      button.addEventListener("click", () =>
        api()
          .get_student_submission(taskId, Number(button.dataset.studentId))
          .then((work) => showStudentWork(work, () => showLabReport(taskId, parentTaskId), "Back to the report"))
          .catch((err) => showToast(errorText(err)))
      );
    });
    document.querySelectorAll(".followup-view").forEach((button) => {
      button.addEventListener("click", () => showLabReport(Number(button.dataset.taskId), taskId));
    });
    document.querySelectorAll(".followup-edit").forEach((button) => {
      button.addEventListener("click", () => {
        const followup = followups.find((f) => f.id === Number(button.dataset.taskId));
        showFollowupModal({ followup, onDone: () => showLabReport(taskId) });
      });
    });
    const exportButton = document.getElementById("exportReportButton");
    exportButton.addEventListener("click", () => exportReport(taskId, exportButton));
    wireFilterBar(allLabs, "report", (filters) => {
      const matches = filterLabs(allLabs, filters);
      if (matches.length > 0) showLabReport(matches[0].id);
    });
    const addFollowup = document.getElementById("addFollowupButton");
    if (addFollowup) {
      addFollowup.addEventListener("click", () => {
        showFollowupModal({ sourceTaskId: taskId, onDone: () => showLabReport(taskId) });
      });
    }
  });
}

// -- Labs screen -------------------------------------------------------

function showLabs() {
  api()
    .get_labs()
    .then((labs) => {
      const cards = labs
        .map(
          (lab, index) => `
        <div class="card lab-card" data-lab-id="${lab.id}">
          <div class="badge-number">${String(index + 1).padStart(2, "0")}</div>
          <h3>${escapeHtml(lab.title)}</h3>
          ${lab.learning_objective ? `<p class="muted">${escapeHtml(lab.learning_objective)}</p>` : ""}
          <div class="meta-row">${icon("person")}${lab.professor_name ? escapeHtml(lab.professor_name) : "Self-paced"}</div>
          <div class="meta-row">${icon("clock")}${lab.stage_count} stage${lab.stage_count === 1 ? "" : "s"}</div>
          <span class="pill ${lab.status === "not_started" ? "pill-ready" : lab.status === "submitted" ? "pill-ready" : "pill-neutral"}">${{ not_started: "Ready to start", in_progress: "In progress", submitted: "Submitted" }[lab.status] || "Ready to start"}</span>
          <button class="primary lab-start-button">${{ in_progress: "Continue", submitted: "View" }[lab.status] || "Start"} ${icon("arrowRight")}</button>
        </div>`
        )
        .join("");

      nextRefresh = { topics: ["labs"], run: () => showLabs() };
      setScreen(
        `
        <div class="page-heading">
          <div><h1>Today's Lab Sessions</h1><p class="subtitle">Complete them to build your CIQ.</p></div>
        </div>
        <div class="lab-grid">${cards}</div>
        <div class="card practice-banner">
          <div class="practice-banner-text">
            <div class="icon-tile">${icon("practice")}</div>
            <div>
              <h3 style="font-size:14px;">Want to practice?</h3>
              <p class="muted">Explore additional practice problems and sharpen your skills.</p>
            </div>
          </div>
          <button class="ghost" id="practiceButton">Open Practice ${icon("arrowRight")}</button>
        </div>
      `,
        "home"
      );

      app.querySelectorAll(".lab-card").forEach((card) => {
        card.querySelector(".lab-start-button").addEventListener("click", () => {
          showStages(Number(card.dataset.labId));
        });
      });
      document.getElementById("practiceButton").addEventListener("click", startPractice);
    });
}

// -- Stages screen -------------------------------------------------------

const STAGE_LABELS = {
  LEARNING: "Learning",
  EXPLORATION: "Exploration",
  ASSESSMENT: "Assessment",
};
const MODE_LABELS = {
  FULL: "Full assistance",
  RESTRICTED: "Restricted assistance",
  NONE: "No AI assistance",
};

function followupAssessmentCards(followups) {
  if (!followups.length) return "";
  const kindLabel = { TRANSFER: "Transfer Task", RETENTION: "Retention Check" };
  const cards = followups
    .map(
      (task) => `
    <div class="card stage-card" data-stage-id="${task.stage_id}">
      <div class="stage-body">
        <h3>${escapeHtml(task.title)}</h3>
        <div class="stage-meta">
          <span class="pill pill-amber">${kindLabel[task.assessment_kind] || task.assessment_kind}</span>
        </div>
        ${task.description ? `<p class="muted">${escapeHtml(task.description)}</p>` : ""}
      </div>
      <button class="primary followup-start-button">Start ${icon("arrowRight")}</button>
    </div>`
    )
    .join("");
  return `
    <div class="page-heading" style="margin-top:28px;"><h2>Follow-Up Assessments</h2></div>
    <div class="stage-list">${cards}</div>`;
}

function confirmStartLab(onConfirm) {
  confirmModal({
    title: "Start the lab?",
    message:
      "The lab opens full screen and you must stay in this window while it is open. Switching to another app is blocked. If the window loses focus you get one warning; the second time your lab is submitted automatically. Pasting into the editor is recorded.",
    confirmLabel: "Start",
    onConfirm,
  });
}

function stageStatusLabel(stage) {
  if (stage.status === "submitted") return "Submitted";
  if (stage.status === "in_progress") return "In progress";
  return stage.unlocked ? "Ready to Start" : "Locked";
}

function formatSubmittedAt(iso) {
  if (!iso) return "";
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return isNaN(d) ? "" : `on ${d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
}

function stageButtonLabel(stage) {
  if (stage.status === "in_progress") return "Continue";
  return "Start";
}

function showStages(taskId) {
  Promise.all([
    api().get_stages(taskId),
    api().get_followup_assessments(taskId),
    api().get_lab_resources(taskId),
  ]).then(
    ([data, followups, labResources]) => {
      const steps = data.stages
        .map((stage, index) => {
          const connector =
            index < data.stages.length - 1 ? '<div class="connector"></div>' : "";
          return `
            <div class="step">
              <div class="node ${stage.unlocked ? "unlocked" : "locked"}">${index + 1}</div>
              <div class="step-label">${STAGE_LABELS[stage.stage_type] || stage.stage_type}</div>
            </div>${connector}`;
        })
        .join("");

      const cards = data.stages
        .map(
          (stage, index) => `
        <div class="card stage-card" data-stage-id="${stage.id}" data-unlocked="${stage.unlocked}">
          <div class="stage-number">${index + 1}</div>
          <div class="stage-body">
            <h3>Stage ${index + 1}: ${STAGE_LABELS[stage.stage_type] || stage.stage_type}</h3>
            <div class="stage-meta">
              <span class="pill pill-neutral">AI Assistance: ${MODE_LABELS[stage.ai_assistance_mode] || stage.ai_assistance_mode}</span>
              <span class="pill ${stage.unlocked ? "pill-ready" : "pill-locked"}">${stageStatusLabel(stage)}</span>
            </div>
          </div>
          ${
            data.lab_submitted
              ? `<span class="pill pill-ready">${icon("check")} Done</span>`
              : `<button class="primary stage-start-button" ${stage.unlocked ? "" : "disabled"}>
            ${stage.unlocked ? stageButtonLabel(stage) : "Locked"} ${icon("arrowRight")}
          </button>`
          }
        </div>`
        )
        .join("");

      const resourceSection = labResources.length
        ? `<div class="page-heading" style="margin-top:28px;"><h2>Resources for this lab</h2></div>
           <div class="resource-grid">${labResources.map((r) => resourceCard(r, false)).join("")}</div>`
        : "";
      nextRefresh = { topics: ["labs", "resources"], run: () => showStages(taskId) };
      setScreen(
        `
        <button class="back-link" id="backButton">${icon("back")}Back to Dashboard</button>
        <div class="page-heading">
          <div><h1>${escapeHtml(data.task_title)}</h1></div>
        </div>
        <div class="stepper">${steps}</div>
        ${
          data.lab_submitted
            ? `<div class="card lab-submitted">
                <div><strong>${icon("check")} This lab has been submitted</strong>
                <div class="muted">Submitted ${escapeHtml(formatSubmittedAt(data.submitted_at))}. A lab can only be submitted once, so it can't be changed now.</div>
                ${data.submit_note ? `<div class="muted" style="margin-top:6px;"><b>${escapeHtml(data.submit_note)}.</b></div>` : ""}</div>
                <button class="primary" id="viewSubmission">View submission ${icon("arrowRight")}</button>
              </div>`
            : `<p class="muted stage-hint">Do the stages in order. You can go back and forth between them, and you submit the whole lab once, at the end of the last stage. You can save and leave at any time.</p>`
        }
        <div class="stage-list">${cards}</div>
        ${followupAssessmentCards(followups)}
        ${resourceSection}
      `,
        "home"
      );
      bindResourceCards(app, labResources);

      document.getElementById("backButton").addEventListener("click", showLabs);
      const viewSubmission = document.getElementById("viewSubmission");
      if (viewSubmission) {
        viewSubmission.addEventListener("click", () => showSubmission(data.submitted_session_id));
      }
      app.querySelectorAll(".stage-card").forEach((card) => {
        const start = card.querySelector(".stage-start-button");
        if (card.dataset.unlocked !== "true" || !start) return;
        start.addEventListener("click", () => {
          const stageId = Number(card.dataset.stageId);
          confirmStartLab(() => startStage(stageId));
        });
      });
      app.querySelectorAll(".followup-start-button").forEach((button) => {
        const card = button.closest(".stage-card");
        button.addEventListener("click", () => confirmStartLab(() => startStage(Number(card.dataset.stageId))));
      });
    }
  );
}

// -- Session start -------------------------------------------------------

function startPractice() {
  api()
    .start_practice()
    .then((info) => showWorkspace(info));
}

function startStage(stageId) {
  api()
    .start_stage(stageId)
    .then((info) => showWorkspace(info));
}

// -- Lab mode ------------------------------------------------------------------
//
// While a lab is being taken the window is full screen and the system's ways of
// switching away are blocked (see client/lockdown.py). Because no operating system
// lets an app block everything, losing focus is also counted on the server: the first
// time is a warning, the second submits the lab for the student.

let labMode = null;
const LAB_REFOCUS_GRACE_MS = 600;

function startLabMode(info, getFiles) {
  if (!info.is_stage) {
    stopLabMode();
    return;
  }
  api().enter_lab_mode().catch(() => {});
  if (labMode) {
    labMode.info = info;
    labMode.getFiles = getFiles;
    return;
  }
  const state = { info, getFiles, lost: false, pendingWarning: false, timer: null, graceUntil: Date.now() + 3000 };
  const reportLoss = () => {
    state.lost = true;
    api()
      .record_focus_lost(state.info.session_id)
      .then((result) => {
        if (!labMode || result.action === "none") return;
        if (result.action === "submit") submitBecauseTheyLeft();
        else {
          state.pendingWarning = true;
          showFocusWarning();
        }
      })
      .catch(() => {});
  };
  state.onBlur = () => {
    if (state.lost || Date.now() < state.graceUntil) return;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      // Full-screen changes and dialogs blink focus; only a real departure counts.
      if (!labMode || state.lost || Date.now() < state.graceUntil) return;
      if (document.hasFocus() && document.visibilityState === "visible") return;
      reportLoss();
    }, LAB_REFOCUS_GRACE_MS);
  };
  state.onFocus = () => {
    clearTimeout(state.timer);
    setTimeout(() => {
      if (labMode === state && document.hasFocus()) state.lost = false;
    }, 300);
    showFocusWarning();
  };
  state.onVisibility = () => (document.visibilityState === "hidden" ? state.onBlur() : state.onFocus());
  window.addEventListener("blur", state.onBlur);
  window.addEventListener("focus", state.onFocus);
  document.addEventListener("visibilitychange", state.onVisibility);
  labMode = state;
}

function stopLabMode() {
  if (!labMode) return;
  clearTimeout(labMode.timer);
  window.removeEventListener("blur", labMode.onBlur);
  window.removeEventListener("focus", labMode.onFocus);
  document.removeEventListener("visibilitychange", labMode.onVisibility);
  labMode = null;
  api().leave_lab_mode().catch(() => {});
}

// Something that leaves CAVY on purpose (opening a resource in another app): don't count it.
function pauseLabFocusCheck(ms = 60000) {
  if (labMode) labMode.graceUntil = Date.now() + ms;
}

function showFocusWarning() {
  if (!labMode || !labMode.pendingWarning || !document.hasFocus()) return;
  labMode.pendingWarning = false;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay lab-alert";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>Warning: you left the lab window</h2>
      <p class="muted">You have to stay in this window while the lab is open. This is your one warning:
      if it happens again, your lab is submitted automatically as it is.</p>
      <div class="modal-actions"><button class="primary" id="focusWarningOk">I understand</button></div>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("focusWarningOk").addEventListener("click", () => overlay.remove());
}

function submitBecauseTheyLeft() {
  const state = labMode;
  if (!state) return;
  const { info, getFiles } = state;
  api()
    .submit_session(info.session_id, getFiles(), "focus")
    .then(() => {
      stopLabMode();
      const finish = () => showSubmission(info.session_id);
      document.querySelectorAll(".modal-overlay").forEach((o) => o.remove());
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay lab-alert";
      overlay.innerHTML = `
        <div class="modal-card">
          <h2>Your lab was submitted</h2>
          <p class="muted">You left the lab window a second time, so the lab was submitted automatically with the work you had.
          Your teacher can see that it ended this way.</p>
          <div class="modal-actions"><button class="primary" id="labEndedOk">View submission</button></div>
        </div>`;
      document.body.appendChild(overlay);
      document.getElementById("labEndedOk").addEventListener("click", () => {
        overlay.remove();
        finish();
      });
    })
    .catch((err) => showToast(errorText(err)));
}

// -- Workspace -------------------------------------------------------

function goBackFromWorkspace(info) {
  if (info.is_stage) {
    showStages(info.task_id);
  } else {
    showLabs();
  }
}

function showConceptCheckModal(sessionId, onDone) {
  api()
    .get_concept_check_question()
    .then((question) => {
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.innerHTML = `
        <div class="modal-card">
          <h2>Before you submit</h2>
          <p class="muted">${escapeHtml(question)}</p>
          <textarea id="conceptCheckInput" rows="5" placeholder="Type your explanation here..."></textarea>
          <div class="modal-actions">
            <button class="ghost" id="conceptCheckSkip">Skip</button>
            <button class="primary" id="conceptCheckSubmit">Submit Explanation</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);

      const close = () => overlay.remove();
      document.getElementById("conceptCheckSkip").addEventListener("click", () => {
        close();
        onDone();
      });
      document.getElementById("conceptCheckSubmit").addEventListener("click", () => {
        const text = document.getElementById("conceptCheckInput").value.trim();
        close();
        if (text) {
          api()
            .submit_concept_check(sessionId, text)
            .then(onDone);
        } else {
          onDone();
        }
      });
    });
}

function showWorkspace(info) {
  const chatColumn =
    info.ai_assistance_mode === "NONE" ? "" : `<div class="chat-column" id="chatColumn"></div>`;

  const pills = [`<span class="pill pill-amber">${icon("sparkle")}${MODE_LABELS[info.ai_assistance_mode] || info.ai_assistance_mode}</span>`];
  if (info.stage_type) {
    pills.unshift(`<span class="pill pill-neutral">${STAGE_LABELS[info.stage_type] || info.stage_type}</span>`);
  }
  if (info.difficulty) {
    pills.push(`<span class="pill pill-neutral">${icon("clock")}Difficulty: ${escapeHtml(info.difficulty)}</span>`);
  }
  if (info.is_stage) {
    pills.push(`<span class="pill pill-amber" title="Full screen. Leaving this window twice submits the lab.">Lab mode</span>`);
  }
  if (info.duration_minutes) {
    pills.push(`<span class="pill pill-neutral">${icon("clock")}Est. Time: ${info.duration_minutes}m</span>`);
  }

  const actionButtons = info.is_stage
    ? `<button class="ghost-icon" id="saveButton">${icon("save")}Save</button><span class="stage-nav" id="stageNav"></span>`
    : "";

  setScreen(
    `
    <div class="workspace">
      <div class="task-header">
        <div class="icon-tile">${icon("file")}</div>
        <div class="task-title-row">
          <h2>${escapeHtml(info.task_title)}</h2>
          <div class="meta-pills">
            ${pills.join("")}
            ${info.task_description ? `<button class="ghost toggle-description" id="toggleDescription">Hide Description</button>` : ""}
          </div>
        </div>
      </div>
      ${info.task_description ? `<div class="card problem-statement" id="problemStatement">${escapeHtml(info.task_description)}</div>` : ""}
      <div class="workspace-body" id="workspaceBody">
        <div class="editor-column">
          <div class="editor-shell" id="editorShell">
            <div class="editor-toolbar">
              <div class="editor-tabs" id="editorTabs"></div>
              <div class="editor-toolbar-actions">
                <button class="run-button" id="runButton">${icon("arrowRight")}Run Code</button>
                <button class="ghost-icon" id="resetButton">${icon("refresh")}Reset</button>
                ${actionButtons}
                <button class="icon-only" id="moreButton" title="More actions (coming soon)">${icon("moreHoriz")}</button>
                <button class="icon-only" id="expandButton" title="Focus mode">${icon("expand")}</button>
              </div>
            </div>
            <div class="editor-container" id="editorContainer"></div>
            <div class="editor-status-bar">
              <span>Python</span>
              <span id="cursorPosition">Ln —, Col —</span>
              <span>Spaces: 4</span>
              <span class="spacer"></span>
              <span class="autosaved" id="autosaveIndicator">Not saved yet</span>
            </div>
          </div>
          <div class="output-shell hidden" id="outputShell">
            <div class="output-header">Output &middot; <span id="outputFilename"></span></div>
            <div class="output-pane" id="outputPane"></div>
          </div>
        </div>
        ${chatColumn}
      </div>
    </div>
  `,
    null,
    { keepSidebar: true }
  );

  // Leaving the coding screen keeps the work: it is saved first, so coming
  // back (to this stage or via Back/Next) shows the same code.
  function saveNow() {
    return api().log_code_edit(info.session_id, currentFiles(), activeFilename, false, true);
  }
  function leaveWorkspace() {
    const saved = info.is_stage ? saveNow() : Promise.resolve();
    saved.then(() => goBackFromWorkspace(info), () => goBackFromWorkspace(info));
  }
  function navigateToStage(stageId) {
    saveNow().then(
      () => startStage(stageId),
      () => startStage(stageId)
    );
  }

  nextRefresh = { topics: ["resources"], run: () => loadSessionResources(info) };
  startLabMode(info, () => currentFiles());
  renderWorkspaceHeaderExtra(info.duration_minutes, leaveWorkspace, info.elapsed_seconds || 0);

  if (info.is_stage) {
    api()
      .get_stages(info.task_id)
      .then((data) => {
        renderWorkspaceSidebar(info, data.stages, navigateToStage, leaveWorkspace);
        renderStageNav(data.stages);
      });
  } else {
    renderWorkspaceSidebar(info, null, null, null);
  }

  // Back / Next move between the lab's stages; only the last stage submits.
  function renderStageNav(stages) {
    const nav = document.getElementById("stageNav");
    if (!nav) return;
    const index = stages.findIndex((stage) => stage.id === info.stage_id);
    const previous = stages[index - 1];
    const next = stages[index + 1];
    const name = (stage) => STAGE_LABELS[stage.stage_type] || stage.stage_type;
    nav.innerHTML = `
      ${previous ? `<button class="ghost-icon" id="previousStageButton">${icon("back")}${escapeHtml(name(previous))}</button>` : ""}
      ${
        next
          ? `<button class="accent" id="nextStageButton">Next: ${escapeHtml(name(next))} ${icon("arrowRight")}</button>`
          : `<button class="accent" id="submitButton">${stages.length > 1 ? "Submit Lab" : "Submit"} ${icon("arrowRight")}</button>`
      }`;
    if (previous) {
      document.getElementById("previousStageButton").addEventListener("click", () => navigateToStage(previous.id));
    }
    if (next) {
      document.getElementById("nextStageButton").addEventListener("click", () => navigateToStage(next.id));
      return;
    }
    document.getElementById("submitButton").addEventListener("click", () => {
      const doSubmit = () =>
        api()
          .submit_session(info.session_id, currentFiles())
          .then(() => showSubmission(info.session_id))
          .catch((err) => showToast(errorText(err)));
      const proceed = () =>
        info.stage_type === "ASSESSMENT" ? showConceptCheckModal(info.session_id, doSubmit) : doSubmit();
      if (stages.length > 1) {
        confirmModal({
          title: "Submit the whole lab?",
          message: `Your ${stages.map(name).join(", ")} work is submitted together. You can't change it afterwards.`,
          confirmLabel: "Submit lab",
          onConfirm: proceed,
        });
      } else {
        proceed();
      }
    });
  }

  if (info.task_description) {
    document.getElementById("toggleDescription").addEventListener("click", (e) => {
      const statement = document.getElementById("problemStatement");
      const collapsed = statement.classList.toggle("collapsed");
      e.target.textContent = collapsed ? "View Full Description" : "Hide Description";
    });
  }

  workspaceFiles = { ...info.starter_files };
  activeFilename = "main.py";

  const editorContainer = document.getElementById("editorContainer");
  const autosaveIndicator = document.getElementById("autosaveIndicator");

  function currentFiles() {
    return currentEditor ? { ...workspaceFiles, [activeFilename]: currentEditor.getValue() } : { ...workspaceFiles };
  }

  function saveActiveFileToMemory() {
    if (currentEditor) {
      workspaceFiles[activeFilename] = currentEditor.getValue();
    }
  }

  function renderFileTabs() {
    const tabs = Object.keys(workspaceFiles)
      .map((name) => {
        const closeButton =
          name === "main.py" ? "" : `<span class="close-tab" data-close="${escapeHtml(name)}">&times;</span>`;
        return `
          <div class="file-tab ${name === activeFilename ? "active" : ""}" data-filename="${escapeHtml(name)}">
            ${icon("file")}${escapeHtml(name)}${closeButton}
          </div>`;
      })
      .join("");
    document.getElementById("editorTabs").innerHTML = `
      ${tabs}
      <div class="new-tab" id="newFileButton" title="Add a file">+</div>
    `;

    document.querySelectorAll(".file-tab").forEach((tab) => {
      tab.addEventListener("click", (e) => {
        if (e.target.classList.contains("close-tab")) return;
        switchToFile(tab.dataset.filename);
      });
    });
    document.querySelectorAll(".close-tab").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        closeFile(btn.dataset.close);
      });
    });
    document.getElementById("newFileButton").addEventListener("click", addFile);
  }

  function switchToFile(filename) {
    if (filename === activeFilename) return;
    saveActiveFileToMemory();
    activeFilename = filename;
    if (currentEditor) currentEditor.setValue(workspaceFiles[filename] || "");
    renderFileTabs();
    // Last run's output belonged to whichever file was active then — once
    // the active file changes, that output no longer describes what's on
    // screen, so hide it rather than leave a stale result visible.
    const outputFilename = document.getElementById("outputFilename");
    if (outputFilename && outputFilename.textContent !== filename) {
      document.getElementById("outputShell").classList.add("hidden");
    }
  }

  function addFile() {
    const name = window.prompt("New file name (e.g. helper.py):", "helper.py");
    if (!name) return;
    if (!/^[\w.-]+\.py$/.test(name)) {
      window.alert("File names must end in .py");
      return;
    }
    if (workspaceFiles[name] !== undefined) {
      window.alert(`${name} already exists.`);
      return;
    }
    saveActiveFileToMemory();
    workspaceFiles[name] = "";
    switchToFile(name);
  }

  function closeFile(filename) {
    saveActiveFileToMemory();
    delete workspaceFiles[filename];
    if (activeFilename === filename) {
      activeFilename = "main.py";
      if (currentEditor) currentEditor.setValue(workspaceFiles["main.py"] || "");
    }
    renderFileTabs();
    const outputFilename = document.getElementById("outputFilename");
    if (outputFilename && outputFilename.textContent === filename) {
      document.getElementById("outputShell").classList.add("hidden");
    }
  }

  renderFileTabs();
  createEditor(editorContainer, workspaceFiles[activeFilename]).then((editor) => {
    currentEditor = editor;
    editor.onChange(() => {
      clearTimeout(debounceTimer);
      autosaveIndicator.textContent = "Saving...";
      debounceTimer = setTimeout(() => {
        api()
          .log_code_edit(info.session_id, currentFiles(), activeFilename)
          .then(() => {
            autosaveIndicator.textContent = "Autosaved just now";
          });
      }, CODE_EDIT_DEBOUNCE_MS);
    });
    // Anything pasted or dropped in is recorded, and the server notes whether it came from the AI.
    editor.onPaste((text) => {
      api().record_paste(info.session_id, text).catch(() => {});
    });
    editor.onCursorMove((line, col) => {
      document.getElementById("cursorPosition").textContent = `Ln ${line}, Col ${col}`;
    });
  });

  document.getElementById("runButton").addEventListener("click", () => {
    const runButton = document.getElementById("runButton");
    const outputShell = document.getElementById("outputShell");
    const outputFilename = document.getElementById("outputFilename");
    const output = document.getElementById("outputPane");
    const ranFilename = activeFilename;
    outputShell.classList.remove("hidden");
    outputFilename.textContent = ranFilename;
    runButton.disabled = true;
    output.textContent = "Running...";
    api()
      .run_code(info.session_id, currentFiles(), ranFilename)
      .then((outcome) => {
        runButton.disabled = false;
        // The active tab may have changed while the run was in flight —
        // only show this result if it's still what the output is labeled
        // for, so a slow run never overwrites a newer one's output.
        if (outputFilename.textContent !== ranFilename) return;
        if (outcome.timed_out) {
          output.textContent = "Timed out.";
        } else {
          const text = outcome.stdout + (outcome.stderr ? "\n" + outcome.stderr : "");
          output.textContent = text || "(no output)";
        }
      });
  });

  document.getElementById("resetButton").addEventListener("click", () => {
    workspaceFiles = { ...info.starter_files };
    activeFilename = "main.py";
    if (currentEditor) currentEditor.setValue(workspaceFiles["main.py"] || "");
    renderFileTabs();
    document.getElementById("outputShell").classList.add("hidden");
    api().log_code_edit(info.session_id, currentFiles(), activeFilename, true, false);
  });

  document.getElementById("expandButton").addEventListener("click", () => {
    document.getElementById("workspaceBody").classList.toggle("focus-mode");
  });

  if (info.is_stage) {
    document.getElementById("saveButton").addEventListener("click", () => {
      api().log_code_edit(info.session_id, currentFiles(), activeFilename, false, true);
      autosaveIndicator.textContent = "Autosaved just now";
    });
  }

  if (info.ai_assistance_mode !== "NONE") {
    renderChatPanel(document.getElementById("chatColumn"), info, currentFiles);
  }
}

const CHAT_CAPABILITIES = [
  "Understanding the problem",
  "Explaining errors",
  "Suggesting approaches",
  "Code examples",
];

const CHAT_SUGGESTIONS = {
  chat: [
    "How do I get started on this?",
    "Can you explain the error I'm seeing?",
    "Is my approach on the right track?",
  ],
  examples: [
    "Show me an example of reading a file in Python",
    "Give me an example of a for-loop over a list",
    "Show an example of handling an exception",
  ],
};

function renderChatPanel(container, info, getFiles) {
  const restricted = info.ai_assistance_mode === "RESTRICTED";
  container.innerHTML = `
    <div class="chat-panel">
      <div class="chat-panel-header">
        ${icon("sparkle")}AI Assistant
        <span class="spacer"></span>
        <span class="info-hint" title="Chat is grounded in this task, your current code, and your last run's output.">i</span>
      </div>
      <div class="chat-status" id="chatStatus">${restricted ? "AI assistance is restricted during this stage" : "Checking assistant..."}</div>
      ${
        restricted
          ? ""
          : `
        <div class="chat-tabs">
          <button class="active" data-tab="chat">Chat</button>
          <button data-tab="examples">Examples</button>
        </div>
        <div id="chatEmptyState">
          <div class="chat-intro">
            Hi! I can help you with:
            <ul>${CHAT_CAPABILITIES.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
          </div>
          <div class="chat-tip">Try solving the task on your own first. Use me when you're stuck or want to verify your approach.</div>
          <div class="chat-suggestions" id="chatSuggestions"></div>
        </div>`
      }
      <div class="chat-transcript" id="chatTranscript"></div>
      <div class="chat-input-row">
        <input type="text" id="chatInput" placeholder="Ask anything..." ${restricted ? "disabled" : ""} />
        <button class="accent" id="chatSendButton" ${restricted ? "disabled" : ""}>${icon("send")}</button>
      </div>
      ${restricted ? "" : `<div class="chat-disclaimer">AI can make mistakes. Verify important information.</div>`}
    </div>
  `;

  if (restricted) return;

  const refreshChatStatus = () =>
    api()
      .get_ai_settings()
      .then((settings) => {
        const el = document.getElementById("chatStatus");
        if (!el) return;
        if (settings.available) {
          el.textContent = settings.scope === "personal" ? "Assistant ready (your key)" : "Assistant ready";
          return;
        }
        el.innerHTML = `Assistant not available — <button class="auth-link" id="chatSetupLink">Connect one</button>`;
        document.getElementById("chatSetupLink").addEventListener("click", () => {
          showAiSetupModal(settings, refreshChatStatus);
        });
      });
  refreshChatStatus();

  const input = document.getElementById("chatInput");
  const sendButton = document.getElementById("chatSendButton");
  const transcript = document.getElementById("chatTranscript");
  const status = document.getElementById("chatStatus");
  const emptyState = document.getElementById("chatEmptyState");

  function renderSuggestions(tab) {
    document.getElementById("chatSuggestions").innerHTML = CHAT_SUGGESTIONS[tab]
      .map((text) => `<button data-suggestion="${escapeHtml(text)}">${escapeHtml(text)}</button>`)
      .join("");
    document.querySelectorAll("#chatSuggestions button").forEach((btn) => {
      btn.addEventListener("click", () => {
        input.value = btn.dataset.suggestion;
        send();
      });
    });
  }
  renderSuggestions("chat");

  document.querySelectorAll(".chat-tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".chat-tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderSuggestions(btn.dataset.tab);
    });
  });

  const send = () => {
    const message = input.value.trim();
    if (!message) return;
    if (emptyState) emptyState.remove();
    transcript.innerHTML += `<div class="msg you"><b>You:</b> ${escapeHtml(message)}</div>`;
    transcript.scrollTop = transcript.scrollHeight;
    input.value = "";
    input.disabled = true;
    sendButton.disabled = true;
    status.textContent = "Thinking...";

    api()
      .send_ai_message(info.session_id, message, getFiles())
      .then((result) => {
        input.disabled = false;
        sendButton.disabled = false;
        if (result.available) {
          transcript.innerHTML += `<div class="msg assistant"><b>Assistant:</b>${renderMarkdown(result.text)}</div>`;
          status.textContent = "Assistant ready";
        } else {
          const why = result.text ? ` (${escapeHtml(String(result.text))})` : "";
          transcript.innerHTML += `<div class="msg assistant"><i>The assistant didn't respond — it may not be running.${why}</i></div>`;
          status.textContent = "Assistant not available";
        }
        transcript.scrollTop = transcript.scrollHeight;
      });
  };

  sendButton.addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });
}

// -- Submission screen -------------------------------------------------------

function showSubmission(sessionId) {
  api()
    .get_submission_summary(sessionId)
    .then((summary) => {
      setScreen(
        `
        <button class="back-link" id="backButton">${icon("back")}Back to Lab</button>
        <div class="submission-wrap">
          <div class="checkmark">&#10003;</div>
          <h1>${escapeHtml(summary.label || "Work")} Submitted Successfully</h1>
          <p class="muted">Your submission has been successfully submitted for evaluation.</p>
          <div class="submission-details">
            <div class="card">
              <span class="meta-icon">${icon("clock")}</span>
              <p class="muted">Submission Time</p>
              <p><b>${escapeHtml(summary.submitted_at ? new Date(/[zZ]|[+-]\d\d:\d\d$/.test(summary.submitted_at) ? summary.submitted_at : summary.submitted_at + "Z").toLocaleString() : "-")}</b></p>
            </div>
            <div class="card">
              <span class="meta-icon">${icon("check")}</span>
              <p class="muted">Execution Status</p>
              <p><b>${escapeHtml(summary.exit_status || "-")}</b></p>
            </div>
          </div>
          <div class="submission-actions">
            <button id="backToDashboardButton">${icon("back")}Back to Dashboard</button>
            <button class="primary" id="viewScoreButton">View CIQ Score ${icon("arrowRight")}</button>
          </div>
        </div>
      `,
        "home"
      );
      document.getElementById("backButton").addEventListener("click", showLabs);
      document.getElementById("backToDashboardButton").addEventListener("click", showLabs);
      document
        .getElementById("viewScoreButton")
        .addEventListener("click", () => showCiqScore(sessionId));
    });
}

// -- CIQ score screen -------------------------------------------------------

const SIGNAL_NAMES = {
  "S1.1": "Help-Seeking Calibration",
  "S1.2": "Student Grounding",
  "S1.3": "Response Utilization",
  "S1.4": "Modification & Verification",
  "S1.5": "Follow-up Engagement",
  "S1.6": "Adaptive AI Use",
  "S2.1": "Independent Initiation",
  "S2.2": "Reasoning Continuity",
  "S2.3": "Problem-Solving Agency",
  "S2.4": "Evidence-Based Error Recovery",
  "S3.1": "Conceptual Understanding",
  "S3.2": "Knowledge Application",
  "S3.3": "Knowledge Transfer",
  "S3.4": "Retention & Independent Recall",
};

function showCiqScore(sessionId) {
  api()
    .get_ciq_score(sessionId)
    .then((data) => {
      const pillars = data.pillars
        .map((pillar) => {
          const rows = pillar.signals
            .map((s) => {
              const name = SIGNAL_NAMES[s.key] || s.key;
              if (s.value === null) {
                return `
                  <div class="signal-row">
                    <span class="signal-name">${s.key} &middot; ${escapeHtml(name)}</span>
                    <span class="signal-pending">${escapeHtml(s.reason || "Not yet available")}</span>
                  </div>`;
              }
              const pct = Math.round(s.value * 100);
              return `
                <div class="signal-row">
                  <span class="signal-name">${s.key} &middot; ${escapeHtml(name)}</span>
                  <span class="signal-bar-track"><span class="signal-bar-fill" style="width:${pct}%"></span></span>
                  <span class="signal-value">${pct}%</span>
                </div>`;
            })
            .join("");
          return `
            <div class="card pillar-card">
              <h3>${escapeHtml(pillar.heading)}</h3>
              <p class="muted">${escapeHtml(pillar.question)}</p>
              ${rows}
            </div>`;
        })
        .join("");

      setScreen(
        `
        <button class="back-link" id="backButton">${icon("back")}Back to Dashboard</button>
        <div class="page-heading"><div><h1>CIQ Score</h1><p class="subtitle">Cognitive Interaction Quotient for this session.</p></div></div>
        <div class="card" style="margin-bottom:14px;">
          <h3 style="font-size:14px;">Raw evidence (Layer 1)</h3>
          <p class="muted">${data.evidence.event_count} events logged &middot; ${data.evidence.snapshot_count} code snapshots &middot; ${data.evidence.interaction_count} AI interactions</p>
        </div>
        <div class="pillar-list">${pillars}</div>
      `,
        "home"
      );
      document.getElementById("backButton").addEventListener("click", showLabs);
    });
}

// -- My Progress (student's long-run learning) ------------------------------

const TREND_TEXT = {
  up: "Improving",
  down: "Slipping",
  steady: "Steady",
  not_enough_data: "Needs more sessions",
};

function sparkline(series, width = 120, height = 32) {
  if (series.length < 2) return "";
  const step = width / (series.length - 1);
  const points = series
    .map((v, i) => `${(i * step).toFixed(1)},${(height - (v / 100) * height).toFixed(1)}`)
    .join(" ");
  return `<svg class="sparkline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

function trendChart(series) {
  if (series.length < 2) {
    return `<p class="muted">Complete at least two sessions to see your trend.</p>`;
  }
  const w = 640;
  const h = 180;
  const pad = { l: 34, r: 12, t: 10, b: 22 };
  const x = (i) => pad.l + (i * (w - pad.l - pad.r)) / (series.length - 1);
  const y = (v) => pad.t + (1 - v / 100) * (h - pad.t - pad.b);
  const grid = [0, 50, 100]
    .map(
      (v) =>
        `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(v)}" y2="${y(v)}" class="chart-grid"/><text x="${pad.l - 6}" y="${y(v) + 4}" class="chart-label" text-anchor="end">${v}</text>`
    )
    .join("");
  const line = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const dots = series
    .map(
      (v, i) =>
        `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3.5" class="chart-dot"><title>Session ${i + 1}: ${v}</title></circle>`
    )
    .join("");
  return `<svg class="trend-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="CIQ over your sessions">${grid}<polyline points="${line}" class="chart-line"/>${dots}<text x="${pad.l}" y="${h - 4}" class="chart-label">Oldest</text><text x="${w - pad.r}" y="${h - 4}" class="chart-label" text-anchor="end">Latest</text></svg>`;
}

function trendBadge(trend) {
  const text = TREND_TEXT[trend.direction] || "";
  const change =
    trend.change === null || trend.direction === "steady"
      ? ""
      : ` (${trend.change > 0 ? "+" : ""}${trend.change})`;
  return `<span class="trend trend-${trend.direction}">${text}${change}</span>`;
}

function formatMinutes(total) {
  if (total < 60) return `${total} min`;
  return `${Math.floor(total / 60)} h ${total % 60} min`;
}

function signalChips(items, emptyText) {
  if (!items.length) return `<p class="muted">${emptyText}</p>`;
  return items
    .map(
      (s) =>
        `<div class="signal-row"><span class="signal-name">${s.key} &middot; ${escapeHtml(SIGNAL_NAMES[s.key] || s.key)}</span><span class="signal-bar-track"><span class="signal-bar-fill" style="width:${s.score}%"></span></span><span class="signal-value">${Math.round(s.score)}%</span></div>`
    )
    .join("");
}

// The progress charts and tables, shared by a student's own My Progress screen and
// the professor's view of one student (``forProfessor`` swaps the Details button for
// the student's submitted code).
function progressBodyHtml(data, forProfessor) {
  const t = data.totals;
  const stat = (label, value) =>
    `<div class="card progress-stat"><div class="progress-stat-value">${value}</div><div class="muted">${label}</div></div>`;
  const pillars = data.pillars
    .map(
      (p) => `
        <div class="card pillar-progress">
          <h3>${escapeHtml(p.heading)}</h3>
          <div class="pillar-score">${p.latest === null ? "&ndash;" : Math.round(p.latest)}<span class="muted"> latest</span></div>
          ${sparkline(p.series)}
          <div>${trendBadge(p)}</div>
          <div class="muted">Average ${p.average === null ? "&ndash;" : Math.round(p.average)}</div>
        </div>`
    )
    .join("");
  const kindLabel = { lab: "Lab", practice: "Practice", followup: "Follow-up" };
  const rows = data.history
    .map(
      (h) => `
        <tr>
          <td>${new Date(h.submitted_at).toLocaleDateString()}</td>
          <td>${escapeHtml(h.title)}${h.stage ? ` <span class="muted">&middot; ${escapeHtml(STAGE_LABELS[h.stage] || h.stage)}</span>` : ""}</td>
          <td>${kindLabel[h.kind] || h.kind}</td>
          <td>${h.ai_interactions}</td>
          <td>${h.score === null ? "&ndash;" : Math.round(h.score)}</td>
          <td><button class="ghost progress-detail" data-session-id="${h.session_id}">${forProfessor ? "View code" : "Details"}</button></td>
        </tr>`
    )
    .join("");
  return `
        <div class="progress-stat-grid">
          ${stat("Sessions submitted", t.sessions)}
          ${stat("Labs attempted", t.labs)}
          ${stat("Practice sessions", t.practice)}
          ${stat("Time spent", formatMinutes(t.minutes))}
          ${stat("Average CIQ", t.average_score === null ? "&ndash;" : Math.round(t.average_score))}
        </div>
        <div class="card" style="margin-top:14px;">
          <div class="chart-head"><h3>CIQ over time</h3>${trendBadge(data.overall)}</div>
          ${trendChart(data.overall.series)}
        </div>
        <div class="pillar-progress-grid">${pillars}</div>
        <div class="two-col">
          <div class="card"><h3>Strongest areas</h3>${signalChips(data.strengths, "Not enough data yet.")}</div>
          <div class="card"><h3>Room to grow</h3>${signalChips(data.growth_areas, "Not enough data yet.")}</div>
        </div>
        <div class="card" style="margin-top:14px;">
          <h3>Session history</h3>
          <table class="data-table progress-history-table">
            <thead><tr><th>Date</th><th>Session</th><th>Type</th><th>AI chats</th><th>CIQ</th><th></th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
          <p class="muted" style="margin-top:10px;">CIQ here is a provisional equal-weight average of the signals available for each session, not a validated grade.</p>
        </div>`;
}

function showMyProgress() {
  const heading = `<div class="page-heading"><div><h1>My Progress</h1><p class="subtitle">How your learning is developing across sessions.</p></div></div>`;
  setScreen(
    `${heading}
     <div class="card"><p class="muted">Loading your progress&hellip; sessions that haven't been scored yet are scored now, which can take a moment.</p></div>`,
    "progress"
  );
  api()
    .get_my_progress()
    .then((data) => {
      if (!data.totals.sessions) {
        setScreen(
          `${heading}
           <div class="card"><h3>Nothing to show yet</h3><p class="muted">Submit your first lab or practice session and your progress will appear here.</p></div>`,
          "progress"
        );
        return;
      }
      setScreen(`${heading}${progressBodyHtml(data, false)}`, "progress");
      app.querySelectorAll(".progress-detail").forEach((button) => {
        button.addEventListener("click", () => showCiqScore(Number(button.dataset.sessionId)));
      });
    });
}

// A professor looking at one student's progress: the same charts the student sees.
function showStudentProgress(studentId, backLabel = "Back to My Class", back = showStudents) {
  setScreen(
    `<button class="back-link" id="progressBack">${icon("back")}${escapeHtml(backLabel)}</button>
     <div class="card"><p class="muted">Loading progress&hellip; sessions that haven't been scored yet are scored now, which can take a moment.</p></div>`,
    "students"
  );
  document.getElementById("progressBack").addEventListener("click", back);
  api()
    .get_student_progress(studentId)
    .then((data) => {
      const place = [data.student.course, data.student.year_label, data.student.division && `Div ${data.student.division}`, data.student.batch && `Batch ${data.student.batch}`]
        .filter(Boolean)
        .join(" · ");
      const heading = `<button class="back-link" id="progressBack">${icon("back")}${escapeHtml(backLabel)}</button>
        <div class="page-heading"><div><h1>${escapeHtml(data.student.name)}</h1><p class="subtitle">${escapeHtml(place || "No course set")}${data.student.roll_number ? ` &middot; Roll ${escapeHtml(data.student.roll_number)}` : ""}</p></div></div>`;
      const body = data.totals.sessions
        ? progressBodyHtml(data, true)
        : `<div class="card"><h3>Nothing to show yet</h3><p class="muted">${escapeHtml(data.student.name)} hasn't submitted a lab or practice session yet.</p></div>`;
      setScreen(heading + body, "students");
      document.getElementById("progressBack").addEventListener("click", back);
      app.querySelectorAll(".progress-detail").forEach((button) => {
        button.addEventListener("click", () =>
          api()
            .get_session_submission(Number(button.dataset.sessionId))
            .then((work) => showStudentWork(work, () => showStudentProgress(studentId, backLabel, back), "Back to progress"))
            .catch((err) => showToast(errorText(err)))
        );
      });
    })
    .catch((err) => {
      showToast(errorText(err));
      back();
    });
}

// A student's submitted code and the output of their last run, stage by stage.
function showStudentWork(work, back, backLabel = "Back") {
  const stageName = (type) => STAGE_LABELS[type] || "Submission";
  const place = [work.student.course, work.student.year_label, work.student.division && `Div ${work.student.division}`, work.student.batch && `Batch ${work.student.batch}`]
    .filter(Boolean)
    .join(" · ");
  setScreen(
    `<button class="back-link" id="workBack">${icon("back")}${escapeHtml(backLabel)}</button>
     <div class="page-heading"><div><h1>${escapeHtml(work.student.name)}</h1>
       <p class="subtitle">${escapeHtml(work.task_title)}${place ? ` &middot; ${escapeHtml(place)}` : ""}${work.score === null ? "" : ` &middot; CIQ ${work.score}`}</p></div></div>
     <div class="stage-tabs" id="workStageTabs">${work.stages
       .map(
         (stage, index) =>
           `<button class="stage-tab ${index === work.stages.length - 1 ? "active" : ""}" data-index="${index}">${escapeHtml(stageName(stage.stage_type))}${stage.submitted ? "" : " <span class='muted'>(not submitted)</span>"}</button>`
       )
       .join("")}</div>
     <div id="workStage"></div>`,
    "reports"
  );
  document.getElementById("workBack").addEventListener("click", back);
  const showStage = (index) => {
    const stage = work.stages[index];
    document.querySelectorAll("#workStageTabs .stage-tab").forEach((tab, i) => tab.classList.toggle("active", i === index));
    const names = Object.keys(stage.files);
    const out = stage.output;
    document.getElementById("workStage").innerHTML = `
      <div class="muted work-meta">${
        stage.submitted_at ? `Submitted ${escapeHtml(new Date(stage.submitted_at).toLocaleString())}` : "Not submitted"
      } &middot; ${stage.ai_chats} AI chat${stage.ai_chats === 1 ? "" : "s"}${
        stage.pastes.ai + stage.pastes.other
          ? ` &middot; <span class="paste-flag">pasted ${stage.pastes.ai ? `${stage.pastes.ai}\u00d7 from the AI (${stage.pastes.ai_chars} chars)` : ""}${stage.pastes.ai && stage.pastes.other ? ", " : ""}${stage.pastes.other ? `${stage.pastes.other}\u00d7 from elsewhere (${stage.pastes.other_chars} chars)` : ""}</span>`
          : ""
      }${stage.focus_losses ? ` &middot; <span class="paste-flag">left the lab window ${stage.focus_losses}\u00d7</span>` : ""}</div>
      ${stage.note ? `<div class="notice notice-warn" style="margin:0 0 10px;">${escapeHtml(stage.note)}</div>` : ""}
      ${
        names.length
          ? names
              .map(
                (name) => `<div class="card work-file"><div class="work-file-name">${icon("file")}${escapeHtml(name)}</div><pre class="code-view">${escapeHtml(stage.files[name] || "(empty)")}</pre></div>`
              )
              .join("")
          : `<div class="card"><p class="muted">No code was saved for this stage.</p></div>`
      }
      <div class="card work-output">
        <div class="work-file-name">Output of the last run</div>
        ${
          out
            ? `<pre class="code-view output-view">${escapeHtml(out.stdout || "")}${out.stderr ? `<span class="output-error">${escapeHtml(out.stderr)}</span>` : ""}${out.timed_out ? `<span class="output-error">\n(timed out)</span>` : ""}${!out.stdout && !out.stderr && !out.timed_out ? "(no output)" : ""}</pre>`
            : `<p class="muted">The student never ran this code.</p>`
        }
      </div>
      ${
        stage.explanation
          ? `<div class="card"><div class="work-file-name">Explanation they wrote before submitting</div><p class="muted">${escapeHtml(stage.explanation.question)}</p><p style="white-space:pre-wrap">${escapeHtml(stage.explanation.response)}</p></div>`
          : ""
      }`;
  };
  document.querySelectorAll("#workStageTabs .stage-tab").forEach((tab) =>
    tab.addEventListener("click", () => showStage(Number(tab.dataset.index)))
  );
  if (work.stages.length) showStage(work.stages.length - 1);
  else document.getElementById("workStage").innerHTML = `<div class="card"><p class="muted">Nothing has been saved yet.</p></div>`;
}

// -- Live updates -------------------------------------------------------------
//
// When connected to a server, the app holds a request open ("long poll"). The
// server answers the moment something changes (a lab is published, a student
// submits, ...), and the current screen redraws itself. Nothing refreshes while
// a dialog is open or someone is typing, so it never steals focus or input.

let liveLoopId = 0;
let liveVersions = {};
let liveChangedTopics = new Set();
let liveRetryTimer = null;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function isSignedOutError(err) {
  if (!err) return false;
  if (err.name === "PermissionError") return true;
  return /signed out by an administrator|session expired|sign in again|account was (disabled|removed)|password was reset|sign-in details were changed/i.test(
    err.message || ""
  );
}

let loginNotice = "";

function handleSignedOut(err) {
  stopLiveUpdates();
  currentRole = null;
  loginNotice = errorText(err);
  showLogin();
}

function stopLiveUpdates() {
  liveLoopId += 1;
  liveVersions = {};
  liveChangedTopics = new Set();
  clearTimeout(liveRetryTimer);
}

function userIsBusy() {
  if (document.querySelector(".modal-overlay")) return true;
  const el = document.activeElement;
  return !!(el && app.contains(el) && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

function applyLiveChange() {
  if (!currentRefresh) {
    liveChangedTopics.clear();
    return;
  }
  const relevant = currentRefresh.topics.some((t) => liveChangedTopics.has(t));
  if (!relevant) {
    liveChangedTopics.clear();
    return;
  }
  if (userIsBusy()) {
    // Try again shortly rather than interrupting.
    clearTimeout(liveRetryTimer);
    liveRetryTimer = setTimeout(applyLiveChange, 1500);
    return;
  }
  liveChangedTopics.clear();
  pendingScroll = app.scrollTop;
  currentRefresh.run();
}

function startLiveUpdates() {
  stopLiveUpdates();
  if (!serverMode) return;
  const loopId = liveLoopId;
  const tick = async () => {
    while (loopId === liveLoopId) {
      try {
        const known = liveVersions;
        const first = Object.keys(known).length === 0 && !startLiveUpdates.seeded;
        const result = await api().wait_for_updates(known, !first);
        if (loopId !== liveLoopId) return;
        if (!result.live) return;
        startLiveUpdates.seeded = true;
        if (!first) {
          Object.keys(result.versions).forEach((topic) => {
            if (result.versions[topic] > (known[topic] || 0)) liveChangedTopics.add(topic);
          });
        }
        liveVersions = result.versions;
        if (liveChangedTopics.size) applyLiveChange();
      } catch (err) {
        if (loopId !== liveLoopId) return;
        if (isSignedOutError(err)) {
          handleSignedOut(err);
          return;
        }
        await sleep(3000); // a network blip: try again
      }
    }
  };
  startLiveUpdates.seeded = false;
  tick();
}

// -- Bootstrap -------------------------------------------------------

function enterApp(role, name, mustChangePassword = false) {
  loginNotice = "";
  startLiveUpdates();
  currentRole = role;
  currentUserName = name;
  document.getElementById("root").classList.remove("auth-mode");
  document.getElementById("studentAvatar").textContent = (name || "?").charAt(0).toUpperCase();
  document.getElementById("studentAvatar").title = name || "";
  renderSidebar();
  if (mustChangePassword) {
    showProfile();
  } else if (role === "professor") {
    showMyLabs();
    maybePromptAiSetup();
  } else {
    showLabs();
    maybePromptAiSetup();
  }
}

function init() {
  showLogin();
}

if (window.pywebview) {
  init();
} else {
  window.addEventListener("pywebviewready", init);
}
