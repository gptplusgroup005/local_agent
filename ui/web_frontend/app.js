const state = {
  arduinoDirty: false,
  arduinoVerifyRunning: false,
  refreshPromise: null,
  arduinoFqbnFull: "",
  arduinoBoardName: "",
  themeHydrated: false,
  lastVerifyText: "Sandbox compile has not been run.",
  lastIssueText: "",
  lastRefreshAt: 0,
  workspaceMutationVersion: 0,
  workspaceMutationRunning: false,
  workspaceSelectionRunning: false,
  selectedWorkspacePath: "",
  activeFilePath: "",
  editorOriginalContent: "",
  editorDirty: false,
  editorLoading: false,
  editorSaving: false,
  codexBusy: false,
  codexMessagesSignature: "",
  codexRefreshPromise: null,
  codexRefreshTimer: null,
};

const THEMES = ["light", "dark", "neutral"];
const THEME_KEY = "talos-theme";
const RAIL_PIN_KEY = "talos-rail-pinned";
const CODEX_PANEL_KEY = "talos-codex-panel-open";
const FAST_REFRESH_MS = 1000;
const IDLE_REFRESH_MS = 5000;
const REFRESH_TICK_MS = 250;
const CODEX_BUSY_REFRESH_MS = 400;
const CODEX_IDLE_REFRESH_MS = 3000;
const CODEX_HIDDEN_REFRESH_MS = 8000;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(payload.error || text || response.statusText);
  return payload;
}

function setView(viewId) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  $$(".nav").forEach((button) => button.classList.toggle("active", button.dataset.view === viewId));
  document.body.classList.toggle("workbench-mode", viewId === "workspace");
  if (viewId === "workspace") {
    refresh();
    refreshCodex();
  }
}

function codexPanelOpen() {
  return localStorage.getItem(CODEX_PANEL_KEY) !== "false";
}

function applyCodexPanel(open) {
  $(".ide-workbench")?.classList.toggle("codex-hidden", !open);
  $("#toggleCodexBtn")?.classList.toggle("active", open);
  $("#toggleCodexBtn")?.setAttribute("aria-pressed", String(Boolean(open)));
  localStorage.setItem(CODEX_PANEL_KEY, String(Boolean(open)));
  scheduleCodexRefresh(0);
}

function activeViewId() {
  return $(".view.active")?.id || "dashboard";
}

function currentTheme() {
  const checked = document.querySelector('input[name="theme"]:checked')?.value;
  if (THEMES.includes(checked)) return checked;
  const active = document.documentElement.dataset.theme;
  if (THEMES.includes(active)) return active;
  const stored = localStorage.getItem(THEME_KEY);
  return THEMES.includes(stored) ? stored : "light";
}

function applyTheme(theme) {
  const nextTheme = THEMES.includes(theme) ? theme : "light";
  document.documentElement.dataset.theme = nextTheme;
  localStorage.setItem(THEME_KEY, nextTheme);
  const input = document.querySelector(`input[name="theme"][value="${nextTheme}"]`);
  if (input) input.checked = true;
}

function railPinned() {
  return document.documentElement.dataset.rail === "pinned";
}

function applyRailPinned(pinned) {
  document.documentElement.dataset.rail = pinned ? "pinned" : "collapsed";
  localStorage.setItem(RAIL_PIN_KEY, String(Boolean(pinned)));
  const button = $("#pinRailBtn");
  if (button) {
    button.classList.toggle("active", pinned);
    button.title = pinned ? "Unpin navigation" : "Pin navigation open";
    button.setAttribute("aria-label", button.title);
    button.setAttribute("aria-pressed", String(Boolean(pinned)));
  }
}

function hydrateTheme(config = {}) {
  if (state.themeHydrated) return;
  state.themeHydrated = true;
  if (THEMES.includes(config.theme)) {
    applyTheme(config.theme);
    return;
  }
  const stored = localStorage.getItem(THEME_KEY);
  if (THEMES.includes(stored)) applyTheme(stored);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function compactBoardLabel(fqbn, boardName = "") {
  if (boardName) return boardName;
  if (!fqbn) return "";
  return fqbn.split(":").slice(0, 3).join(":") || fqbn;
}

function boardInfoText(fqbn, boardName = "") {
  if (!fqbn) return "No board details available.";
  const parts = fqbn.split(":");
  const base = parts.slice(0, 3).join(":");
  const options = parts.slice(3).join(":");
  const lines = [];
  if (boardName) lines.push(`Board: ${boardName}`);
  if (base) lines.push(`FQBN: ${base}`);
  if (options) {
    lines.push("", "Options:");
    options.split(",").forEach((option) => lines.push(`- ${option}`));
  }
  return lines.join("\n");
}

function commandBaseName(command = "") {
  const head = String(command).trim().split(/\s+/)[0] || "";
  return head.split(/[\\/]/).pop() || head;
}

function formatBytes(value = 0) {
  const number = Number(value || 0);
  if (number >= 1024 * 1024) return `${(number / (1024 * 1024)).toFixed(1)} MB`;
  if (number >= 1024) return `${(number / 1024).toFixed(1)} KB`;
  return `${number} B`;
}

function memorySummary(memory = {}) {
  const rows = [];
  if (memory.program) {
    rows.push(["Program", `${formatBytes(memory.program.used)} / ${formatBytes(memory.program.maximum)} (${memory.program.percent}%)`]);
  }
  if (memory.dynamic) {
    rows.push(["Dynamic", `${formatBytes(memory.dynamic.used)} / ${formatBytes(memory.dynamic.maximum)} (${memory.dynamic.percent}%)`]);
  }
  return rows;
}

function verifySummaryHtml(result = {}) {
  const rows = memorySummary(result.memory || {});
  const libraries = result.libraries || [];
  const platforms = result.platforms || [];
  const issues = result.issues || [];
  if (!rows.length && !libraries.length && !platforms.length && !issues.length) return "";
  return `
    <div class="verify-summary">
      ${rows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join("")}
      ${libraries.length ? `<div><span>Libraries</span><b>${escapeHtml(libraries.map((item) => `${item.name} ${item.version}`.trim()).join(", "))}</b></div>` : ""}
      ${platforms.length ? `<div><span>Platform</span><b>${escapeHtml(platforms.map((item) => `${item.name} ${item.version}`.trim()).join(", "))}</b></div>` : ""}
      ${issues.length ? `<div><span>Issues</span><b>${escapeHtml(String(issues.length))}</b></div>` : ""}
    </div>
  `;
}

function issueFileLabel(file = "") {
  const parts = String(file).split(/[\\/]/);
  return parts.pop() || String(file);
}

function verifyIssuesHtml(issues = []) {
  if (!issues.length) return "";
  return `
    <section class="verify-issues" aria-label="Compile issues">
      <div class="verify-section-title">Compile issues</div>
      <div class="verify-issue-list">
        ${issues.map((issue) => {
          const level = String(issue.level || "error").toLowerCase();
          const location = [
            issueFileLabel(issue.file),
            Number(issue.line || 0) || "",
            Number(issue.column || 0) || "",
          ].filter((value) => value !== "").join(":");
          return `
            <div class="verify-issue ${level === "warning" ? "warning" : "error"}">
              <span class="verify-issue-level">${escapeHtml(level)}</span>
              <code class="verify-issue-location" title="${escapeHtml(issue.file || "")}">${escapeHtml(location || "Compiler")}</code>
              <span class="verify-issue-message">${escapeHtml(issue.message || "Unknown compiler issue.")}</span>
            </div>
          `;
        }).join("")}
      </div>
    </section>
  `;
}

function renderVerifyOutput(result = null, pendingText = "") {
  const output = $("#arduinoOutput");
  if (!result) {
    output.className = `verify-output ${pendingText ? "pending" : "empty"}`;
    state.lastVerifyText = pendingText || "Sandbox compile has not been run.";
    state.lastIssueText = "";
    $("#copyIssuesBtn").disabled = true;
    output.textContent = state.lastVerifyText;
    return;
  }
  const ok = Boolean(result.ok);
  const status = result.status || (ok ? "passed" : "failed");
  const command = result.command || "";
  const sandbox = result.sandbox || "";
  state.lastVerifyText = [
    `Status: ${status}`,
    command ? `Command: ${command}` : "",
    sandbox ? `Sandbox: ${sandbox}` : "",
    "",
    result.output || "No compiler output.",
  ].filter(Boolean).join("\n");
  state.lastIssueText = result.issue_context || "";
  $("#copyIssuesBtn").disabled = !state.lastIssueText;
  output.className = `verify-output ${ok ? "passed" : "failed"}`;
  output.innerHTML = `
    <div class="verify-head">
      <span class="verify-badge">${escapeHtml(status)}</span>
      <span class="verify-command-name">${escapeHtml(commandBaseName(command) || "arduino-cli")}</span>
    </div>
    ${command ? `<div class="verify-field"><span>Command</span><code>${escapeHtml(command)}</code></div>` : ""}
    ${sandbox ? `<div class="verify-field"><span>Sandbox</span><code>${escapeHtml(sandbox)}</code></div>` : ""}
    ${verifySummaryHtml(result)}
    ${verifyIssuesHtml(result.issues || [])}
    <pre class="verify-log">${escapeHtml(result.output || "No compiler output.")}</pre>
  `;
}

async function copyText(text, statusSelector = "") {
  const value = String(text || "").trim();
  if (!value) return;
  await navigator.clipboard.writeText(value);
  if (statusSelector) {
    const status = $(statusSelector);
    if (status) {
      const previous = status.textContent;
      status.textContent = "Copied.";
      window.setTimeout(() => {
        status.textContent = previous;
      }, 1200);
    }
  }
}

function fileListText() {
  return $$("#arduinoFiles tr").map((row) => [...row.children].map((cell) => cell.textContent.trim()).join("\t")).join("\n");
}

function setEditorDirty(dirty) {
  state.editorDirty = Boolean(dirty);
  $("#editorDirtyBadge").hidden = !state.editorDirty;
  $("#saveFileBtn").disabled = !state.activeFilePath || !state.editorDirty || state.editorSaving;
}

function resetEditor(message = "No file selected.") {
  state.activeFilePath = "";
  state.editorOriginalContent = "";
  state.editorLoading = false;
  state.editorSaving = false;
  $("#editorFileName").textContent = "Select a source file";
  $("#sourceEditor").value = "";
  $("#sourceEditor").disabled = true;
  $("#editorStatus").textContent = message;
  setEditorDirty(false);
}

function canDiscardEditorChanges() {
  return !state.editorDirty || window.confirm("Discard unsaved changes in the current source file?");
}

async function openWorkspaceFile(path) {
  if (!path || path === state.activeFilePath || state.editorLoading) return;
  if (!canDiscardEditorChanges()) return;
  state.editorLoading = true;
  $("#editorStatus").textContent = `Loading ${path}...`;
  try {
    const result = await api(`/api/arduino_file?path=${encodeURIComponent(path)}`);
    state.activeFilePath = result.path;
    state.editorOriginalContent = result.content || "";
    $("#editorFileName").textContent = result.path;
    $("#sourceEditor").value = state.editorOriginalContent;
    $("#sourceEditor").disabled = false;
    $("#editorStatus").textContent = `${Number(result.bytes || 0)} bytes`;
    setEditorDirty(false);
    renderActiveFileRow();
    $("#sourceEditor").focus();
  } catch (error) {
    $("#editorStatus").textContent = `Open failed: ${error.message}`;
  } finally {
    state.editorLoading = false;
  }
}

function renderActiveFileRow() {
  $$("#arduinoFiles tr").forEach((row) => {
    row.classList.toggle("active", row.dataset.path === state.activeFilePath);
  });
}

async function saveWorkspaceFile() {
  if (!state.activeFilePath || !state.editorDirty || state.editorSaving) return;
  state.editorSaving = true;
  $("#saveFileBtn").disabled = true;
  $("#editorStatus").textContent = `Saving ${state.activeFilePath}...`;
  try {
    const content = $("#sourceEditor").value;
    const result = await api("/api/arduino_file", {
      method: "POST",
      body: JSON.stringify({ path: state.activeFilePath, content }),
    });
    state.editorOriginalContent = content;
    $("#editorStatus").textContent = `Saved ${result.path} (${Number(result.bytes || 0)} bytes).`;
    setEditorDirty(false);
    await refresh();
  } catch (error) {
    $("#editorStatus").textContent = `Save failed: ${error.message}`;
  } finally {
    state.editorSaving = false;
    $("#saveFileBtn").disabled = !state.activeFilePath || !state.editorDirty;
  }
}

function setBoardField(fqbn = "", boardName = "") {
  state.arduinoFqbnFull = fqbn || "";
  state.arduinoBoardName = boardName || "";
  $("#arduinoFqbnInput").value = compactBoardLabel(state.arduinoFqbnFull, state.arduinoBoardName);
  $("#boardInfoPanel").textContent = boardInfoText(state.arduinoFqbnFull, state.arduinoBoardName);
  $("#boardInfoBtn").disabled = !state.arduinoFqbnFull;
}

function normalizedWindowsPath(value = "") {
  return String(value).trim().replaceAll("/", "\\").replace(/\\+$/, "").toLowerCase();
}

function renderStats(payload) {
  const arduino = payload.arduino || {};
  const projects = payload.arduino_projects || [];
  const rows = [
    ["Role", payload.role || "Tool server"],
    ["Native C", payload.native_available ? "Loaded" : "Not built"],
    ["Open sketches", String(projects.length)],
    ["Arduino", arduino.valid ? "Ready" : "Not ready"],
  ];
  $("#stats").innerHTML = rows
    .map(([label, value]) => `<div class="stat"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`)
    .join("");
}

function renderArduino(arduino, force = false, ide = {}) {
  if (!arduino) return;
  const workspaceChanged = normalizedWindowsPath(arduino.path) !== normalizedWindowsPath(state.selectedWorkspacePath);
  if (workspaceChanged) {
    state.selectedWorkspacePath = arduino.path || "";
    resetEditor(arduino.valid ? "Select a source file." : "No valid workspace selected.");
  }
  if (!state.arduinoDirty || force) {
    $("#arduinoPathInput").value = arduino.path || "";
  }
  const detectedFqbn = ide.fqbn || arduino.fqbn || "";
  const detectedBoardName = ide.board_name || (detectedFqbn === arduino.fqbn ? state.arduinoBoardName : "");
  if (
    force
    || detectedFqbn !== state.arduinoFqbnFull
    || detectedBoardName !== state.arduinoBoardName
  ) {
    setBoardField(detectedFqbn, detectedBoardName);
  }
  $("#arduinoStatus").textContent = arduino.message || "No Arduino sketch folder configured.";
  $("#arduinoMeta").textContent = arduino.valid
    ? `${arduino.files.length} file(s) | main sketch: ${arduino.main_sketch}`
    : "Set a sketch folder that contains at least one .ino file.";
  $("#verifyArduinoBtn").disabled = state.arduinoVerifyRunning || !arduino.configured;
  $("#arduinoFiles").innerHTML = (arduino.files || []).map((file) => `
    <tr data-path="${escapeHtml(file.path)}" tabindex="0">
      <td>
        <span class="file-name">${escapeHtml(file.path)}</span>
        <span class="file-meta">${Number(file.lines || 0)} lines · ${Number(file.bytes || 0)} bytes</span>
      </td>
    </tr>
  `).join("");
  $$("#arduinoFiles tr").forEach((row) => {
    const open = () => openWorkspaceFile(row.dataset.path);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });
  renderActiveFileRow();
  renderCodexContext();
}

function renderCodexContext() {
  const chips = $$("#codexContext span");
  chips[0]?.classList.toggle("ready", Boolean(state.selectedWorkspacePath));
  chips[1]?.classList.toggle("ready", Boolean(state.activeFilePath));
  chips[2]?.classList.toggle(
    "ready",
    Boolean(state.lastIssueText || (state.lastVerifyText && !state.lastVerifyText.startsWith("Sandbox compile"))),
  );
}

function codexAccountLabel(payload = {}) {
  const account = payload.account || {};
  if (!payload.available) return "Codex runtime not found";
  if (payload.initializing) return "Starting local Codex...";
  if (payload.error && !account.type) return "Sign-in required";
  const plan = account.planType ? ` · ${account.planType}` : "";
  const email = account.email ? `${account.email}${plan}` : `${account.type || "ChatGPT"}${plan}`;
  return payload.connected ? email : "Connecting...";
}

function renderCodex(payload = {}) {
  state.codexBusy = Boolean(payload.busy);
  $("#codexAccount").textContent = codexAccountLabel(payload);
  $("#sendCodexBtn").disabled = !payload.ok || state.codexBusy;
  $("#codexInput").disabled = !payload.ok;
  $("#newCodexThreadBtn").disabled = state.codexBusy;
  $("#codexStatus").textContent = payload.error
    || (state.codexBusy ? "Codex is working..." : payload.thread_id ? "Thread ready" : "Ready for a new thread");
  const messages = payload.messages || [];
  const signature = JSON.stringify(messages);
  if (signature !== state.codexMessagesSignature) {
    state.codexMessagesSignature = signature;
    $("#codexMessages").innerHTML = messages.length
      ? messages.map((message) => `
          <article class="codex-message ${message.role === "user" ? "user" : "assistant"}">
            <span class="codex-message-role">${message.role === "user" ? "You" : "Codex"}</span>
            <div class="codex-message-body">${escapeHtml(message.text || "")}</div>
          </article>
        `).join("")
      : `<div class="codex-empty">Ask Codex to inspect, edit, verify, or optimize the selected Arduino sketch.</div>`;
    $("#codexMessages").scrollTop = $("#codexMessages").scrollHeight;
  }
  const activity = payload.activity || [];
  $("#codexActivity").hidden = !activity.length;
  $("#codexActivity").textContent = activity.join("\n");
}

async function refreshCodex() {
  if (state.codexRefreshPromise) return state.codexRefreshPromise;
  state.codexRefreshPromise = api("/api/codex_status")
    .then(renderCodex)
    .catch((error) => {
      renderCodex({ available: true, connected: false, error: error.message });
    })
    .finally(() => {
      state.codexRefreshPromise = null;
      scheduleCodexRefresh();
    });
  return state.codexRefreshPromise;
}

function scheduleCodexRefresh(delay = null) {
  window.clearTimeout(state.codexRefreshTimer);
  const nextDelay = delay ?? (
    document.hidden || activeViewId() !== "workspace" || !codexPanelOpen()
      ? CODEX_HIDDEN_REFRESH_MS
      : state.codexBusy
        ? CODEX_BUSY_REFRESH_MS
        : CODEX_IDLE_REFRESH_MS
  );
  state.codexRefreshTimer = window.setTimeout(refreshCodex, nextDelay);
}

async function sendCodexMessage() {
  const input = $("#codexInput");
  const message = input.value.trim();
  if (!message || state.codexBusy) return;
  $("#codexStatus").textContent = "Sending context to Codex...";
  $("#sendCodexBtn").disabled = true;
  try {
    await api("/api/codex_message", {
      method: "POST",
      body: JSON.stringify({
        message,
        active_file: state.activeFilePath
          ? { path: state.activeFilePath, content: $("#sourceEditor").value }
          : {},
        verify_context: state.lastIssueText || state.lastVerifyText,
      }),
    });
    input.value = "";
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = error.message;
    $("#sendCodexBtn").disabled = false;
  }
}

async function newCodexThread() {
  try {
    await api("/api/codex_thread", { method: "POST", body: "{}" });
    state.codexMessagesSignature = "";
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = error.message;
  }
}

function renderArduinoProjects(projects = []) {
  if (!projects.length) {
    $("#arduinoProjects").innerHTML = `<div class="project-row"><div><div class="project-title">No open Arduino sketches detected</div><div class="project-path">Open one or more .ino sketches in Arduino IDE, then refresh.</div></div></div>`;
    return;
  }
  $("#arduinoProjects").innerHTML = projects.map((project, index) => `
    <div class="project-row ${project.unsaved ? "unsaved" : ""}">
      <div>
        <div class="project-title">
          ${escapeHtml(project.sketch || "Arduino sketch")}
          ${project.unsaved ? '<span class="project-badge">Unsaved</span>' : ""}
          ${!project.valid && !project.unsaved ? '<span class="project-badge warning">Folder not found</span>' : ""}
        </div>
        ${project.unsaved ? '<div class="project-path">Save this sketch in Arduino IDE to create a selectable workspace.</div>' : ""}
      </div>
      <button class="button ghost select-project" data-index="${index}" ${project.valid && !state.workspaceSelectionRunning ? "" : "disabled"}>Select</button>
    </div>
  `).join("");
  $$(".select-project").forEach((button) => {
    button.addEventListener("click", async () => {
      if (state.workspaceSelectionRunning) return;
      if (!canDiscardEditorChanges()) return;
      const project = projects[Number(button.dataset.index)];
      if (!project?.path) return;
      state.workspaceSelectionRunning = true;
      $$(".select-project").forEach((item) => {
        item.disabled = true;
      });
      $("#arduinoPathInput").value = project.path;
      if (project.fqbn) setBoardField(project.fqbn, project.board_name || "");
      state.arduinoDirty = true;
      try {
        await saveArduinoWorkspace();
      } finally {
        state.workspaceSelectionRunning = false;
        await refreshAfterWorkspaceMutation();
      }
    });
  });
}

function render(payload) {
  hydrateTheme(payload.config || {});
  const projects = payload.arduino_projects || [];
  const arduino = payload.arduino || {};
  const selectedPath = normalizedWindowsPath(arduino.path);
  const selectedProject = projects.find((project) => (
    normalizedWindowsPath(project.path) === selectedPath && project.fqbn === arduino.fqbn
  ))
    || projects.find((project) => normalizedWindowsPath(project.path) === selectedPath)
    || {};
  $("#modeLine").textContent = `${payload.role} | ${payload.root}`;
  $("#toolList").textContent = (payload.tools || []).join("\n");
  renderStats(payload);
  renderArduino(arduino, false, selectedProject);
  renderArduinoProjects(projects);
  $("#logText").textContent = (payload.events || []).join("\n");
}

async function refresh() {
  if (state.workspaceMutationRunning) {
    return state.refreshPromise || null;
  }
  if (state.refreshPromise) return state.refreshPromise;
  const mutationVersion = state.workspaceMutationVersion;
  state.refreshPromise = api("/api/state")
    .then((payload) => {
      if (mutationVersion === state.workspaceMutationVersion) {
        render(payload);
      }
      state.lastRefreshAt = Date.now();
      return payload;
    })
    .finally(() => {
      state.refreshPromise = null;
    });
  return state.refreshPromise;
}

async function refreshAfterWorkspaceMutation() {
  if (state.refreshPromise) {
    try {
      await state.refreshPromise;
    } catch (_error) {
      // The fresh request below remains authoritative.
    }
  }
  return refresh();
}

function maybeRefresh() {
  if (document.hidden) return;
  const interval = activeViewId() === "workspace" ? FAST_REFRESH_MS : IDLE_REFRESH_MS;
  if (Date.now() - state.lastRefreshAt >= interval) refresh();
}

async function saveArduinoWorkspace() {
  state.workspaceMutationVersion += 1;
  state.workspaceMutationRunning = true;
  try {
    const result = await api("/api/arduino_workspace", {
      method: "POST",
      body: JSON.stringify({
        path: $("#arduinoPathInput").value,
        fqbn: state.arduinoFqbnFull || $("#arduinoFqbnInput").value,
      }),
    });
    state.arduinoDirty = false;
    renderArduino(result.arduino, true);
    return result;
  } finally {
    state.workspaceMutationRunning = false;
  }
}

async function verifyArduinoWorkspace() {
  renderVerifyOutput(null, "Copying sketch folder to sandbox and running arduino-cli compile...");
  state.arduinoVerifyRunning = true;
  $("#verifyArduinoBtn").disabled = true;
  try {
    const result = await api("/api/arduino_verify", {
      method: "POST",
      body: JSON.stringify({
        path: $("#arduinoPathInput").value,
        fqbn: state.arduinoFqbnFull || $("#arduinoFqbnInput").value,
      }),
    });
    renderVerifyOutput(result);
    state.arduinoDirty = false;
    await refresh();
    return result;
  } finally {
    state.arduinoVerifyRunning = false;
    $("#verifyArduinoBtn").disabled = false;
  }
}

async function saveSettings() {
  const result = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ theme: currentTheme() }),
  });
  $("#settingsStatus").textContent = `Saved ${result.config?.theme || currentTheme()} theme.`;
  return result;
}

function setMaximizeIcon(maximized) {
  const button = $("#maximizeBtn");
  if (!button) return;
  button.innerHTML = maximized ? "&#10064;" : "&#9633;";
  button.title = maximized ? "Restore" : "Maximize";
  button.setAttribute("aria-label", button.title);
}

async function syncWindowState() {
  const stateInfo = await window.pywebview?.api?.get_window_state?.();
  if (stateInfo) setMaximizeIcon(Boolean(stateInfo.maximized));
}

async function toggleMaximize() {
  const maximized = await window.pywebview?.api?.toggle_maximize?.();
  setMaximizeIcon(Boolean(maximized));
}

function snapTarget(kind) {
  const screenInfo = window.screen;
  const left = Number(screenInfo.availLeft ?? 0);
  const top = Number(screenInfo.availTop ?? 0);
  const width = Number(screenInfo.availWidth || screenInfo.width || 1200);
  const height = Number(screenInfo.availHeight || screenInfo.height || 800);
  const halfW = Math.round(width / 2);
  const halfH = Math.round(height / 2);
  const thirdW = Math.round(width / 3);
  const targets = {
    left: [left, top, halfW, height],
    right: [left + width - halfW, top, halfW, height],
    "third-left": [left, top, thirdW, height],
    "third-center": [left + thirdW, top, width - thirdW * 2, height],
    "top-left": [left, top, halfW, halfH],
    "bottom-right": [left + width - halfW, top + height - halfH, halfW, halfH],
  };
  return targets[kind] || targets.left;
}

async function snapWindow(kind) {
  const [x, y, width, height] = snapTarget(kind);
  await window.pywebview?.api?.snap_to?.(x, y, width, height);
  setMaximizeIcon(false);
  $("#snapMenu")?.classList.remove("open");
}

function bindEvents() {
  applyRailPinned(railPinned());
  applyCodexPanel(codexPanelOpen());
  $$(".nav").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#pinRailBtn").addEventListener("click", () => applyRailPinned(!railPinned()));
  $("#refreshBtn").addEventListener("click", refresh);
  $("#refreshWorkspaceBtn").addEventListener("click", refresh);
  $("#saveArduinoBtn").addEventListener("click", async () => {
    await saveArduinoWorkspace();
    await refreshAfterWorkspaceMutation();
  });
  $("#verifyArduinoBtn").addEventListener("click", verifyArduinoWorkspace);
  $("#saveSettingsBtn").addEventListener("click", saveSettings);
  $("#copyFilesBtn").addEventListener("click", () => copyText(fileListText(), "#arduinoMeta"));
  $("#copyIssuesBtn").addEventListener("click", () => copyText(state.lastIssueText));
  $("#copyVerifyBtn").addEventListener("click", () => copyText(state.lastVerifyText));
  $("#saveFileBtn").addEventListener("click", saveWorkspaceFile);
  $("#toggleCodexBtn").addEventListener("click", () => applyCodexPanel(!codexPanelOpen()));
  $("#closeCodexBtn").addEventListener("click", () => applyCodexPanel(false));
  $("#newCodexThreadBtn").addEventListener("click", newCodexThread);
  $("#codexComposer").addEventListener("submit", (event) => {
    event.preventDefault();
    sendCodexMessage();
  });
  $("#codexInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendCodexMessage();
    }
  });
  $("#sourceEditor").addEventListener("input", () => {
    setEditorDirty($("#sourceEditor").value !== state.editorOriginalContent);
    $("#editorStatus").textContent = state.editorDirty ? "Unsaved changes." : "No changes.";
  });
  $("#sourceEditor").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveWorkspaceFile();
    }
  });
  $("#boardInfoBtn").addEventListener("click", () => {
    const panel = $("#boardInfoPanel");
    const isHidden = panel.hidden;
    panel.hidden = !isHidden;
    $("#boardInfoBtn").classList.toggle("active", isHidden);
  });
  $$("#workspace input").forEach((input) => {
    input.addEventListener("input", () => {
      state.arduinoDirty = true;
      $("#verifyArduinoBtn").disabled = false;
    });
  });
  $$('input[name="theme"]').forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) applyTheme(input.value);
      $("#settingsStatus").textContent = "Theme changed. Save to keep it for next launch.";
    });
  });
  $(".app-chrome").addEventListener("dblclick", (event) => {
    if (event.target.closest(".chrome-actions")) return;
    toggleMaximize();
  });
  $("#minimizeBtn").addEventListener("click", () => window.pywebview?.api?.minimize?.());
  $("#maximizeBtn").addEventListener("click", toggleMaximize);
  $("#maximizeBtn").addEventListener("pointerdown", () => $("#snapMenu")?.classList.add("open"));
  $$(".snap-option").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      snapWindow(button.dataset.snap);
    });
  });
  $("#closeBtn").addEventListener("click", () => window.pywebview?.api?.close?.());
  syncWindowState();
}

bindEvents();
refresh();
setInterval(maybeRefresh, REFRESH_TICK_MS);
refreshCodex();
