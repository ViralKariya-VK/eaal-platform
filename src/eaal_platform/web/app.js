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

function setScreen(html, activeNav, options) {
  if (currentEditor) {
    currentEditor.dispose();
    currentEditor = null;
  }
  clearTimeout(debounceTimer);
  clearInterval(workspaceTimerId);
  workspaceTimerId = null;
  document.getElementById("headerExtra").innerHTML = "";
  app.innerHTML = html;
  if (!options || !options.keepSidebar) {
    renderSidebar();
  }
  setActiveNav(activeNav);
}

// -- Auth: login / create account -----------------------------------------

let loginRole = "student";

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

function showLogin() {
  document.getElementById("root").classList.add("auth-mode");
  document.getElementById("authScreen").innerHTML = `
    ${authHero()}
    <div class="auth-panel">
      <div class="auth-card">
        <div class="icon-tile">${icon("sparkle")}</div>
        <h2>Welcome back</h2>
        <p class="subtitle">Sign in to pick up your session where you left it.</p>
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
      </div>
    </div>
  `;

  document.querySelectorAll(".role-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      loginRole = btn.dataset.role;
      showLogin();
    });
  });

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
          enterApp(result.role, result.name);
        } else {
          errorEl.textContent = result.error || "Login failed.";
        }
      });
  };
  document.getElementById("loginButton").addEventListener("click", doLogin);
  document.getElementById("loginPassword").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });
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
        <div class="auth-form">
          <label>Full Name <input type="text" id="createName" placeholder="Your name" /></label>
          <label>Email <input type="text" id="createEmail" placeholder="you@school.edu" /></label>
          <label>Password <input type="password" id="createPassword" placeholder="At least 8 characters" /></label>
          ${
            loginRole === "student"
              ? `<label>Enrolment No. <span class="muted">(optional)</span> <input type="text" id="createEnrollment" placeholder="e.g. BSC2026007" /></label>`
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
    api()
      .create_account(loginRole, name, email, password, enrollmentNo)
      .then((result) => {
        if (!result.ok) {
          errorEl.textContent = result.error || "Could not create account.";
          return;
        }
        api()
          .login(loginRole, email, password)
          .then((loginResult) => enterApp(loginResult.role, loginResult.name));
      });
  });
}

// -- Sidebar -------------------------------------------------------

let currentRole = null; // "student" | "professor" — set by enterApp() after login
let currentUserName = null;

const STUDENT_NAV_ITEMS = [
  { key: "home", label: "Home", icon: "home", action: showLabs },
  { key: "practice", label: "Practice", icon: "practice", action: startPractice },
  { key: "resources", label: "Resources", icon: "resources", action: showResources },
  { key: "profile", label: "Profile", icon: "profile", action: showProfile },
];

const PROFESSOR_NAV_ITEMS = [
  { key: "home", label: "Home", icon: "home", action: showProfessorHome },
  { key: "my-labs", label: "My Labs", icon: "layers", action: showMyLabs },
  { key: "students", label: "Students", icon: "people", action: showStudentsPlaceholder },
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

function renderWorkspaceSidebar(info, stages) {
  const backAction = info.is_stage ? () => showStages(info.task_id) : showLabs;
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
      <ul class="resource-list">
        <li>${icon("file")}Task Description</li>
        <li>${icon("file")}Reference Notes</li>
      </ul>
    </div>
    <div class="sidebar-footer">Learn. Think.<br />Collaborate. Grow.</div>
  `;

  document.getElementById("sidebarBackButton").addEventListener("click", backAction);
  if (stages) {
    document
      .querySelectorAll(".task-nav-item[data-unlocked='true']:not([disabled])")
      .forEach((btn) => {
        btn.addEventListener("click", () => startStage(Number(btn.dataset.stageId)));
      });
  }
}

// -- Header: session timer / end session -----------------------------------

function formatClock(totalSeconds) {
  const clamped = Math.max(0, totalSeconds);
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function renderWorkspaceHeaderExtra(durationMinutes, onEndSession) {
  const headerExtra = document.getElementById("headerExtra");
  if (!durationMinutes) {
    headerExtra.innerHTML = `<button class="danger" id="endSessionButton">End Session</button>`;
    document.getElementById("endSessionButton").addEventListener("click", onEndSession);
    return;
  }

  let remaining = durationMinutes * 60;
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

function showResources() {
  setScreen(
    `
    <div class="page-heading"><div><h1>Resources</h1><p class="subtitle">Reference material for the labs you're working on.</p></div></div>
    <div class="card">
      <p class="muted">Nothing has been attached to your labs yet — professors add reference material (datasets, docs, cheat sheets) when they create a session.</p>
    </div>`,
    "resources"
  );
}

function showProfile() {
  const roleLabel = currentRole === "professor" ? "Professor" : "Student";
  setScreen(
    `
    <div class="page-heading"><div><h1>Profile</h1><p class="subtitle">Your CAVY account.</p></div></div>
    <div class="card" style="max-width:420px;">
      <p><b>${escapeHtml(currentUserName || roleLabel)}</b></p>
      <p class="muted">${roleLabel} account &middot; multi-device sync is coming in a later phase.</p>
      <button class="danger" id="logoutButton" style="margin-top:16px;">Log Out</button>
    </div>`,
    "profile"
  );
  document.getElementById("logoutButton").addEventListener("click", () => {
    api()
      .logout()
      .then(() => showLogin());
  });
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

function labsTableRows(labs) {
  const rows = labs
    .map(
      (lab) => `
    <tr>
      <td>${escapeHtml(lab.title)}</td>
      <td>${escapeHtml(lab.course || "—")}</td>
      <td>${escapeHtml(lab.division || "—")}</td>
      <td>${escapeHtml(lab.batch || "—")}</td>
      <td><button class="view-report-button" data-task-id="${lab.id}">${icon("file")}View Report</button></td>
    </tr>`
    )
    .join("");
  return (
    rows ||
    `<tr><td colspan="5" class="muted" style="padding:16px;">No labs yet — create one to get started.</td></tr>`
  );
}

function renderLabsScreen(navKey, heading, subtitle, eyebrow, showCreateButton) {
  api()
    .get_professor_labs()
    .then((allLabs) => {
      const render = (labs) => {
        setScreen(
          `
          <div class="page-heading">
            <div>${eyebrow ? `<div class="eyebrow">${escapeHtml(eyebrow)}</div>` : ""}<h1>${escapeHtml(heading)}</h1><p class="subtitle">${escapeHtml(subtitle)}</p></div>
          </div>
          ${renderFilterBar(allLabs, "labs")}
          <div class="card" style="padding:0; overflow:hidden;">
            <table class="data-table">
              <thead>
                <tr><th>Lab</th><th>Course</th><th>Division</th><th>Batch</th><th>Action</th></tr>
              </thead>
              <tbody>${labsTableRows(labs)}</tbody>
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
            .addEventListener("click", showCreateSession);
        }
        document.querySelectorAll(".view-report-button").forEach((btn) => {
          btn.addEventListener("click", () => showLabReport(Number(btn.dataset.taskId)));
        });
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

function showStudentsPlaceholder() {
  setScreen(
    `
    <div class="page-heading"><div><h1>Students</h1><p class="subtitle">Your class roster.</p></div></div>
    <div class="card">
      <p class="muted">A student roster spanning every device needs the multi-user sync phase, which isn't built yet — right now each device only has whichever students actually attempted a lab on it (see each lab's Report).</p>
    </div>`,
    "students"
  );
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

function showCreateSession() {
  const stageCards = STAGE_CONFIG.map(
    (stage) => `
      <div class="card stage-config-card">
        <h3>${stage.label}</h3>
        <label>
          Duration (minutes)
          <input type="number" min="1" class="stage-duration" data-stage="${stage.type}" value="${stage.defaultMinutes}" />
        </label>
        <label>
          AI Mode
          <select class="stage-mode" data-stage="${stage.type}">
            ${AI_MODE_OPTIONS.map(
              (opt) =>
                `<option value="${opt.value}" ${stage.type === "ASSESSMENT" && opt.value === "RESTRICTED" ? "selected" : ""}>${opt.label}</option>`
            ).join("")}
          </select>
        </label>
      </div>`
  ).join("");

  setScreen(
    `
    <button class="back-link" id="backButton">${icon("back")}Back to Dashboard</button>
    <div class="page-heading"><div><h1>Create New Session</h1><p class="subtitle">Set up a new lab with learning, exploration, and assessment stages.</p></div></div>
    <div class="card">
      <h3 style="margin-bottom:14px;">Session Details</h3>
      <div class="form-grid">
        <label class="span-2">Session Title <span class="required">*</span>
          <input type="text" id="sessionTitle" placeholder="Enter session title" />
        </label>
        <label>Course
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
        </label>
        <label>Difficulty
          <select id="sessionDifficulty">
            <option value="Easy">Easy</option>
            <option value="Medium" selected>Medium</option>
            <option value="Hard">Hard</option>
          </select>
        </label>
        <label class="span-2">Description
          <textarea id="sessionDescription" rows="3" placeholder="Enter a brief description about this session..."></textarea>
        </label>
      </div>
    </div>
    <div class="card" style="margin-top:16px;">
      <h3 style="margin-bottom:4px;">Learning Workflow</h3>
      <p class="muted" style="margin-bottom:14px;">Configure the duration and AI mode for each stage of the session.</p>
      <div class="stage-config-grid">${stageCards}</div>
    </div>
    <div class="button-row" style="margin-top:16px;">
      <button class="primary" id="createLabButton">Create Lab ${icon("arrowRight")}</button>
      <span class="muted" id="createLabError"></span>
    </div>
  `,
    "professor"
  );

  document.getElementById("backButton").addEventListener("click", showMyLabs);
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

    const payload = {
      title,
      course: document.getElementById("sessionCourse").value.trim() || null,
      division: document.getElementById("sessionDivision").value.trim() || null,
      batch: document.getElementById("sessionBatch").value.trim() || null,
      topic: document.getElementById("sessionTopic").value.trim() || null,
      description: document.getElementById("sessionDescription").value.trim() || null,
      difficulty: document.getElementById("sessionDifficulty").value,
      stages,
    };

    api()
      .create_lab(payload)
      .then(() => showMyLabs());
  });
}

// -- Professor: lab report -------------------------------------------------------

function exportReportCsv(report) {
  const header = ["Student Name", "Enrolment No.", "Score", "Submitted On", "Status"];
  const lines = [header, ...report.rows.map((row) => [
    row.student_name,
    row.enrollment_no || "",
    row.score === null ? "" : row.score,
    row.submitted_at || "",
    row.status,
  ])].map((cols) => cols.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(","));
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${report.task_title.replace(/[^\w.-]+/g, "_")}_report.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function showAddFollowupModal(sourceTaskId, onDone) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>Add Follow-Up Assessment</h2>
      <p class="muted">Spin off a Transfer Task or Retention Check linked to this Lab.</p>
      <label>Kind
        <select id="followupKind">
          <option value="TRANSFER">Transfer Task (a different problem, same concept)</option>
          <option value="RETENTION">Retention Check (same concept, attempted later)</option>
        </select>
      </label>
      <label>Title
        <input type="text" id="followupTitle" placeholder="e.g. Recursion Basics — Transfer Task" />
      </label>
      <label>Problem Description
        <textarea id="followupDescription" rows="3" placeholder="Describe the task the student will attempt..."></textarea>
      </label>
      <label>AI Assistance
        <select id="followupAiMode">
          <option value="RESTRICTED">Restricted</option>
          <option value="NONE">None</option>
          <option value="FULL">Full</option>
        </select>
      </label>
      <label>Duration (minutes)
        <input type="number" id="followupDuration" value="20" min="1" />
      </label>
      <div class="modal-actions">
        <button class="ghost" id="followupCancel">Cancel</button>
        <button class="primary" id="followupCreate">Create</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  document.getElementById("followupCancel").addEventListener("click", () => overlay.remove());
  document.getElementById("followupCreate").addEventListener("click", () => {
    const title = document.getElementById("followupTitle").value.trim();
    if (!title) return;
    api()
      .create_followup_assessment({
        source_task_id: sourceTaskId,
        kind: document.getElementById("followupKind").value,
        title,
        description: document.getElementById("followupDescription").value.trim(),
        ai_assistance_mode: document.getElementById("followupAiMode").value,
        duration_minutes: Number(document.getElementById("followupDuration").value) || null,
      })
      .then(() => {
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

function showLabReport(taskId) {
  Promise.all([
    api().get_lab_report(taskId),
    api().get_professor_labs(),
    api().get_followup_assessments(taskId),
  ]).then(([report, allLabs, followups]) => {
    const rows = report.rows
      .map(
        (row) => `
        <tr>
          <td>${escapeHtml(row.student_name)}</td>
          <td>${escapeHtml(row.enrollment_no || "—")}</td>
          <td>${row.score === null ? "—" : row.score}</td>
          <td>${row.submitted_at ? escapeHtml(new Date(row.submitted_at).toLocaleString()) : "—"}</td>
          <td><span class="pill ${row.status === "Submitted" ? "pill-ready" : "pill-danger"}">${row.status}</span></td>
        </tr>`
      )
      .join("");

    const meta = [report.course, `Batch: ${report.batch || "—"}`, `Division: ${report.division || "—"}`]
      .filter(Boolean)
      .join(" | ");

    setScreen(
      `
        <button class="back-link" id="backButton">${icon("back")}Back to Dashboard</button>
        <div class="page-heading">
          <div><h1>${escapeHtml(report.task_title)}</h1><p class="subtitle">${escapeHtml(meta)}</p></div>
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
        ${followupAssessmentSummary(followups)}
      `,
      "reports"
    );

    document.getElementById("backButton").addEventListener("click", showMyLabs);
    document.getElementById("exportReportButton").addEventListener("click", () => {
      exportReportCsv(report);
    });
    wireFilterBar(allLabs, "report", (filters) => {
      const matches = filterLabs(allLabs, filters);
      if (matches.length > 0) showLabReport(matches[0].id);
    });
    document.getElementById("addFollowupButton").addEventListener("click", () => {
      showAddFollowupModal(taskId, () => showLabReport(taskId));
    });
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
          <span class="pill pill-ready">Ready to start</span>
          <button class="primary lab-start-button">Start ${icon("arrowRight")}</button>
        </div>`
        )
        .join("");

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

function showStages(taskId) {
  Promise.all([api().get_stages(taskId), api().get_followup_assessments(taskId)]).then(
    ([data, followups]) => {
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
              <span class="pill ${stage.unlocked ? "pill-ready" : "pill-locked"}">${stage.unlocked ? "Ready to Start" : "Locked"}</span>
            </div>
          </div>
          <button class="primary stage-start-button" ${stage.unlocked ? "" : "disabled"}>
            ${stage.unlocked ? "Start" : "Locked"} ${icon("arrowRight")}
          </button>
        </div>`
        )
        .join("");

      setScreen(
        `
        <button class="back-link" id="backButton">${icon("back")}Back to Dashboard</button>
        <div class="page-heading">
          <div><h1>${escapeHtml(data.task_title)}</h1></div>
        </div>
        <div class="stepper">${steps}</div>
        <div class="stage-list">${cards}</div>
        ${followupAssessmentCards(followups)}
      `,
        "home"
      );

      document.getElementById("backButton").addEventListener("click", showLabs);
      app.querySelectorAll(".stage-card").forEach((card) => {
        if (card.dataset.unlocked !== "true") return;
        card.querySelector(".stage-start-button").addEventListener("click", () => {
          startStage(Number(card.dataset.stageId));
        });
      });
      app.querySelectorAll(".followup-start-button").forEach((button) => {
        const card = button.closest(".stage-card");
        button.addEventListener("click", () => startStage(Number(card.dataset.stageId)));
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
  if (info.duration_minutes) {
    pills.push(`<span class="pill pill-neutral">${icon("clock")}Est. Time: ${info.duration_minutes}m</span>`);
  }

  const actionButtons = info.is_stage
    ? `<button class="ghost-icon" id="saveButton">${icon("save")}Save</button><button class="accent" id="submitButton">Submit ${icon("arrowRight")}</button>`
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

  renderWorkspaceHeaderExtra(info.duration_minutes, () => goBackFromWorkspace(info));

  if (info.is_stage) {
    api()
      .get_stages(info.task_id)
      .then((data) => renderWorkspaceSidebar(info, data.stages));
  } else {
    renderWorkspaceSidebar(info, null);
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
    document.getElementById("submitButton").addEventListener("click", () => {
      const doSubmit = () =>
        api()
          .submit_session(info.session_id, currentFiles())
          .then(() => showSubmission(info.session_id));
      if (info.stage_type === "ASSESSMENT") {
        showConceptCheckModal(info.session_id, doSubmit);
      } else {
        doSubmit();
      }
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

  api()
    .ping_ai()
    .then((available) => {
      document.getElementById("chatStatus").textContent = available
        ? "Assistant ready"
        : "Assistant not available";
    });

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
          transcript.innerHTML += `<div class="msg assistant"><i>The assistant didn't respond — it may not be running.</i></div>`;
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
          <h1>Assessment Submitted Successfully</h1>
          <p class="muted">Your submission has been successfully submitted for evaluation.</p>
          <div class="submission-details">
            <div class="card">
              <span class="meta-icon">${icon("clock")}</span>
              <p class="muted">Submission Time</p>
              <p><b>${escapeHtml(summary.submitted_at || "-")}</b></p>
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

// -- Bootstrap -------------------------------------------------------

function enterApp(role, name) {
  currentRole = role;
  currentUserName = name;
  document.getElementById("root").classList.remove("auth-mode");
  document.getElementById("studentAvatar").textContent = (name || "?").charAt(0).toUpperCase();
  document.getElementById("studentAvatar").title = name || "";
  renderSidebar();
  if (role === "professor") {
    showMyLabs();
  } else {
    showLabs();
  }
}

function init() {
  api()
    .login("student", "student@cavy.local", "student123")
    .then((res) => {
      enterApp(res.role, res.name);
      return api().get_labs();
    })
    .then((labs) => api().get_stages(labs[0].id))
    .then((data) => api().start_stage(data.stages[2].id))
    .then((info) => showWorkspace(info))
    .catch((err) => {
      document.body.innerHTML = `<pre style="color:red;font-size:20px;padding:40px;">${err}\n${err.stack || ""}</pre>`;
    });
}

if (window.pywebview) {
  init();
} else {
  window.addEventListener("pywebviewready", init);
}
