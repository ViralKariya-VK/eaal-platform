// CIQ Pilot Survey — a small hand-rolled flow, no framework, matching
// CAVY's own frontend philosophy: one #app root, screens swap its content.

const app = document.getElementById("app");

let state = {
  token: null,
  files: { "main.py": "" },
  activeFilename: "main.py",
  taskInfo: null,
};

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

// -- Screen 1: Consent + Groq key -------------------------------------------

function renderConsent() {
  app.innerHTML = `
    <div class="card">
      <h2>Before you start</h2>
      <p class="muted">
        This is a short research pilot for a framework that measures how
        students use AI while coding &mdash; not you personally, and not your
        grade. It should take about 20 minutes: two small coding tasks with
        an AI assistant available, then a one-question survey.
      </p>
      <div class="consent-box">
        <b>What's collected:</b> the code you write, your messages to the AI
        assistant and its replies, and your answer to the closing question.
        No name, email, or other identifying information is requested.
        <br /><br />
        <b>Your Groq API key</b> is used only to call Groq's API for your
        session and is never stored or logged by this server &mdash; it lives
        in memory for the duration of your session and is discarded when you
        finish. Groq's free tier is more than enough for this.
        <a href="https://console.groq.com/keys" target="_blank" rel="noopener">Get a free key &rarr;</a>
        <br /><br />
        Participation is voluntary. You can close this tab at any point
        without submitting anything.
      </div>
      <label class="checkline">
        <input type="checkbox" id="consentCheck" />
        <span>I understand what's collected above and agree to take part.</span>
      </label>
      <label for="groqKeyInput">Groq API key</label>
      <input type="password" id="groqKeyInput" placeholder="gsk_..." autocomplete="off" />
      <div id="consentError"></div>
      <div class="row" style="margin-top:16px;">
        <button class="primary" id="beginButton">Begin</button>
      </div>
    </div>
  `;

  document.getElementById("beginButton").addEventListener("click", async () => {
    const consented = document.getElementById("consentCheck").checked;
    const key = document.getElementById("groqKeyInput").value.trim();
    const errorEl = document.getElementById("consentError");
    errorEl.innerHTML = "";
    if (!consented) {
      errorEl.innerHTML = `<div class="error">Please check the consent box to continue.</div>`;
      return;
    }
    if (!key) {
      errorEl.innerHTML = `<div class="error">A Groq API key is required.</div>`;
      return;
    }
    const button = document.getElementById("beginButton");
    button.disabled = true;
    button.textContent = "Checking key...";
    try {
      const result = await api("/api/session/start", { groq_api_key: key });
      state.token = result.token;
      state.taskInfo = result.task;
      state.files = { "main.py": result.task.starter_files["main.py"] || "" };
      renderTask();
    } catch (err) {
      errorEl.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
      button.disabled = false;
      button.textContent = "Begin";
    }
  });
}

// -- Screen 2: Task workspace -------------------------------------------------

function renderTask() {
  const info = state.taskInfo;
  app.innerHTML = `
    <div class="card">
      <span class="pill">${escapeHtml(info.task_title)}</span>
      <h2 style="margin-top:10px;">Task</h2>
      <p class="muted">${escapeHtml(info.task_description)}</p>
    </div>
    <div class="card">
      <label for="codeArea">Your code (main.py)</label>
      <textarea class="code" id="codeArea" spellcheck="false">${escapeHtml(state.files["main.py"])}</textarea>
      <div class="row" style="margin-top:10px;">
        <button class="run" id="runButton">Run Code</button>
        <button class="primary" id="submitButton">Submit &amp; Continue</button>
      </div>
      <div id="runOutput"></div>
    </div>
    <div class="card">
      <h2>AI Assistant</h2>
      <div class="chat-log" id="chatLog"></div>
      <div class="row">
        <input type="text" id="chatInput" placeholder="Ask the assistant anything about this task..." style="flex:1;" />
        <button id="sendButton">Send</button>
      </div>
    </div>
  `;

  const codeArea = document.getElementById("codeArea");
  codeArea.addEventListener("input", () => {
    state.files["main.py"] = codeArea.value;
  });
  codeArea.addEventListener(
    "blur",
    () => {
      api("/api/code/edit", { token: state.token, files: state.files, active_filename: "main.py" }).catch(
        () => {}
      );
    },
    { passive: true }
  );

  document.getElementById("runButton").addEventListener("click", async () => {
    const button = document.getElementById("runButton");
    button.disabled = true;
    button.textContent = "Running...";
    try {
      const result = await api("/api/code/run", { token: state.token, files: state.files });
      const text = (result.stdout || "") + (result.stderr ? "\n" + result.stderr : "");
      document.getElementById("runOutput").innerHTML = `<div class="output-pane">${escapeHtml(
        text || "(no output)"
      )}</div>`;
    } catch (err) {
      document.getElementById("runOutput").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    }
    button.disabled = false;
    button.textContent = "Run Code";
  });

  document.getElementById("submitButton").addEventListener("click", async () => {
    const button = document.getElementById("submitButton");
    button.disabled = true;
    button.textContent = "Submitting...";
    try {
      const result = await api("/api/task/submit", { token: state.token, files: state.files });
      if (result.next === "task2") {
        state.taskInfo = result.task;
        state.files = { "main.py": result.task.starter_files["main.py"] || "" };
        renderTask();
      } else {
        renderScore(result.summary);
      }
    } catch (err) {
      button.disabled = false;
      button.textContent = "Submit & Continue";
      alert(err.message);
    }
  });

  document.getElementById("sendButton").addEventListener("click", sendChatMessage);
  document.getElementById("chatInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendChatMessage();
  });
}

function appendChatBubble(role, text) {
  const log = document.getElementById("chatLog");
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role === "me" ? "me" : "ai"}`;
  bubble.textContent = text;
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
}

async function sendChatMessage() {
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  appendChatBubble("me", message);
  appendChatBubble("ai", "Thinking...");
  try {
    const result = await api("/api/chat/send", { token: state.token, message, files: state.files });
    const log = document.getElementById("chatLog");
    log.lastChild.textContent = result.available ? result.text : "The assistant is unavailable right now.";
  } catch (err) {
    document.getElementById("chatLog").lastChild.textContent = "Error: " + err.message;
  }
}

// -- Screen 3: Score reveal ---------------------------------------------------

function renderScore(summary) {
  const tiles = Object.entries(summary)
    .map(([label, value]) => {
      const display = value === null ? "—" : `${value}%`;
      return `<div class="stat-tile"><div class="value">${display}</div><div class="label">${escapeHtml(label)}</div></div>`;
    })
    .join("");

  app.innerHTML = `
    <div class="card">
      <h2>Your results</h2>
      <p class="muted">
        This reflects how you worked across both tasks, not just whether your
        code was correct &mdash; things like whether you verified AI
        suggestions, reasoned through the problem yourself, and could apply
        the idea to a new, different problem.
      </p>
      <div class="stat-row">${tiles}</div>
      <button class="primary" id="continueButton">Continue</button>
    </div>
  `;
  document.getElementById("continueButton").addEventListener("click", renderRating);
}

// -- Screen 4: Self-rating ----------------------------------------------------

let selectedRating = null;

function renderRating() {
  const labels = ["Much lower", "Lower", "About right", "Higher", "Much higher"];
  app.innerHTML = `
    <div class="card">
      <h2>One last question</h2>
      <p class="muted">
        Compared to how you'd rate your own performance, was the score you
        just saw about what you expected?
      </p>
      <div class="rating-scale" id="ratingScale">
        ${labels
          .map((label, i) => `<button data-value="${i + 1}">${escapeHtml(label)}</button>`)
          .join("")}
      </div>
      <label for="ratingComment">Anything you'd add? (optional)</label>
      <textarea id="ratingComment" rows="3" placeholder="Optional comment..."></textarea>
      <div class="row" style="margin-top:16px;">
        <button class="primary" id="finishButton" disabled>Finish</button>
      </div>
    </div>
  `;

  document.querySelectorAll("#ratingScale button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#ratingScale button").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      selectedRating = Number(btn.dataset.value);
      document.getElementById("finishButton").disabled = false;
    });
  });

  document.getElementById("finishButton").addEventListener("click", async () => {
    const button = document.getElementById("finishButton");
    button.disabled = true;
    button.textContent = "Submitting...";
    try {
      await api("/api/self_rating", {
        token: state.token,
        self_rating: selectedRating,
        self_rating_comment: document.getElementById("ratingComment").value.trim(),
      });
      renderThanks();
    } catch (err) {
      alert(err.message);
      button.disabled = false;
      button.textContent = "Finish";
    }
  });
}

// -- Screen 5: Thanks ----------------------------------------------------------

function renderThanks() {
  app.innerHTML = `
    <div class="card">
      <h2>Thank you</h2>
      <p class="muted">
        Your responses have been recorded anonymously. You can close this
        tab now &mdash; there's nothing else to do.
      </p>
    </div>
  `;
}

renderConsent();
