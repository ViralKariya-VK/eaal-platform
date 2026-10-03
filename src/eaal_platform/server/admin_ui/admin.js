// CAVY admin panel. Plain JavaScript, no build step.
"use strict";

const app = document.getElementById("app");
const TOKEN_KEY = "cavy_admin_token";
let adminName = "";

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3500);
}

// -- API ----------------------------------------------------------------------

function token() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
function setToken(value) {
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the panel just asks you to sign in again on reload */
  }
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(`/admin/api${path}`, { ...options, headers });
  } catch {
    throw new Error("Can't reach the server.");
  }
  if (response.status === 401 && token()) {
    setToken(null);
    showAuth();
    throw new Error("Please sign in again.");
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return options.raw ? response : response.json();
}
const get = (path) => request(path);
const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body ?? {}) });

async function download(path, fallbackName) {
  try {
    const response = await request(path, { raw: true });
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = /filename="?([^";]+)"?/.exec(disposition);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = match ? match[1] : fallbackName;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (err) {
    toast(err.message);
  }
}

// -- helpers ------------------------------------------------------------------

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return isNaN(d) ? iso : d.toLocaleString();
}

function modal(html, onMount) {
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.innerHTML = `<div class="modal">${html}</div>`;
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
  if (onMount) onMount(overlay);
  return overlay;
}

function table(headers, rows, { rowClass = "", empty = "Nothing here yet." } = {}) {
  if (!rows.length) return `<p class="muted">${esc(empty)}</p>`;
  return `<div class="table-wrap"><table>
    <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr class="${rowClass}">${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div>`;
}

// -- sign in ------------------------------------------------------------------

async function showAuth() {
  let needsSetup = false;
  try {
    needsSetup = (await get("/status")).needs_setup;
  } catch (err) {
    app.innerHTML = `<div class="auth card"><h2>CAVY Admin</h2><p class="error">${esc(err.message)}</p></div>`;
    return;
  }
  app.innerHTML = `
    <div class="auth card">
      <h2>CAVY Admin</h2>
      <p class="muted">${needsSetup ? "First time here: create the administrator account." : "Sign in to manage this server."}</p>
      ${needsSetup ? `<label>Your name <input id="aName" autocomplete="name" /></label>` : ""}
      <label>Email <input id="aEmail" type="text" autocomplete="username" /></label>
      <label>Password <input id="aPassword" type="password" autocomplete="${needsSetup ? "new-password" : "current-password"}" /></label>
      ${needsSetup ? `<p class="muted">At least 8 characters. Keep it safe: this account controls every user and all data.</p>` : ""}
      <p class="error" id="aError"></p>
      <button class="primary" id="aGo">${needsSetup ? "Create admin account" : "Sign in"}</button>
    </div>`;
  const go = async () => {
    const body = {
      email: document.getElementById("aEmail").value.trim(),
      password: document.getElementById("aPassword").value,
    };
    if (needsSetup) body.name = document.getElementById("aName").value.trim();
    try {
      const result = await post(needsSetup ? "/setup" : "/login", body);
      setToken(result.token);
      adminName = result.name;
      showShell("overview");
    } catch (err) {
      document.getElementById("aError").textContent = err.message;
    }
  };
  document.getElementById("aGo").addEventListener("click", go);
  document.getElementById("aPassword").addEventListener("keydown", (e) => {
    if (e.key === "Enter") go();
  });
}

// -- shell --------------------------------------------------------------------

const SECTIONS = [
  ["overview", "Overview", showOverview],
  ["approved", "Approved emails", showApproved],
  ["users", "Users", showUsers],
  ["labs", "Labs", showLabs],
  ["resources", "Resources", showResources],
  ["database", "Database", showDatabase],
  ["audit", "Audit log", showAudit],
  ["ai", "AI assistant", showAi],
  ["backup", "Backup", showBackup],
];

function showShell(active) {
  app.innerHTML = `
    <div class="shell">
      <nav class="side">
        <div class="brand">CAVY<small>Admin panel</small></div>
        ${SECTIONS.map(([key, label]) => `<button data-nav="${key}" class="${key === active ? "active" : ""}">${label}</button>`).join("")}
        <div class="spacer"></div>
        <div class="who">${esc(adminName)}</div>
        <button id="signOut">Sign out</button>
      </nav>
      <main id="main"></main>
    </div>`;
  document.querySelectorAll("[data-nav]").forEach((button) =>
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-nav]").forEach((b) => b.classList.toggle("active", b === button));
      SECTIONS.find(([key]) => key === button.dataset.nav)[2]();
    })
  );
  document.getElementById("signOut").addEventListener("click", async () => {
    try {
      await post("/logout");
    } catch {
      /* already signed out */
    }
    setToken(null);
    showAuth();
  });
  SECTIONS.find(([key]) => key === active)[2]();
}

const main = () => document.getElementById("main");

async function guarded(render) {
  try {
    await render();
  } catch (err) {
    main().innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

// -- auto refresh ---------------------------------------------------------------

let refreshTimer = null;
function autoRefresh(section, render, seconds = 4) {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    const active = document.querySelector("[data-nav].active");
    if (!active || active.dataset.nav !== section) return clearInterval(refreshTimer);
    if (document.querySelector(".overlay") || document.hidden) return; // don't disturb dialogs
    const el = document.activeElement;
    if (el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return;
    const scroll = main().scrollTop;
    render(true).then(() => (main().scrollTop = scroll));
  }, seconds * 1000);
}

function ago(seconds) {
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

// -- overview (live) --------------------------------------------------------------

function showOverview(quiet) {
  return guarded(async () => {
    const [data, live] = await Promise.all([get("/overview"), get("/live")]);
    const c = data.counts;
    const a = live.accounts;
    const tile = (label, value, hint = "") =>
      `<div class="card tile"><div class="num">${value}</div><div class="muted">${label}</div>${hint ? `<div class="muted" style="font-size:11px">${hint}</div>` : ""}</div>`;
    const ai = data.ai;
    main().innerHTML = `
      <div class="page-head"><div><h1>Overview</h1><p class="muted">Live: updates itself every few seconds.</p></div>
        <button id="refresh">Refresh now</button></div>
      <div class="tiles">
        ${tile("Accounts", a.students + a.professors, `${a.students} students · ${a.professors} professors`)}
        ${tile("Signed in now", a.signed_in)}
        ${tile("Working on a task now", live.working_now.length)}
        ${tile("Disabled accounts", a.disabled)}
        ${tile("Labs", c.labs)}${tile("Sessions", c.sessions, `${c.submitted} submitted`)}${tile("Events logged", c.events)}${tile("AI chats", c.ai_interactions)}
      </div>
      <div class="card"><div class="page-head" style="margin:0 0 8px"><h3>Signed in now</h3>
        ${live.signed_in.length ? '<button class="danger" id="signOutAll">Sign everyone out</button>' : ""}</div>
        ${table(
          ["Name", "Role", "From", "Signed in", "Last active", ""],
          live.signed_in.map((u) => [
            esc(u.name),
            esc(u.role),
            esc(u.address),
            esc(formatTime(u.signed_in_at)),
            esc(ago(u.idle_seconds)),
            `<button class="danger" data-kick="${u.client_id}" data-name="${esc(u.name)}">Sign out</button>`,
          ]),
          { empty: "Nobody is signed in." }
        )}
      </div>
      <div class="card"><h3>Working on a task right now</h3>
        ${table(
          ["Student", "Task", "Stage", "Started", "Last activity", "Events", "AI chats", ""],
          live.working_now.map((w) => [
            esc(w.student),
            esc(w.task),
            esc(w.stage || "Practice"),
            esc(formatTime(w.started_at)),
            esc(ago(w.idle_seconds)),
            w.events,
            w.ai_chats,
            `<button class="link" data-session="${w.session_id}">Open</button>`,
          ]),
          { empty: "Nobody is mid-task." }
        )}
      </div>
      <div class="cols2">
        <div class="card"><h3>Latest activity</h3>
          ${table(
            ["When", "Student", "What", ""],
            live.feed.map((f) => [esc(formatTime(f.timestamp)), esc(f.student), esc(f.type.replaceAll("_", " ").toLowerCase()), `<button class="link" data-session="${f.session_id}">Open</button>`]),
            { empty: "No activity yet." }
          )}
        </div>
        <div class="card"><h3>AI assistant</h3>
          <p>${ai.available ? '<span class="pill ok">Ready</span>' : '<span class="pill bad">Not available</span>'}
          ${esc(ai.provider || "none")} ${ai.model ? "· " + esc(ai.model) : ""}</p>
          ${ai.problem ? `<p class="muted">${esc(ai.problem)}</p>` : ""}
          <h3 style="margin-top:18px">Latest sessions</h3>
          ${table(
            ["#", "Student", "Task", "Status", ""],
            data.recent_sessions.slice(0, 6).map((s) => [
              s.session_id,
              esc(s.student),
              esc(s.task),
              s.submitted ? '<span class="pill ok">Submitted</span>' : '<span class="pill warn">In progress</span>',
              `<button class="link" data-session="${s.session_id}">Open</button>`,
            ]),
            { empty: "No sessions yet." }
          )}
        </div>
      </div>`;
    document.getElementById("refresh").addEventListener("click", () => showOverview());
    main().querySelectorAll("[data-session]").forEach((b) =>
      b.addEventListener("click", () => showSession(Number(b.dataset.session)))
    );
    main().querySelectorAll("[data-kick]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await post(`/signed-in/${b.dataset.kick}/sign-out`);
          toast(`${b.dataset.name} was signed out.`);
          showOverview();
        } catch (err) {
          toast(err.message);
          showOverview();
        }
      })
    );
    const all = document.getElementById("signOutAll");
    if (all)
      all.addEventListener("click", () =>
        confirmBox("Sign everyone out?", "Everyone signed in (students and professors) is returned to the login screen. Unsaved work may be lost.", "Sign everyone out", async () => {
          const r = await post("/signed-in/sign-out-all");
          toast(`${r.signed_out} signed out.`);
          showOverview();
        })
      );
    if (!quiet) autoRefresh("overview", showOverview);
  });
}

function confirmBox(title, message, label, onYes) {
  modal(
    `<h3>${esc(title)}</h3><p class="muted">${esc(message)}</p>
     <div class="actions"><button id="no">Cancel</button><button class="danger" id="yes">${esc(label)}</button></div>`,
    (overlay) => {
      overlay.querySelector("#no").addEventListener("click", () => overlay.remove());
      overlay.querySelector("#yes").addEventListener("click", async () => {
        overlay.remove();
        try {
          await onYes();
        } catch (err) {
          toast(err.message);
        }
      });
    }
  );
}

// -- approved emails (who may create an account) ----------------------------------

let approvedRole = "professor"; // "professor" | "student"
const ROLE_NAME = { professor: "Teachers", student: "Students" };

function showApproved(quiet) {
  return guarded(async () => {
    const data = await get(`/allowed/${approvedRole}`);
    const isStudent = approvedRole === "student";
    const c = data.counts;
    main().innerHTML = `
      <div class="page-head"><div><h1>Approved emails</h1>
        <p class="muted">Only people whose email is on these lists can create an account, so CAVY stays limited to your institution. ${isStudent ? "Students" : "Teachers"} not on the list are refused at sign-up.</p></div></div>
      <div class="toolbar">
        <button class="${approvedRole === "professor" ? "primary" : ""}" data-role="professor">Teachers</button>
        <button class="${approvedRole === "student" ? "primary" : ""}" data-role="student">Students</button>
        <span class="muted" style="margin-left:10px">${c.total} approved · ${c.registered} registered · ${c.waiting} waiting</span>
        <span style="flex:1"></span>
        <button id="dlTemplate">Download template</button>
        <button id="importBtn">Import CSV / Excel</button>
        <input type="file" id="importFile" accept=".csv,.xlsx,.xlsm,.txt,.tsv" hidden />
        <button class="primary" id="addOne">Add one</button>
      </div>
      <div class="card">${table(
        isStudent ? ["Name", "Email", "Enrolment", "Status", ""] : ["Name", "Email", "Status", ""],
        data.entries.map((e) => {
          const status = e.registered ? '<span class="pill ok">Registered</span>' : '<span class="pill warn">Waiting to sign up</span>';
          const actions = `<div class="row-actions">${e.registered ? "" : `<button data-edit="${e.id}">Edit</button>`}<button class="danger" data-remove="${e.id}">Remove</button></div>`;
          return isStudent
            ? [esc(e.name || ""), esc(e.email), esc(e.enrollment_no || ""), status, actions]
            : [esc(e.name || ""), esc(e.email), status, actions];
        }),
        { empty: `No ${isStudent ? "student" : "teacher"} emails yet. Use “Add one” or import a file.` }
      )}</div>
      <p class="muted">A file needs a header row: <code>${isStudent ? "name, email, enrollment_no" : "name, email"}</code>. Extra columns are ignored and the order doesn't matter. Use <b>Download template</b> for an example. Edit a person who has already registered on the Users page.</p>`;

    const byId = Object.fromEntries(data.entries.map((e) => [e.id, e]));
    main().querySelectorAll("[data-role]").forEach((b) =>
      b.addEventListener("click", () => { approvedRole = b.dataset.role; showApproved(); })
    );
    document.getElementById("addOne").addEventListener("click", () => approvedForm(null));
    document.getElementById("dlTemplate").addEventListener("click", () =>
      download(`/allowed-template/${approvedRole}.csv`, `${approvedRole}-template.csv`)
    );
    const fileInput = document.getElementById("importFile");
    document.getElementById("importBtn").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async () => {
        const text = String(reader.result);
        try {
          const report = await post("/allowed-import", {
            role: approvedRole,
            filename: file.name,
            data_base64: text.slice(text.indexOf(",") + 1),
          });
          showImportReport(file.name, report);
        } catch (err) {
          toast(err.message);
        }
        fileInput.value = "";
      };
      reader.readAsDataURL(file);
    });
    main().querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => approvedForm(byId[b.dataset.edit]))
    );
    main().querySelectorAll("[data-remove]").forEach((b) =>
      b.addEventListener("click", () => {
        const e = byId[b.dataset.remove];
        confirmBox(
          `Remove ${e.email}?`,
          e.registered
            ? "They have already registered, and their account stays as it is. If you delete their account later they will not be able to sign up again unless you add them back."
            : "They will no longer be able to create an account with this email.",
          "Remove",
          async () => { await post(`/allowed/${e.id}/delete`); toast("Removed."); showApproved(); }
        );
      })
    );
  });
}

function approvedForm(existing) {
  const isStudent = approvedRole === "student";
  modal(
    `<h3>${existing ? "Edit" : "Add"} ${isStudent ? "student" : "teacher"} email</h3>
     <label>Name (optional) <input id="aeName" value="${esc(existing?.name || "")}" /></label>
     <label>Email <input id="aeEmail" value="${esc(existing?.email || "")}" placeholder="name@university.edu" /></label>
     ${isStudent ? `<label>Enrolment number (optional) <input id="aeEnrol" value="${esc(existing?.enrollment_no || "")}" /></label>` : ""}
     <p class="error" id="aeError"></p>
     <div class="actions"><button id="aeCancel">Cancel</button><button class="primary" id="aeSave">${existing ? "Save" : "Add"}</button></div>`,
    (o) => {
      o.querySelector("#aeCancel").addEventListener("click", () => o.remove());
      const save = async () => {
        const body = {
          name: o.querySelector("#aeName").value.trim(),
          email: o.querySelector("#aeEmail").value.trim(),
          enrollment_no: isStudent ? o.querySelector("#aeEnrol").value.trim() : null,
        };
        try {
          if (existing) await post(`/allowed/${existing.id}/update`, body);
          else await post("/allowed", { role: approvedRole, ...body });
          o.remove();
          toast(existing ? "Saved." : "Added.");
          showApproved();
        } catch (err) {
          o.querySelector("#aeError").textContent = err.message;
        }
      };
      o.querySelector("#aeSave").addEventListener("click", save);
      o.querySelector("#aeEmail").addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    }
  );
}

function showImportReport(filename, report) {
  const problems = report.problems.length
    ? `<h3 style="margin-top:14px">Rows that were not added (${report.problems.length})</h3>
       ${table(["Row", "Email", "Why"], report.problems.map((p) => [p.row ?? "", esc(p.email), esc(p.error)]))}`
    : "";
  modal(
    `<h3>Import finished</h3>
     <p class="muted">${esc(filename)} · ${report.rows} row${report.rows === 1 ? "" : "s"} read</p>
     <div class="tiles" style="margin:10px 0">
       <div class="card tile"><div class="num">${report.added}</div><div class="muted">added</div></div>
       <div class="card tile"><div class="num">${report.already_listed}</div><div class="muted">already on the list</div></div>
       <div class="card tile"><div class="num">${report.problems.length}</div><div class="muted">not added</div></div>
     </div>${problems}
     <div class="actions"><button class="primary" id="irDone">Done</button></div>`,
    (o) => o.querySelector("#irDone").addEventListener("click", () => { o.remove(); showApproved(); })
  );
}

// -- users --------------------------------------------------------------------

function showUsers(quiet) {
  return guarded(async () => {
    const data = await get("/users");
    const status = (u) =>
      (u.online ? '<span class="pill ok">online</span> ' : "") +
      (u.disabled ? '<span class="pill bad">disabled</span> ' : "") +
      (u.must_change_password ? '<span class="pill warn">must change password</span>' : "");
    const teacherSelect = (u) =>
      `<select data-assign="${u.id}" style="min-width:130px;padding:4px 6px">
         <option value="">No class</option>
         ${data.professors.map((p) => `<option value="${p.id}" ${p.id === u.professor_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}
       </select>`;
    const row = (role) => (u) => [
      u.id,
      esc(u.name),
      esc(u.email),
      role === "student" ? esc(u.enrollment_no || "") : `${u.students} students`,
      role === "student" ? teacherSelect(u) : `${u.resources} resources`,
      role === "student" ? u.sessions : "",
      status(u),
      `<div class="row-actions"><button data-edit="${role}:${u.id}">Edit</button> <button data-reset="${role}:${u.id}" data-name="${esc(u.name)}">Reset password</button>
       <button data-toggle="${role}:${u.id}" data-disabled="${u.disabled}" data-name="${esc(u.name)}">${u.disabled ? "Enable" : "Disable"}</button>
       <button class="danger" data-delete="${role}:${u.id}" data-name="${esc(u.name)}">Delete</button></div>`,
    ];
    const byKey = {};
    data.students.forEach((u) => (byKey[`student:${u.id}`] = u));
    data.professors.forEach((u) => (byKey[`professor:${u.id}`] = u));
    main().innerHTML = `
      <div class="page-head"><div><h1>Users</h1>
      <p class="muted">${data.students.length} students · ${data.professors.length} professors. Changing an email or password signs that person out. Disabling keeps their history but blocks sign-in. A student is in one teacher's class; only that teacher (or you) can share resources with them or manage them. Accounts you add here don't need an approved email; people signing up themselves do (see Approved emails).</p></div>
      <button class="primary" id="newUser">Add account</button></div>
      <div class="card"><h3>Professors (${data.professors.length})</h3>
        ${table(["ID", "Name", "Email", "Class", "Shared", "", "Status", ""], data.professors.map(row("professor")), { empty: "No professors yet." })}</div>
      <div class="card"><h3>Students (${data.students.length})</h3>
        ${table(["ID", "Name", "Email", "Enrolment", "Teacher", "Sessions", "Status", ""], data.students.map(row("student")), { empty: "No students yet." })}</div>`;

    document.getElementById("newUser").addEventListener("click", () => userForm(null));
    main().querySelectorAll("[data-assign]").forEach((select) =>
      select.addEventListener("change", async () => {
        try {
          await post("/users/assign", {
            student_id: Number(select.dataset.assign),
            professor_id: select.value ? Number(select.value) : null,
          });
          toast(select.value ? "Student assigned." : "Student removed from their class.");
          showUsers();
        } catch (err) {
          toast(err.message);
          showUsers();
        }
      })
    );
    main().querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => userForm({ role: b.dataset.edit.split(":")[0], ...byKey[b.dataset.edit] }))
    );
    main().querySelectorAll("[data-toggle]").forEach((b) =>
      b.addEventListener("click", () => {
        const [role, id] = b.dataset.toggle.split(":");
        const disabling = b.dataset.disabled !== "true";
        const go = async () => {
          await post("/users/disable", { role, id: Number(id), disabled: disabling });
          toast(`${b.dataset.name} ${disabling ? "disabled" : "enabled"}.`);
          showUsers();
        };
        if (disabling) confirmBox(`Disable ${b.dataset.name}?`, "They are signed out now and cannot sign in until you enable the account again. Their work is kept.", "Disable", go);
        else go().catch((e) => toast(e.message));
      })
    );
    main().querySelectorAll("[data-delete]").forEach((b) =>
      b.addEventListener("click", () => {
        const [role, id] = b.dataset.delete.split(":");
        confirmBox(`Delete ${b.dataset.name}?`, "This permanently removes the account. Students who have recorded work cannot be deleted; disable them instead.", "Delete account", async () => {
          await post("/users/delete", { role, id: Number(id) });
          toast("Account deleted.");
          showUsers();
        });
      })
    );
    main().querySelectorAll("[data-reset]").forEach((button) =>
      button.addEventListener("click", () => {
        const [role, id] = button.dataset.reset.split(":");
        confirmBox(`Reset password for ${button.dataset.name}?`, "Their current password stops working immediately and they are signed out.", "Reset password", async () => {
          const result = await post("/users/reset-password", { role, id: Number(id) });
          modal(
            `<h3>Temporary password</h3>
             <p class="muted">Give this to ${esc(button.dataset.name)}. It is shown only now; they must choose a new one at sign-in.</p>
             <div class="temp mono">${esc(result.temporary_password)}</div>
             <div class="actions"><button class="primary" id="done">Done</button></div>`,
            (o) => o.querySelector("#done").addEventListener("click", () => { o.remove(); showUsers(); })
          );
        });
      })
    );
    if (!quiet) autoRefresh("users", showUsers, 6);
  });
}

function userForm(existing) {
  const creating = !existing;
  modal(
    `<h3>${creating ? "Add account" : "Edit " + esc(existing.name)}</h3>
     ${creating ? `<label>Role <select id="uRole"><option value="student">Student</option><option value="professor">Professor</option></select></label>` : ""}
     <label>Name <input id="uName" value="${esc(existing?.name || "")}" /></label>
     <label>Email <input id="uEmail" value="${esc(existing?.email || "")}" /></label>
     <div id="enrolRow"><label>Enrolment number <input id="uEnrol" value="${esc(existing?.enrollment_no || "")}" /></label></div>
     <label>${creating ? "Password" : "New password (leave empty to keep the current one)"} <input id="uPass" type="text" autocomplete="off" placeholder="at least 8 characters" /></label>
     ${creating ? "" : `<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="uMust" style="width:auto" checked /> Ask them to choose their own password at next sign-in</label>`}
     <p class="error" id="uError"></p>
     <div class="actions"><button id="cancel">Cancel</button><button class="primary" id="save">${creating ? "Create" : "Save"}</button></div>`,
    (o) => {
      const role = () => (creating ? o.querySelector("#uRole").value : existing.role);
      const syncRole = () => (o.querySelector("#enrolRow").style.display = role() === "student" ? "" : "none");
      if (creating) o.querySelector("#uRole").addEventListener("change", syncRole);
      syncRole();
      o.querySelector("#cancel").addEventListener("click", () => o.remove());
      o.querySelector("#save").addEventListener("click", async () => {
        const v = (id) => o.querySelector(id).value.trim();
        try {
          if (creating) {
            await post("/users/create", { role: role(), name: v("#uName"), email: v("#uEmail"), password: o.querySelector("#uPass").value, enrollment_no: v("#uEnrol") || null });
          } else {
            const body = { role: role(), id: existing.id, name: v("#uName"), email: v("#uEmail") };
            if (role() === "student") body.enrollment_no = v("#uEnrol");
            const pass = o.querySelector("#uPass").value;
            if (pass) {
              body.new_password = pass;
              body.must_change_password = o.querySelector("#uMust").checked;
            }
            const r = await post("/users/update", body);
            if (r.signed_out) toast(`Saved. ${r.signed_out} open session(s) were signed out.`);
          }
          o.remove();
          showUsers();
        } catch (err) {
          o.querySelector("#uError").textContent = err.message;
        }
      });
    }
  );
}

// -- labs ---------------------------------------------------------------------

function showLabs(quiet) {
  return guarded(async () => {
    const labs = await get("/labs");
    main().innerHTML = `
      <div class="page-head"><div><h1>Labs</h1><p class="muted">The tests being run, and how many students are working on or have finished each. Archiving hides a lab from students; nothing is deleted.</p></div></div>
      <div class="card">${table(
        ["ID", "Title", "Professor", "Course", "Difficulty", "Students", "In progress", "Submitted", "Status", ""],
        labs.map((l) => [
          l.id,
          esc(l.title),
          esc(l.professor || ""),
          esc(l.course || ""),
          esc(l.difficulty || ""),
          l.students,
          l.in_progress ? `<span class="pill warn">${l.in_progress} working</span>` : "0",
          l.submitted,
          l.archived ? '<span class="pill warn">Archived</span>' : '<span class="pill ok">Active</span>',
          `<button data-archive="${l.id}" data-state="${l.archived}">${l.archived ? "Restore" : "Archive"}</button>`,
        ]),
        { empty: "No labs yet." }
      )}</div>`;
    main().querySelectorAll("[data-archive]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          await post(`/labs/${button.dataset.archive}/archive`, { archived: button.dataset.state !== "true" });
          showLabs();
        } catch (err) {
          toast(err.message);
        }
      })
    );
    if (!quiet) autoRefresh("labs", showLabs);
  });
}

// -- database browser ---------------------------------------------------------

const dbState = { table: null, page: 1, q: "", sort: "", desc: true, filter: null };

function showDatabase(jumpTo) {
  return guarded(async () => {
    if (jumpTo) Object.assign(dbState, { page: 1, q: "", sort: "", desc: true, ...jumpTo });
    const tables = await get("/tables");
    if (!dbState.table) dbState.table = tables[0].name;
    main().innerHTML = `
      <div class="page-head"><div><h1>Database</h1>
      <p class="muted">Read-only. Click a highlighted id to follow it to the related record.</p></div></div>
      <div class="db-grid">
        <div class="card table-list">${tables
          .map((t) => `<button data-table="${t.name}" class="${t.name === dbState.table ? "active" : ""}"><span>${t.name}</span><span class="muted">${t.rows}</span></button>`)
          .join("")}</div>
        <div id="dbView"></div>
      </div>`;
    main().querySelectorAll("[data-table]").forEach((b) =>
      b.addEventListener("click", () => {
        Object.assign(dbState, { table: b.dataset.table, page: 1, q: "", sort: "", desc: true, filter: null });
        showDatabase();
      })
    );
    await loadTable(tables);
  });
}

async function loadTable(tables) {
  const params = new URLSearchParams({ page: dbState.page, page_size: 50, q: dbState.q, desc: dbState.desc });
  if (dbState.sort) params.set("sort", dbState.sort);
  if (dbState.filter) {
    params.set("filter_column", dbState.filter.column);
    params.set("filter_value", dbState.filter.value);
  }
  const data = await get(`/tables/${dbState.table}?${params}`);
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const view = document.getElementById("dbView");
  const sessionLink = (columnName) => columnName === "session_id";
  view.innerHTML = `
    <div class="toolbar">
      <h3>${esc(dbState.table)}</h3><span class="muted">${data.total} rows</span>
      <input id="dbSearch" placeholder="Search all columns…" value="${esc(dbState.q)}" />
      ${dbState.filter ? `<span class="chip">${esc(dbState.filter.column)} = ${esc(dbState.filter.value)} <button class="link" id="clearFilter">clear</button></span>` : ""}
      <span style="flex:1"></span>
      <button id="exportCsv">Export CSV</button>
    </div>
    <div class="table-wrap"><table>
      <thead><tr>${data.columns
        .map((c) => `<th class="sortable" data-sort="${c.name}">${esc(c.name)}${data.sort === c.name ? (data.desc ? " ▼" : " ▲") : ""}</th>`)
        .join("")}</tr></thead>
      <tbody>${data.rows
        .map(
          (row, i) => `<tr class="clickable" data-row="${row[data.columns.findIndex((c) => c.name === "id")] ?? ""}">${row
            .map((value, ci) => {
              const col = data.columns[ci];
              const shown = value === null ? '<span class="muted">null</span>' : esc(value);
              if (col.references && value !== null) {
                return `<td><button class="link" data-follow="${col.references}:${esc(value)}">${shown}</button></td>`;
              }
              if (sessionLink(col.name) && value !== null) {
                return `<td><button class="link" data-session="${esc(value)}">${shown}</button></td>`;
              }
              return `<td title="${esc(value)}">${shown}</td>`;
            })
            .join("")}</tr>`
        )
        .join("")}</tbody>
    </table></div>
    <div class="toolbar" style="margin-top:10px">
      <button id="prev" ${dbState.page <= 1 ? "disabled" : ""}>Previous</button>
      <span class="muted">Page ${data.page} of ${pages}</span>
      <button id="next" ${dbState.page >= pages ? "disabled" : ""}>Next</button>
    </div>`;

  const search = document.getElementById("dbSearch");
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      dbState.q = search.value;
      dbState.page = 1;
      guarded(() => loadTable(tables));
    }, 300);
  });
  const clear = document.getElementById("clearFilter");
  if (clear) clear.addEventListener("click", () => { dbState.filter = null; dbState.page = 1; guarded(() => loadTable(tables)); });
  document.getElementById("prev").addEventListener("click", () => { dbState.page--; guarded(() => loadTable(tables)); });
  document.getElementById("next").addEventListener("click", () => { dbState.page++; guarded(() => loadTable(tables)); });
  document.getElementById("exportCsv").addEventListener("click", () => download(`/export/${dbState.table}.csv`, `${dbState.table}.csv`));
  view.querySelectorAll("th[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      dbState.desc = dbState.sort === th.dataset.sort ? !dbState.desc : false;
      dbState.sort = th.dataset.sort;
      guarded(() => loadTable(tables));
    })
  );
  view.querySelectorAll("[data-follow]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const [name, id] = b.dataset.follow.split(":");
      showDatabase({ table: name, filter: { column: "id", value: id } });
    })
  );
  view.querySelectorAll("[data-session]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      showSession(Number(b.dataset.session));
    })
  );
  view.querySelectorAll("tr[data-row]").forEach((tr) =>
    tr.addEventListener("click", async () => {
      if (!tr.dataset.row) return;
      try {
        const row = await get(`/tables/${dbState.table}/${tr.dataset.row}`);
        modal(`<h3>${esc(dbState.table)} #${esc(tr.dataset.row)}</h3>${Object.entries(row)
          .map(([k, v]) => `<p class="muted" style="margin:10px 0 0">${esc(k)}</p><pre>${esc(v === null ? "null" : v)}</pre>`)
          .join("")}<div class="actions"><button id="close">Close</button></div>`, (o) =>
          o.querySelector("#close").addEventListener("click", () => o.remove()));
      } catch (err) {
        toast(err.message);
      }
    })
  );
}

// -- one session --------------------------------------------------------------

function showSession(id) {
  return guarded(async () => {
    const d = await get(`/sessions/${id}`);
    const s = d.session;
    const signals = Object.entries(d.signals);
    main().innerHTML = `
      <div class="page-head"><div><button id="back">← Back</button></div></div>
      <div class="page-head"><div><h1>Session #${s.id}</h1>
        <p class="muted">${esc(s.student)} · ${esc(s.task)}${s.stage ? " · " + esc(s.stage) : ""} · started ${esc(formatTime(s.started_at))} ·
        ${s.submitted_at ? "submitted " + esc(formatTime(s.submitted_at)) : "not submitted"}</p></div></div>
      <div class="cols2">
        <div class="card"><h3>Signal scores (latest)</h3>
          ${signals.length ? table(["Signal", ""], signals.map(([k, v]) => [esc(k), v === null ? '<span class="muted">n/a</span>' : `<span class="bar"><span style="width:${Math.round(v * 100)}%"></span></span> ${Math.round(v * 100)}%`])) : '<p class="muted">Not scored yet.</p>'}
        </div>
        <div class="card"><h3>Event timeline (${d.events.length})</h3>
          <div class="timeline" style="max-height:320px;overflow:auto">${d.events.map((e) => `<div class="ev"><span class="mono">${esc(formatTime(e.timestamp))}</span> · ${esc(e.type)}</div>`).join("") || '<p class="muted">No events.</p>'}</div>
        </div>
      </div>
      <div class="card"><h3>AI conversation (${d.ai_interactions.length})</h3>
        ${d.ai_interactions.map((i) => `<p class="muted" style="margin:10px 0 0">Student · ${esc(formatTime(i.timestamp))}</p><pre>${esc(i.prompt)}</pre><p class="muted" style="margin:6px 0 0">${esc(i.provider || "AI")} ${esc(i.model || "")}</p><pre>${esc(i.response ?? "(no response)")}</pre>`).join("") || '<p class="muted">The student did not use the AI assistant.</p>'}
      </div>
      <div class="card"><h3>Code runs (${d.runs.length})</h3>
        ${d.runs.map((r) => `<p class="muted" style="margin:10px 0 0">${esc(formatTime(r.timestamp))} · exit ${r.exit_status ?? "?"}${r.timed_out ? " · timed out" : ""} · ${r.duration_ms} ms</p>${r.stdout ? `<pre>${esc(r.stdout)}</pre>` : ""}${r.stderr ? `<pre style="color:#f2b8a8">${esc(r.stderr)}</pre>` : ""}`).join("") || '<p class="muted">No runs.</p>'}
      </div>
      <div class="card"><h3>Latest code</h3><pre>${esc(d.final_code ?? "(none saved)")}</pre></div>`;
    document.getElementById("back").addEventListener("click", () => showShell("overview"));
  });
}

// -- resources --------------------------------------------------------------------

function formatSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1048576 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}

function showResources(quiet) {
  return guarded(async () => {
    const rows = await get("/resources");
    main().innerHTML = `
      <div class="page-head"><div><h1>Resources</h1>
      <p class="muted">Everything professors have shared. Contents stay private to each professor's class; here you can see what exists and remove it.</p></div></div>
      <div class="card">${table(
        ["ID", "Title", "Type", "Detail", "Owner", "Shared with", "Labs", "Added", ""],
        rows.map((r) => [
          r.id,
          esc(r.title),
          esc(r.kind.toLowerCase()),
          esc(r.detail || "") + (r.size_bytes ? ` · ${formatSize(r.size_bytes)}` : ""),
          esc(r.owner || ""),
          esc(r.shared_with),
          r.labs,
          esc(formatTime(r.created_at)),
          `<button class="danger" data-delete-resource="${r.id}" data-title="${esc(r.title)}">Delete</button>`,
        ]),
        { empty: "Nothing has been shared yet." }
      )}</div>`;
    main().querySelectorAll("[data-delete-resource]").forEach((b) =>
      b.addEventListener("click", () =>
        confirmBox(`Delete "${b.dataset.title}"?`, "Students lose access immediately. This can't be undone.", "Delete", async () => {
          await post(`/resources/${b.dataset.deleteResource}/delete`);
          toast("Resource deleted.");
          showResources();
        })
      )
    );
    if (!quiet) autoRefresh("resources", showResources, 6);
  });
}

// -- audit log ------------------------------------------------------------------

let auditPage = 1;
function showAudit() {
  return guarded(async () => {
    const d = await get(`/audit?page=${auditPage}&page_size=50`);
    const pages = Math.max(1, Math.ceil(d.total / d.page_size));
    main().innerHTML = `
      <div class="page-head"><div><h1>Audit log</h1>
      <p class="muted">Who signed in or out, and what was changed. ${d.total} entries. Passwords and lab content are never recorded here.</p></div>
      <button id="auditRefresh">Refresh</button></div>
      <div class="card">${table(
        ["When", "Who", "Action", "Detail", "From"],
        d.rows.map((r) => [esc(formatTime(r.timestamp)), esc(r.actor), esc(r.action.replaceAll("_", " ")), esc(r.detail || ""), esc(r.address || "")]),
        { empty: "Nothing recorded yet." }
      )}</div>
      <div class="toolbar"><button id="aPrev" ${auditPage <= 1 ? "disabled" : ""}>Newer</button>
        <span class="muted">Page ${d.page} of ${pages}</span>
        <button id="aNext" ${auditPage >= pages ? "disabled" : ""}>Older</button></div>`;
    document.getElementById("auditRefresh").addEventListener("click", showAudit);
    document.getElementById("aPrev").addEventListener("click", () => { auditPage--; showAudit(); });
    document.getElementById("aNext").addEventListener("click", () => { auditPage++; showAudit(); });
  });
}

// -- AI -------------------------------------------------------------------------

function showAi() {
  return guarded(async () => {
    const [ai, providers] = await Promise.all([get("/ai"), get("/ai/providers")]);
    const hasModelList = (key) => key !== "groq" && key !== "ollama";
    main().innerHTML = `
      <div class="page-head"><div><h1>AI assistant</h1>
      <p class="muted">The class assistant: used to score sessions, and for chat by any student who hasn't connected their own. Students can connect their own key (Gemini, Claude, OpenAI or Grok) in the app; that stays on their computer.</p></div></div>
      <div class="card">
        <p>${ai.available ? '<span class="pill ok">Ready</span>' : '<span class="pill bad">Not available</span>'}
          Using <strong>${esc(ai.provider || "none")}</strong> ${ai.model ? "· " + esc(ai.model) : ""}</p>
        ${ai.problem ? `<p class="muted">${esc(ai.problem)}</p>` : ""}
        <label>Assistant
          <select id="aiProvider">${providers.map((p) => `<option value="${p.key}">${esc(p.label)}</option>`).join("")}</select></label>
        <div id="keyBlock">
          <label>API key <input id="aiKey" type="password" autocomplete="off" /></label>
          <p class="muted" id="aiHelp" style="margin:6px 0 0"></p>
          <button id="aiCheck" style="margin-top:8px">Check key</button>
        </div>
        <label id="modelBlock" style="display:none">Model <select id="aiModel"></select></label>
        <p class="muted">The key is kept in the server's memory only and is never written to the database. Restarting the server clears it.</p>
        <p class="error" id="aiError"></p>
        <button class="primary" id="aiSave" disabled>Connect</button>
      </div>`;
    const provider = document.getElementById("aiProvider");
    provider.value = providers.some((p) => p.key === ai.provider) ? ai.provider : providers[0].key;
    const cur = () => providers.find((p) => p.key === provider.value);
    const reset = () => {
      document.getElementById("modelBlock").style.display = "none";
      document.getElementById("aiError").textContent = "";
      const k = cur().key;
      document.getElementById("aiSave").disabled = !(k === "ollama" || (k === "groq" && document.getElementById("aiKey").value.trim()));
    };
    const sync = () => {
      const p = cur();
      document.getElementById("keyBlock").style.display = p.needs_key ? "" : "none";
      document.getElementById("aiCheck").style.display = hasModelList(p.key) ? "" : "none";
      document.getElementById("aiKey").placeholder = p.key_hint;
      document.getElementById("aiKey").value = "";
      document.getElementById("aiHelp").innerHTML = p.needs_key ? `Get a key at <a href="${esc(p.help_url)}" target="_blank" rel="noopener">${esc(p.help_url.replace("https://", ""))}</a>` : "Needs Ollama installed and running on the server computer.";
      reset();
    };
    provider.addEventListener("change", sync);
    document.getElementById("aiKey").addEventListener("input", reset);
    document.getElementById("aiCheck").addEventListener("click", async () => {
      const button = document.getElementById("aiCheck");
      button.disabled = true;
      button.textContent = "Checking…";
      try {
        const r = await post("/ai/check", { provider: provider.value, api_key: document.getElementById("aiKey").value });
        if (!r.ok) { document.getElementById("aiError").textContent = r.error; return; }
        document.getElementById("aiError").textContent = "";
        document.getElementById("aiModel").innerHTML = r.models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
        document.getElementById("modelBlock").style.display = "";
        document.getElementById("aiSave").disabled = false;
        toast("Key accepted. Choose a model, then Connect.");
      } catch (err) {
        document.getElementById("aiError").textContent = err.message;
      } finally {
        button.disabled = false;
        button.textContent = "Check key";
      }
    });
    document.getElementById("aiSave").addEventListener("click", async () => {
      try {
        const k = cur().key;
        const result = await post("/ai", { provider: k, api_key: document.getElementById("aiKey").value, model: hasModelList(k) ? document.getElementById("aiModel").value : "" });
        if (!result.ok) { document.getElementById("aiError").textContent = result.error || "That didn't work."; return; }
        toast("AI assistant updated.");
        showAi();
      } catch (err) {
        document.getElementById("aiError").textContent = err.message;
      }
    });
    sync();
  });
}

// -- backup ---------------------------------------------------------------------

function showBackup() {
  main().innerHTML = `
    <div class="page-head"><div><h1>Backup</h1>
    <p class="muted">Download a complete copy of the database, taken safely while the server is running.</p></div></div>
    <div class="card">
      <p>The file is a standard SQLite database. It contains every account (passwords are stored hashed), lab, and recorded session. Treat it as private.</p>
      <button class="primary" id="backupBtn">Download backup</button>
    </div>`;
  document.getElementById("backupBtn").addEventListener("click", () => download("/backup", "cavy-backup.db"));
}

// -- start ----------------------------------------------------------------------

(async function start() {
  if (token()) {
    try {
      await get("/overview");
      adminName = adminName || "Admin";
      showShell("overview");
      return;
    } catch {
      setToken(null);
    }
  }
  showAuth();
})();
