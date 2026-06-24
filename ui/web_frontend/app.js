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
  activeFileByWorkspace: {},
  activeFilePath: "",
  editorOriginalContent: "",
  editorFileMtimeNs: 0,
  editorDirty: false,
  localEditMode: false,
  editorLoading: false,
  editorSaving: false,
  editorDiskChecking: false,
  lastCheckpoint: null,
  codexBusy: false,
  codexMessagesSignature: "",
  codexRefreshPromise: null,
  codexRefreshTimer: null,
  codexPatchRevision: 0,
  codexPatchEventRevision: null,
  codexChangedFiles: new Set(),
  conflictedFilePaths: new Set(),
  codexPreviewRevision: 0,
  codexPreviewPath: "",
  codexPreviewStreaming: false,
  codexPreviewTimer: null,
  codexPreviewCommitted: false,
  codexPreviewBaseContent: "",
  codexPreviewContent: "",
  codexReviewPatch: null,
  codexPatches: [],
  codexApplyingPatchId: "",
  codexPatchVerifyRunning: false,
  codexConversationSignature: "",
  codexHistoryExpanded: false,
  codexConversations: [],
  codexTasksVisible: true,
  runHistorySignature: "",
};

const THEMES = ["light", "dark", "neutral"];
const THEME_KEY = "talos-theme";
const RAIL_PIN_KEY = "talos-rail-pinned";
const CODEX_PANEL_KEY = "talos-codex-panel-open";
const EXPLORER_WIDTH_KEY = "talos-explorer-pane-width";
const FAST_REFRESH_MS = 1000;
const IDLE_REFRESH_MS = 5000;
const REFRESH_TICK_MS = 250;
const CODEX_BUSY_REFRESH_MS = 400;
const CODEX_IDLE_REFRESH_MS = 3000;
const CODEX_HIDDEN_REFRESH_MS = 8000;
const ACTIVE_FILE_POLL_MS = 700;

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

function clampPaneWidth(value, minimum, maximum) {
  return Math.min(Math.max(Number(value) || minimum, minimum), maximum);
}

function restorePaneWidths() {
  const root = document.documentElement;
  const explorer = Number(localStorage.getItem(EXPLORER_WIDTH_KEY));
  if (Number.isFinite(explorer) && explorer > 0) {
    root.style.setProperty("--explorer-pane-width", `${clampPaneWidth(explorer, 240, 420)}px`);
  }
}

function resetExplorerWidth() {
  document.documentElement.style.removeProperty("--explorer-pane-width");
  localStorage.removeItem(EXPLORER_WIDTH_KEY);
}

function bindExplorerSplitter(selector) {
  const splitter = $(selector);
  if (!splitter) return;
  splitter.addEventListener("dblclick", resetExplorerWidth);
  splitter.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || window.innerWidth <= 900) return;
    const workbench = splitter.closest(".ide-workbench");
    if (!workbench) return;
    event.preventDefault();
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add("dragging");
    const bounds = workbench.getBoundingClientRect();
    const update = (clientX) => {
      const available = bounds.width;
      const minimumEditor = Math.min(520, Math.max(360, available * 0.38));
      const maximum = Math.max(240, available - minimumEditor - 320 - 8);
      const width = clampPaneWidth(clientX - bounds.left, 240, maximum);
      document.documentElement.style.setProperty("--explorer-pane-width", `${Math.round(width)}px`);
      localStorage.setItem(EXPLORER_WIDTH_KEY, String(Math.round(width)));
    };
    const move = (moveEvent) => update(moveEvent.clientX);
    const finish = () => {
      splitter.classList.remove("dragging");
      splitter.removeEventListener("pointermove", move);
      splitter.removeEventListener("pointerup", finish);
      splitter.removeEventListener("pointercancel", finish);
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", finish);
    splitter.addEventListener("pointercancel", finish);
    update(event.clientX);
  });
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

function hydrateAppIdentity(app = {}) {
  const displayName = app.display_name || app.app_name || "Talos";
  const channel = app.channel ? ` ${app.channel}` : "";
  const version = app.version ? `v${app.version}${channel}` : channel.trim();
  document.title = displayName;
  $("#chromeAppName").textContent = displayName;
  $("#brandName").textContent = displayName;
  $("#heroAppName").textContent = displayName;
  $("#modeLine").dataset.version = version || "";
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

function formatDuration(value = 0) {
  const seconds = Number(value || 0);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(2)} s`;
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
  const timings = result.timings || {};
  const libraries = result.libraries || [];
  const platforms = result.platforms || [];
  const issues = result.issues || [];
  const timingRows = [
    ["Prepare", timings.prepare],
    ["Sandbox copy", timings.sandbox_copy],
    ["Compile", timings.compile],
    ["Total", timings.total],
  ].filter(([, value]) => Number.isFinite(Number(value)));
  if (!rows.length && !timingRows.length && !libraries.length && !platforms.length && !issues.length) return "";
  return `
    <div class="verify-summary">
      ${rows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join("")}
      ${timingRows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(formatDuration(value))}</b></div>`).join("")}
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

function setOutputView(view) {
  const historyVisible = view === "history";
  $("#arduinoOutput").toggleAttribute("hidden", historyVisible);
  $("#runHistory").toggleAttribute("hidden", !historyVisible);
  $("#arduinoOutput").setAttribute("aria-hidden", String(historyVisible));
  $("#runHistory").setAttribute("aria-hidden", String(!historyVisible));
  $("#verifyOutputTab").classList.toggle("active", !historyVisible);
  $("#runHistoryTab").classList.toggle("active", historyVisible);
  $("#verifyOutputTab").setAttribute("aria-selected", String(!historyVisible));
  $("#runHistoryTab").setAttribute("aria-selected", String(historyVisible));
  $("#copyIssuesBtn").hidden = historyVisible;
  $("#copyVerifyBtn").hidden = historyVisible;
}

function renderRunHistory(events = []) {
  const signature = JSON.stringify(events);
  if (signature === state.runHistorySignature) return;
  state.runHistorySignature = signature;
  $("#runHistory").innerHTML = events.length
    ? events.map((event) => {
        if (event.type === "patch") {
          const files = event.files || [];
          const timeline = event.timeline || [];
          return `
            <article class="run-history-item patch">
              <div class="run-history-main">
                <span class="run-history-badge">PATCH</span>
                <div>
                  <strong>${files.length} file(s) from Codex</strong>
                  <span>${escapeHtml(event.status || "staged")} | ${escapeHtml(event.time || "")}</span>
                </div>
              </div>
              <div class="run-history-files">
                ${files.map((file) => `<code>${escapeHtml(file.kind || "update")} ${escapeHtml(file.path || "")} | ${Number(file.hunks || 0)} hunk(s) | ${escapeHtml(file.status || "staged")}</code>`).join("")}
              </div>
              <ol class="patch-timeline">
                ${timeline.map((entry) => `<li><strong>${escapeHtml(String(entry.action || "updated").replaceAll("-", " "))}</strong><span>${escapeHtml(entry.path || entry.detail?.status || "")} ${escapeHtml(entry.time || "")}</span></li>`).join("")}
              </ol>
            </article>`;
        }
        const source = event.source === "codex_patch" ? "After Codex patch" : "Manual verify";
        return `
          <button class="run-history-item verify ${event.ok ? "passed" : "failed"}" type="button" data-verify-history-id="${escapeHtml(event.id || "")}">
            <span class="run-history-badge">${escapeHtml(event.status || "failed")}</span>
            <span class="run-history-copy">
              <strong>${escapeHtml(event.main_sketch || "Arduino sketch")}</strong>
              <span>${escapeHtml(source)} | ${escapeHtml(event.time || "")}</span>
            </span>
          </button>`;
      }).join("")
    : '<div class="run-history-empty">No verify attempts or Codex patches recorded yet.</div>';
  $$("[data-verify-history-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const event = events.find((item) => item.id === button.dataset.verifyHistoryId);
      if (!event?.result) return;
      renderVerifyOutput(event.result);
      setOutputView("verify");
    });
  });
}

function verifySource() {
  const patch = state.codexPatches.find((item) => (
    normalizedWindowsPath(item.workspace || "") === normalizedWindowsPath(state.selectedWorkspacePath)
    && (item.files || []).some((file) => (
      file.path === state.activeFilePath
      && ["applied-to-editor", "saved"].includes(file.review_status || "")
    ))
  ));
  return patch ? "codex_patch" : "manual";
}

async function refreshRunHistory() {
  const payload = await api("/api/run_history");
  renderRunHistory(payload.events || []);
  return payload;
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
  const conflicted = state.conflictedFilePaths.has(state.activeFilePath);
  $("#saveFileBtn").disabled = !state.activeFilePath || !state.editorDirty || state.editorSaving || conflicted;
  $("#saveAndVerifyBtn").disabled = !state.activeFilePath || !state.editorDirty || state.editorSaving || conflicted;
  $("#rollbackFileBtn").disabled = !state.activeFilePath || !state.lastCheckpoint || state.editorSaving || conflicted;
}

function setCheckpoint(checkpoint = null) {
  state.lastCheckpoint = checkpoint || null;
  const button = $("#rollbackFileBtn");
  const available = Boolean(state.activeFilePath && state.lastCheckpoint && !state.editorSaving && !state.conflictedFilePaths.has(state.activeFilePath));
  button.disabled = !available;
  button.title = available
    ? `Restore ${state.activeFilePath} to its state before Talos saved it at ${state.lastCheckpoint.created_at || "the last checkpoint"}`
    : "No safe Talos checkpoint is available for this file";
}

async function refreshCheckpoint() {
  if (!state.activeFilePath) {
    setCheckpoint();
    return;
  }
  try {
    const result = await api(`/api/arduino_checkpoint?path=${encodeURIComponent(state.activeFilePath)}`);
    setCheckpoint(result.checkpoint);
  } catch (_error) {
    setCheckpoint();
  }
}

function updateEditorAccess() {
  const editor = $("#sourceEditor");
  const reviewOpen = $(".source-editor").classList.contains("reviewing");
  const canEdit = Boolean(state.activeFilePath && state.localEditMode && !reviewOpen);
  editor.disabled = !canEdit;
  $(".source-editor").classList.toggle("viewing", Boolean(state.activeFilePath && !canEdit && !reviewOpen));
  const button = $("#editInTalosBtn");
  button.disabled = !state.activeFilePath || reviewOpen;
  button.classList.toggle("active", canEdit);
  button.textContent = canEdit ? "Stop Editing" : "Edit in Talos";
  button.title = canEdit
    ? "Return to review mode; local changes remain until saved or discarded"
    : "Enable local editing in Talos; Arduino IDE is not updated until Save File";
  button.setAttribute("aria-pressed", String(canEdit));
  $("#editorModeBadge").textContent = reviewOpen ? "Reviewing" : canEdit ? "Local edit" : "Review";
}

function setLocalEditMode(enabled) {
  if (!state.activeFilePath || $(".source-editor").classList.contains("reviewing")) return;
  state.localEditMode = Boolean(enabled);
  updateEditorAccess();
  if (state.localEditMode) {
    $("#editorStatus").textContent = "Local edit mode. Save File is required to update Arduino IDE.";
    $("#sourceEditor").focus();
  } else if (state.editorDirty) {
    $("#editorStatus").textContent = "Review mode. Local changes are retained; Save File updates Arduino IDE.";
  } else {
    $("#editorStatus").textContent = "Review mode. Arduino IDE owns the saved sketch.";
  }
}

function renderEditorLineNumbers() {
  const editor = $("#sourceEditor");
  const lineCount = Math.max(1, editor.value.split("\n").length);
  $("#editorLineNumbers").textContent = Array.from(
    { length: lineCount },
    (_value, index) => String(index + 1),
  ).join("\n");
}

function applyEditorFileResult(result, statusText = "") {
  state.activeFilePath = result.path;
  state.localEditMode = false;
  if (state.selectedWorkspacePath && result.path) {
    state.activeFileByWorkspace[normalizedWindowsPath(state.selectedWorkspacePath)] = result.path;
  }
  state.editorOriginalContent = result.content || "";
  setCheckpoint();
  state.editorFileMtimeNs = Number(result.mtime_ns || 0);
  state.codexPreviewPath = "";
  state.codexPreviewStreaming = false;
  state.codexPreviewCommitted = false;
  state.codexPreviewBaseContent = "";
  state.codexPreviewContent = "";
  state.codexReviewPatch = null;
  if (state.codexPreviewTimer) window.clearTimeout(state.codexPreviewTimer);
  state.codexPreviewTimer = null;
  $("#editorFileName").textContent = result.path;
  setCodexReviewMode(null);
  $("#sourceEditor").value = state.editorOriginalContent;
  renderEditorLineNumbers();
  updateEditorAccess();
  $("#editorStatus").textContent = statusText || `Review mode | ${Number(result.bytes || 0)} bytes | Arduino IDE owns the saved sketch.`;
  setEditorDirty(false);
}

function applyStoredCodexDraft() {
  const workspace = normalizedWindowsPath(state.selectedWorkspacePath);
  const draft = [...state.codexPatches].reverse().flatMap((patch) => (
    normalizedWindowsPath(patch.workspace || "") === workspace ? patch.files || [] : []
  )).find((file) => (
    file.path === state.activeFilePath
    && file.review_status === "applied-to-editor"
    && Object.hasOwn(file, "editor_content")
  ));
  if (!draft) return;
  $("#sourceEditor").value = String(draft.editor_content || "");
  renderEditorLineNumbers();
  setEditorDirty($("#sourceEditor").value !== state.editorOriginalContent);
  $("#editorStatus").textContent = "Applied Codex draft. Save File is required to update Arduino IDE.";
}

function resetEditor(message = "No file selected.") {
  state.activeFilePath = "";
  setCheckpoint();
  state.localEditMode = false;
  state.editorOriginalContent = "";
  state.editorFileMtimeNs = 0;
  state.editorLoading = false;
  state.editorSaving = false;
  state.editorDiskChecking = false;
  state.codexPreviewPath = "";
  state.codexPreviewStreaming = false;
  state.codexPreviewCommitted = false;
  state.codexPreviewBaseContent = "";
  state.codexPreviewContent = "";
  state.codexReviewPatch = null;
  if (state.codexPreviewTimer) window.clearTimeout(state.codexPreviewTimer);
  state.codexPreviewTimer = null;
  $("#editorFileName").textContent = "Select a source file";
  setCodexReviewMode(null);
  $("#sourceEditor").value = "";
  renderEditorLineNumbers();
  $("#sourceEditor").disabled = true;
  updateEditorAccess();
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
    applyEditorFileResult(result);
    applyStoredCodexDraft();
    renderActiveFileRow();
    refreshCodexReview(state.codexPatches);
    await refreshCheckpoint();
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
  if (!state.activeFilePath || !state.editorDirty || state.editorSaving) return false;
  if (state.conflictedFilePaths.has(state.activeFilePath)) {
    $("#editorStatus").textContent = "Save blocked: this file changed outside Talos and requires conflict resolution.";
    return false;
  }
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
    state.editorFileMtimeNs = Number(result.mtime_ns || 0);
    state.localEditMode = false;
    $("#editorStatus").textContent = `Saved ${result.path} (${Number(result.bytes || 0)} bytes).`;
    setEditorDirty(false);
    setCheckpoint(result.checkpoint);
    updateEditorAccess();
    void api("/api/codex_save_patch", {
      method: "POST",
      body: JSON.stringify({ path: state.activeFilePath }),
    }).then(() => refreshCodex()).catch((error) => {
      $("#codexStatus").textContent = `File saved, but change status could not be synced: ${error.message}`;
    });
    await refresh();
    return true;
  } catch (error) {
    $("#editorStatus").textContent = `Save failed: ${error.message}`;
    return false;
  } finally {
    state.editorSaving = false;
    setEditorDirty(state.editorDirty);
  }
}

async function saveAndVerifyWorkspace() {
  const saved = await saveWorkspaceFile();
  if (!saved) return;
  await verifyArduinoWorkspace(verifySource());
}

async function rollbackWorkspaceFile() {
  if (!state.activeFilePath || !state.lastCheckpoint || state.editorSaving) return;
  if (!window.confirm(`Restore ${state.activeFilePath} to the state before Talos's last save?`)) return;
  state.editorSaving = true;
  setEditorDirty(state.editorDirty);
  try {
    await api("/api/arduino_rollback", {
      method: "POST",
      body: JSON.stringify({ path: state.activeFilePath }),
    });
    const restored = await api(`/api/arduino_file?path=${encodeURIComponent(state.activeFilePath)}`);
    applyEditorFileResult(restored, "Restored the file from the Talos checkpoint. Arduino IDE now has the restored version.");
    setCheckpoint();
    await refresh();
  } catch (error) {
    $("#editorStatus").textContent = `Rollback failed: ${error.message}`;
  } finally {
    state.editorSaving = false;
    setEditorDirty(state.editorDirty);
  }
}

function selectEditorLine(lineNumber) {
  const editor = $("#sourceEditor");
  if (!lineNumber || editor.disabled) return;
  const lines = editor.value.split("\n");
  const lineIndex = Math.min(Math.max(lineNumber - 1, 0), lines.length - 1);
  let start = 0;
  for (let index = 0; index < lineIndex; index += 1) {
    start += lines[index].length + 1;
  }
  const end = Math.min(editor.value.length, start + lines[lineIndex].length);
  editor.focus();
  editor.setSelectionRange(start, end);
}

function lineFromGutterEvent(event) {
  const gutter = $("#editorLineNumbers");
  const style = window.getComputedStyle(gutter);
  const lineHeight = Number.parseFloat(style.lineHeight) || 20;
  const paddingTop = Number.parseFloat(style.paddingTop) || 0;
  const y = event.clientY - gutter.getBoundingClientRect().top + gutter.scrollTop - paddingTop;
  return Math.floor(Math.max(0, y) / lineHeight) + 1;
}

async function checkActiveFileOnDisk() {
  if (
    document.hidden
    || activeViewId() !== "workspace"
    || !state.activeFilePath
    || state.editorDirty
    || state.editorLoading
    || state.editorSaving
    || state.editorDiskChecking
    || state.codexPreviewStreaming
    || state.codexPreviewCommitted
  ) {
    return;
  }
  state.editorDiskChecking = true;
  try {
    const result = await api(`/api/arduino_file?path=${encodeURIComponent(state.activeFilePath)}`);
    const nextMtime = Number(result.mtime_ns || 0);
    if (nextMtime && state.editorFileMtimeNs && nextMtime === state.editorFileMtimeNs) return;
    if ((result.content || "") === state.editorOriginalContent) {
      state.editorFileMtimeNs = nextMtime;
      return;
    }
    applyEditorFileResult(result, `Reloaded from disk (${Number(result.bytes || 0)} bytes).`);
    renderActiveFileRow();
    await refreshCheckpoint();
  } catch (error) {
    resetEditor(`Active file reload failed: ${error.message}`);
  } finally {
    state.editorDiskChecking = false;
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

function applyUnifiedDiff(original, diffText) {
  const diffLines = String(diffText || "").split("\n");
  const originalLines = String(original || "").split("\n");
  const output = [];
  let oldIndex = 0;
  let sawHunk = false;

  for (let index = 0; index < diffLines.length; index += 1) {
    const header = diffLines[index].match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (!header) continue;
    sawHunk = true;
    const oldStart = Math.max(0, Number(header[1]) - 1);
    while (oldIndex < oldStart && oldIndex < originalLines.length) {
      output.push(originalLines[oldIndex]);
      oldIndex += 1;
    }
    index += 1;
    while (index < diffLines.length && !diffLines[index].startsWith("@@ ")) {
      const line = diffLines[index];
      if (line.startsWith(" ")) {
        output.push(line.slice(1));
        oldIndex += 1;
      } else if (line.startsWith("-")) {
        oldIndex += 1;
      } else if (line.startsWith("+")) {
        output.push(line.slice(1));
      } else if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("diff ")) {
        index -= 1;
        break;
      }
      index += 1;
    }
    index -= 1;
  }

  if (!sawHunk) return null;
  while (oldIndex < originalLines.length) {
    output.push(originalLines[oldIndex]);
    oldIndex += 1;
  }
  return output.join("\n");
}

function streamEditorContent(targetContent, path) {
  if (!path || path !== state.activeFilePath) return;
  if ($("#sourceEditor").value === targetContent) return;
  if (state.codexPreviewTimer) window.clearTimeout(state.codexPreviewTimer);
  state.codexPreviewPath = path;
  state.codexPreviewStreaming = true;
  state.codexPreviewCommitted = false;
  state.codexPreviewBaseContent = state.editorOriginalContent;
  state.codexPreviewContent = String(targetContent || "");
  $("#editorStatus").textContent = `Preparing Codex change review for ${path}...`;
  setCodexReviewMode({
    streaming: true,
    workspace: state.selectedWorkspacePath,
    files: [{ path, content: state.codexPreviewContent, review_status: "reviewing" }],
  });
  state.codexPreviewStreaming = false;
  state.codexPreviewCommitted = true;
}

function previewPendingCodexPatch(pendingPatch = {}) {
  const revision = Number(pendingPatch.revision || 0);
  if (!revision || revision === state.codexPreviewRevision || !state.activeFilePath || state.editorDirty) return;
  const patchWorkspace = normalizedWindowsPath(pendingPatch.workspace || "");
  const selectedWorkspace = normalizedWindowsPath(state.selectedWorkspacePath);
  if (patchWorkspace && selectedWorkspace && patchWorkspace !== selectedWorkspace) return;
  const activePath = normalizedWindowsPath(state.activeFilePath);
  const change = (pendingPatch.files || []).find((file) => normalizedWindowsPath(file.path) === activePath);
  if (!change?.diff) return;
  const targetContent = applyUnifiedDiff(state.editorOriginalContent, change.diff);
  if (targetContent === null || targetContent === state.editorOriginalContent) return;
  state.codexPreviewRevision = revision;
  streamEditorContent(targetContent, state.activeFilePath);
}

function codexDiffRows(editorContent = "", proposedContent = "") {
  const before = String(editorContent).split("\n");
  const after = String(proposedContent).split("\n");
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < before.length - prefix
    && suffix < after.length - prefix
    && before[before.length - suffix - 1] === after[after.length - suffix - 1]
  ) suffix += 1;
  const rows = [
    { kind: "meta", text: "--- Talos editor" },
    { kind: "meta", text: "+++ Codex proposed change" },
    { kind: "meta", text: `@@ -1,${before.length} +1,${after.length} @@` },
  ];
  let oldLine = 1;
  let newLine = 1;
  before.slice(0, prefix).forEach((text) => {
    rows.push({ kind: "context", text, oldLine: oldLine++, newLine: newLine++ });
  });
  before.slice(prefix, before.length - suffix).forEach((text) => {
    rows.push({ kind: "remove", text, oldLine: oldLine++ });
  });
  after.slice(prefix, after.length - suffix).forEach((text) => {
    rows.push({ kind: "add", text, newLine: newLine++ });
  });
  before.slice(before.length - suffix).forEach((text) => {
    rows.push({ kind: "context", text, oldLine: oldLine++, newLine: newLine++ });
  });
  return rows;
}

function selectDiffLine(row) {
  $$(".codex-diff-line.selected").forEach((item) => item.classList.remove("selected"));
  row.classList.add("selected");
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(row.querySelector(".codex-diff-content") || row);
  selection.removeAllRanges();
  selection.addRange(range);
}

function contentWithAppliedHunks(originalContent = "", hunks = []) {
  const trailingNewline = String(originalContent).endsWith("\n");
  const originalLines = String(originalContent).split("\n");
  if (trailingNewline) originalLines.pop();
  const output = [];
  let cursor = 0;
  [...hunks].sort((left, right) => Number(left.old_start || 0) - Number(right.old_start || 0)).forEach((hunk) => {
    const start = Number(hunk.old_start || 0);
    const end = Number(hunk.old_end || start);
    output.push(...originalLines.slice(cursor, start));
    output.push(...(
      hunk.review_status === "applied-to-editor"
        ? (hunk.new_lines || [])
        : originalLines.slice(start, end)
    ));
    cursor = end;
  });
  output.push(...originalLines.slice(cursor));
  return `${output.join("\n")}${trailingNewline ? "\n" : ""}`;
}

function renderCodexDiff(editorContent = "", proposedContent = "", file = {}) {
  const preview = $("#codexDiffPreview");
  const hunks = file.hunks || [];
  if (hunks.length) {
    preview.innerHTML = hunks.map((hunk, index) => {
      const status = hunk.review_status || "staged";
      const reviewable = ["staged", "reviewing"].includes(status);
      const rows = [
        ...(hunk.old_lines || []).map((text, line) => ({ kind: "remove", text, line: Number(hunk.old_start || 0) + line + 1 })),
        ...(hunk.new_lines || []).map((text, line) => ({ kind: "add", text, line: Number(hunk.new_start || 0) + line + 1 })),
      ];
      return `
        <section class="codex-diff-hunk ${escapeHtml(status)}">
          <header>
            <span>Hunk ${index + 1} | ${escapeHtml(status)}</span>
            <div>
              <button class="icon-button hunk-action" type="button" data-hunk-action="reject" data-hunk-id="${escapeHtml(hunk.id || "")}" ${reviewable ? "" : "disabled"}>Reject hunk</button>
              <button class="button primary hunk-action" type="button" data-hunk-action="apply" data-hunk-id="${escapeHtml(hunk.id || "")}" ${reviewable ? "" : "disabled"}>Apply hunk</button>
            </div>
          </header>
          ${rows.map((row) => `<button class="codex-diff-line ${row.kind}" type="button"><span class="codex-diff-number">${row.line}</span><span class="codex-diff-content">${escapeHtml(`${row.kind === "add" ? "+" : "-"}${row.text || ""}`)}</span></button>`).join("")}
        </section>`;
    }).join("");
    $$(".hunk-action").forEach((button) => button.addEventListener("click", () => {
      reviewCodexHunk(button.dataset.hunkAction, button.dataset.hunkId);
    }));
    $$(".codex-diff-line").forEach((row) => row.addEventListener("click", () => selectDiffLine(row)));
    return;
  }
  preview.innerHTML = codexDiffRows(editorContent, proposedContent).map((row) => {
    const lineNumber = row.newLine || row.oldLine || "";
    const prefix = row.kind === "add" ? "+" : row.kind === "remove" ? "-" : " ";
    return `<button class="codex-diff-line ${row.kind}" type="button"><span class="codex-diff-number">${lineNumber}</span><span class="codex-diff-content">${escapeHtml(`${prefix}${row.text || ""}`)}</span></button>`;
  }).join("");
  $$(".codex-diff-line").forEach((row) => row.addEventListener("click", () => selectDiffLine(row)));
}

function setCodexConflictMode(patch = null, file = null) {
  const visible = Boolean(patch && file && file.review_status === "conflict");
  const view = $("#codexConflictView");
  view.hidden = !visible;
  $(".source-editor").classList.toggle("conflicting", visible);
  if (!visible) return;
  $("#codexConflictLabel").textContent = `Resolve conflict: ${file.path}`;
  $("#codexConflictTime").textContent = file.conflict_detected_at || "";
  $("#codexConflictBase").textContent = String(file.base_content || "");
  $("#codexConflictCurrent").textContent = String(file.conflict_current_content || "");
  $("#codexConflictProposed").textContent = String(file.content || "");
}

async function keepExternalConflict() {
  const patch = state.codexPatches.find((item) => (
    normalizedWindowsPath(item.workspace || "") === normalizedWindowsPath(state.selectedWorkspacePath)
    && (item.files || []).some((file) => file.path === state.activeFilePath && file.review_status === "conflict")
  ));
  if (!patch?.id || !state.activeFilePath) return;
  try {
    await api("/api/codex_keep_external", {
      method: "POST",
      body: JSON.stringify({ id: patch.id, path: state.activeFilePath }),
    });
    const current = await api(`/api/arduino_file?path=${encodeURIComponent(state.activeFilePath)}`);
    applyEditorFileResult(current, "Kept the current Arduino file. The conflicting Codex change was rejected.");
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = `Could not keep the Arduino version: ${error.message}`;
  }
}

function setCodexReviewMode(patch = null) {
  setCodexConflictMode();
  state.codexReviewPatch = patch;
  const activeFile = state.activeFilePath;
  const file = patch && (patch.files || []).find((item) => item.path === activeFile);
  const proposedContent = file && Object.hasOwn(file, "content") ? String(file.content || "") : null;
  const differsFromEditor = proposedContent !== null && $("#sourceEditor").value !== proposedContent;
  const reviewing = Boolean(
    patch
    && file
    && ["staged", "reviewing"].includes(file.review_status || "staged")
    && file.kind !== "delete"
    && differsFromEditor
  );
  $(".source-editor").classList.toggle("reviewing", reviewing);
  $("#codexReviewBar").hidden = !reviewing;
  $("#codexDiffPreview").hidden = !reviewing;
  if (!reviewing) {
    $("#codexDiffPreview").innerHTML = "";
    $("#applyCodexTurnBtn").hidden = true;
    $("#rejectCodexTurnBtn").hidden = true;
    $("#rejectCodexPatchBtn").hidden = false;
    $("#applyCodexPatchBtn").textContent = "Apply To Editor";
    updateEditorAccess();
    return;
  }
  const reviewable = ["staged", "reviewing"].includes(file.review_status || "staged");
  const streaming = Boolean(patch.streaming);
  $("#codexReviewLabel").textContent = streaming
    ? `Streaming Codex change: ${file.path}`
    : `Codex change review: ${file.path}`;
  $("#applyCodexPatchBtn").textContent = reviewable ? "Apply To Editor" : "Restore Proposed Change";
  $("#applyCodexPatchBtn").disabled = false;
  $("#verifyCodexPatchBtn").disabled = streaming;
  $("#applyCodexTurnBtn").hidden = false;
  $("#rejectCodexTurnBtn").hidden = false;
  $("#applyCodexTurnBtn").disabled = !reviewable || streaming;
  $("#rejectCodexTurnBtn").disabled = !reviewable || streaming;
  $("#rejectCodexPatchBtn").hidden = !reviewable || streaming;
  renderCodexDiff($("#sourceEditor").value, proposedContent, file);
  updateEditorAccess();
  $("#saveFileBtn").disabled = true;
}

function refreshCodexReview(patches = []) {
  state.codexPatches = patches;
  const workspacePatches = patches.filter((patch) => (
    normalizedWindowsPath(patch.workspace || "") === normalizedWindowsPath(state.selectedWorkspacePath)
  ));
  renderArduinoFilesAfterCodexPatch();
  state.conflictedFilePaths = new Set(workspacePatches.flatMap((patch) => (
    (patch.files || [])
      .filter((file) => file.review_status === "conflict")
      .map((file) => file.path)
  )));
  setEditorDirty(state.editorDirty);
  const conflict = workspacePatches.find((patch) => (
    (patch.files || []).some((file) => file.path === state.activeFilePath && file.review_status === "conflict")
  ));
  if (conflict) {
    setCodexReviewMode(null);
    const conflictFile = (conflict.files || []).find((file) => file.path === state.activeFilePath);
    setCodexConflictMode(conflict, conflictFile);
    $("#editorStatus").textContent = "External source change detected. Resolve the Codex conflict before applying or saving this draft.";
    $("#codexStatus").textContent = "Codex change conflict detected.";
    return;
  }
  const pending = workspacePatches.find((patch) => (
    (patch.files || []).some((file) => (
      file.path === state.activeFilePath
      && ["staged", "reviewing"].includes(file.review_status || "staged")
      && file.kind !== "delete"
      && Object.hasOwn(file, "content")
      && $("#sourceEditor").value !== String(file.content || "")
    ))
  ));
  setCodexReviewMode(pending || null);
  const reviewingFile = pending && (pending.files || []).find((file) => file.path === state.activeFilePath);
  if (pending?.id && reviewingFile?.review_status === "staged") {
    void api("/api/codex_review_patch", {
      method: "POST",
      body: JSON.stringify({ id: pending.id, path: state.activeFilePath }),
    }).then(() => refreshCodex()).catch((error) => {
      $("#codexStatus").textContent = `Change review could not be recorded: ${error.message}`;
    });
  }
}

async function applyCodexPatch(patch = state.codexReviewPatch) {
  if (!patch) return;
  const proposedFile = (patch.files || []).find((file) => file.path === state.activeFilePath);
  if (!proposedFile || !Object.hasOwn(proposedFile, "content")) return;
  const acceptedHunks = (proposedFile.hunks || []).map((hunk) => ({
    ...hunk,
    review_status: ["staged", "reviewing"].includes(hunk.review_status || "staged")
      ? "applied-to-editor"
      : hunk.review_status,
  }));
  const content = acceptedHunks.length
    ? contentWithAppliedHunks(state.editorOriginalContent, acceptedHunks)
    : String(proposedFile.content || "");
  setCodexReviewMode(null);
  $("#sourceEditor").value = content;
  renderEditorLineNumbers();
  state.codexPreviewBaseContent = "";
  state.codexPreviewContent = "";
  state.codexPreviewCommitted = false;
  setEditorDirty(content !== state.editorOriginalContent);
  state.localEditMode = false;
  updateEditorAccess();
  $("#editorStatus").textContent = "Codex change applied to Talos editor. Save File to update Arduino IDE.";
  $("#codexStatus").textContent = "Codex change applied to editor; waiting for Save File.";

  const reviewable = patch.id && ["staged", "reviewing"].includes(proposedFile.review_status || "staged");
  if (!reviewable) return;
  state.codexApplyingPatchId = patch.id;
  void api("/api/codex_apply_patch", {
    method: "POST",
    body: JSON.stringify({ id: patch.id, path: state.activeFilePath }),
  }).then(() => refreshCodex()).catch((error) => {
    $("#codexStatus").textContent = `Editor updated, but patch state could not be synced: ${error.message}`;
  }).finally(() => {
    state.codexApplyingPatchId = "";
  });
}

async function verifyCodexPatch() {
  const patch = state.codexReviewPatch;
  if (!patch?.id || state.codexPatchVerifyRunning) return;
  state.codexPatchVerifyRunning = true;
  $("#verifyCodexPatchBtn").disabled = true;
  setOutputView("verify");
  renderVerifyOutput(null, "Compiling the staged Codex change in an isolated Arduino sandbox...");
  try {
    const result = await api("/api/codex_verify_patch", {
      method: "POST",
      body: JSON.stringify({ id: patch.id }),
    });
    renderVerifyOutput(result);
    $("#codexStatus").textContent = result.ok
      ? "Staged Codex change compiled successfully. Review and Save File when ready."
      : "Staged Codex change did not compile. Arduino IDE files were not changed.";
    await refreshRunHistory();
  } catch (error) {
    renderVerifyOutput(null, `Staged Codex verify failed: ${error.message}`);
    $("#codexStatus").textContent = `Could not verify staged Codex change: ${error.message}`;
  } finally {
    state.codexPatchVerifyRunning = false;
    if (state.codexReviewPatch?.id === patch.id) $("#verifyCodexPatchBtn").disabled = false;
  }
}

async function reviewCodexHunk(action, hunkId) {
  const patch = state.codexReviewPatch;
  const file = patch && (patch.files || []).find((item) => item.path === state.activeFilePath);
  if (!patch?.id || !file || !hunkId || !["apply", "reject"].includes(action)) return;
  const endpoint = action === "apply" ? "/api/codex_apply_hunk" : "/api/codex_reject_hunk";
  try {
    const result = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({ id: patch.id, path: state.activeFilePath, hunk_id: hunkId }),
    });
    const updatedPatch = result.patch || patch;
    const updatedFile = result.file || file;
    $("#sourceEditor").value = contentWithAppliedHunks(state.editorOriginalContent, updatedFile.hunks || []);
    renderEditorLineNumbers();
    setEditorDirty($("#sourceEditor").value !== state.editorOriginalContent);
    state.localEditMode = false;
    setCodexReviewMode(updatedPatch);
    $("#editorStatus").textContent = action === "apply"
      ? "Codex hunk applied to Talos editor. Review remaining hunks or Save File when complete."
      : "Codex hunk rejected. The Arduino sketch was not changed.";
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = `Could not ${action} Codex hunk: ${error.message}`;
  }
}

async function resolveCodexTurn(action) {
  const patch = state.codexReviewPatch;
  if (!patch?.id || !["apply", "reject"].includes(action)) return;
  const endpoint = action === "apply" ? "/api/codex_apply_all" : "/api/codex_reject_all";
  try {
    const result = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({ id: patch.id }),
    });
    const updatedPatch = result.patch || patch;
    const updatedFile = (updatedPatch.files || []).find((file) => file.path === state.activeFilePath);
    if (updatedFile?.editor_content !== undefined) {
      $("#sourceEditor").value = String(updatedFile.editor_content || "");
    } else if (updatedFile?.review_status === "rejected") {
      $("#sourceEditor").value = state.editorOriginalContent;
    }
    renderEditorLineNumbers();
    setEditorDirty($("#sourceEditor").value !== state.editorOriginalContent);
    state.localEditMode = false;
    setCodexReviewMode(updatedPatch);
    $("#editorStatus").textContent = action === "apply"
      ? `Applied ${Number(result.changed || 0)} Codex hunk(s) to Talos drafts. Save File writes the active draft to Arduino IDE.`
      : `Rejected ${Number(result.changed || 0)} pending Codex hunk(s). The Arduino sketch was not changed.`;
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = `Could not ${action} this Codex turn: ${error.message}`;
  }
}

async function rejectCodexPatch() {
  const patch = state.codexReviewPatch;
  if (!patch?.id) return;
  try {
    await api("/api/codex_reject_patch", {
      method: "POST",
      body: JSON.stringify({ id: patch.id, path: state.activeFilePath }),
    });
    if (state.codexPreviewBaseContent) {
      $("#sourceEditor").value = state.codexPreviewBaseContent;
      renderEditorLineNumbers();
    }
    state.codexPreviewBaseContent = "";
    state.codexPreviewContent = "";
    state.codexPreviewCommitted = false;
    setCodexReviewMode(null);
    $("#editorStatus").textContent = "Codex change rejected. The Arduino sketch was not changed.";
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = `Could not reject Codex patch: ${error.message}`;
  }
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
  const nextWorkspacePath = normalizedWindowsPath(arduino.path);
  const currentWorkspacePath = normalizedWindowsPath(state.selectedWorkspacePath);
  const transientWorkspaceLoss = (
    !nextWorkspacePath
    && currentWorkspacePath
    && state.activeFilePath
    && (state.codexBusy || state.codexPreviewStreaming || state.codexPreviewCommitted)
  );
  const workspaceChanged = !transientWorkspaceLoss && nextWorkspacePath !== currentWorkspacePath;
  if (workspaceChanged) {
    if (state.selectedWorkspacePath && state.activeFilePath) {
      state.activeFileByWorkspace[currentWorkspacePath] = state.activeFilePath;
    }
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
    <tr class="${state.codexChangedFiles.has(String(file.path).toLowerCase()) ? "codex-changed" : ""}" data-path="${escapeHtml(file.path)}" tabindex="0">
      <td>
        <span class="file-name">${escapeHtml(file.path)}</span>
        <span class="file-meta">${Number(file.lines || 0)} lines | ${Number(file.bytes || 0)} bytes</span>
        ${state.codexChangedFiles.has(String(file.path).toLowerCase()) ? '<span class="file-change-badge">Changed by Codex</span>' : ""}
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
  const rememberedFile = state.activeFileByWorkspace[normalizedWindowsPath(state.selectedWorkspacePath)];
  const rememberedExists = rememberedFile && (arduino.files || []).some((file) => file.path === rememberedFile);
  if (!state.activeFilePath && rememberedExists && !state.editorLoading) {
    window.setTimeout(() => openWorkspaceFile(rememberedFile), 0);
  }
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
  const plan = account.planType ? ` | ${account.planType}` : "";
  const email = account.email ? `${account.email}${plan}` : `${account.type || "ChatGPT"}${plan}`;
  return payload.connected ? email : "Connecting...";
}

function codexChangeStatusLabel(status = "staged") {
  return {
    staged: "Change staged",
    reviewing: "Change under review",
    "applied-to-editor": "Applied to editor",
    saved: "Saved to Arduino workspace",
    rejected: "Change rejected",
    conflict: "Change conflict",
  }[status] || status;
}

function renderCodex(payload = {}) {
  state.codexBusy = Boolean(payload.busy);
  previewPendingCodexPatch(payload.pending_patch || {});
  $("#codexAccount").textContent = codexAccountLabel(payload);
  $("#sendCodexBtn").disabled = !payload.ok || state.codexBusy;
  $("#sendCodexBtn").hidden = state.codexBusy;
  $("#cancelCodexBtn").hidden = !state.codexBusy;
  $("#cancelCodexBtn").disabled = !state.codexBusy;
  $("#codexInput").disabled = !payload.ok;
  $("#newCodexThreadBtn").disabled = state.codexBusy;
  $("#codexStatus").textContent = payload.error
    || (state.codexBusy ? "Codex is working..." : payload.thread_id ? "Thread ready" : "Ready for a new thread");
  const messages = payload.messages || [];
  const patches = payload.patches || [];
  const conversations = payload.conversations || [];
  renderCodexHistory(conversations);
  const signature = JSON.stringify([messages, patches]);
  if (signature !== state.codexMessagesSignature) {
    state.codexMessagesSignature = signature;
    const messageHtml = messages.map((message) => `
          <article class="codex-message ${message.role === "user" ? "user" : "assistant"}">
            <span class="codex-message-role">${message.role === "user" ? "You" : "Codex"}</span>
            <div class="codex-message-body">${escapeHtml(message.text || "")}</div>
          </article>
        `).join("");
    const patchHtml = patches.slice(-5).map((patch) => `
      <section class="codex-patch">
        <div class="codex-patch-head"><span>${escapeHtml(codexChangeStatusLabel(patch.review_status || "staged"))}</span><span>${escapeHtml(patch.time || "")}</span></div>
        <div class="codex-patch-list">
          ${(patch.files || []).map((file) => `
            <div class="codex-patch-file">
              <span class="codex-patch-kind">${escapeHtml(file.review_status || "staged")}</span>
              <code title="${escapeHtml(file.path || "")}">${escapeHtml(file.path || "")}</code>
            </div>
          `).join("")}
        </div>
      </section>
    `).join("");
    $("#codexMessages").innerHTML = (messageHtml || patchHtml)
      ? `${messageHtml}${patchHtml}`
      : `
        <div class="codex-empty">
          <span class="codex-empty-mark">C</span>
          <strong>Work with your Arduino sketch</strong>
          <p>Codex receives the selected workspace, active file, and latest verify result.</p>
          <div class="codex-suggestions">
            <button type="button" data-codex-prompt="Review this sketch and identify the most important issues.">Review this sketch</button>
            <button type="button" data-codex-prompt="Explain the active file and its control flow.">Explain the active file</button>
            <button type="button" data-codex-prompt="Optimize this sketch while preserving its current behavior.">Optimize the code</button>
          </div>
        </div>`;
    bindCodexSuggestions();
    $("#codexMessages").scrollTop = $("#codexMessages").scrollHeight;
  }
  const activity = payload.activity || [];
  const visibleActivity = state.codexBusy ? activity.slice(-4) : [];
  $("#codexActivity").hidden = !visibleActivity.length;
  $("#codexActivity").textContent = visibleActivity.join("\n");
  const nextRevision = Number(payload.patch_revision || 0);
  if (nextRevision !== state.codexPatchRevision) {
    const latestPatch = patches.at(-1) || {};
    state.codexPatchRevision = nextRevision;
    state.codexChangedFiles = new Set(
      (latestPatch.files || []).map((file) => String(file.path || "").toLowerCase()),
    );
    renderArduinoFilesAfterCodexPatch();
  }
  refreshCodexReview(patches);
  const nextEventRevision = Number(payload.patch_event_revision || 0);
  if (state.codexPatchEventRevision === null) {
    state.codexPatchEventRevision = nextEventRevision;
  } else if (nextEventRevision !== state.codexPatchEventRevision) {
    state.codexPatchEventRevision = nextEventRevision;
    $("#codexStatus").textContent = "Codex patch is ready for review.";
  }
}

function renderCodexHistory(conversations = []) {
  state.codexConversations = conversations;
  const signature = JSON.stringify([conversations, state.codexHistoryExpanded]);
  if (signature === state.codexConversationSignature) return;
  state.codexConversationSignature = signature;
  $("#codexHistoryCount").textContent = conversations.length ? String(conversations.length) : "";
  const visible = state.codexHistoryExpanded ? conversations : conversations.slice(0, 3);
  $("#codexHistoryList").innerHTML = visible.length
    ? `${visible.map((conversation) => `
        <button class="codex-history-item ${conversation.active ? "active" : ""}" type="button" data-conversation-id="${escapeHtml(conversation.id || "")}">
          <span class="codex-history-title">${escapeHtml(conversation.title || "New conversation")}</span>
          <span class="codex-history-time">${escapeHtml(relativeTimeLabel(conversation.updated_at || ""))}</span>
        </button>
      `).join("")}${conversations.length > 3 ? `
        <button id="codexHistoryMoreBtn" class="codex-history-more" type="button">
          ${state.codexHistoryExpanded ? "Show recent" : `View all (${conversations.length})`}
        </button>` : ""}`
    : '<div class="codex-history-empty">No saved conversations yet.</div>';
  $$("[data-conversation-id]").forEach((button) => {
    button.addEventListener("click", () => selectCodexConversation(button.dataset.conversationId || ""));
  });
  $("#codexHistoryMoreBtn")?.addEventListener("click", () => {
    state.codexHistoryExpanded = !state.codexHistoryExpanded;
    state.codexConversationSignature = "";
    renderCodexHistory(state.codexConversations);
  });
}

function relativeTimeLabel(value = "") {
  const timestamp = typeof value === "number"
    ? value * 1000
    : Date.parse(String(value).replace(" ", "T"));
  if (!Number.isFinite(timestamp)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

function bindCodexSuggestions() {
  $$("[data-codex-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#codexInput").value = button.dataset.codexPrompt || "";
      $("#codexInput").focus();
    });
  });
}

function renderArduinoFilesAfterCodexPatch() {
  $$("#arduinoFiles tr").forEach((row) => {
    const changed = state.codexChangedFiles.has(String(row.dataset.path || "").toLowerCase());
    row.classList.toggle("codex-changed", changed);
  });
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
  showCodexTasks(false);
  try {
    await api("/api/codex_message", {
      method: "POST",
      body: JSON.stringify({
        message,
        active_file: state.activeFilePath
          ? { path: state.activeFilePath, content: $("#sourceEditor").value }
          : {},
        verify_context: state.lastIssueText || state.lastVerifyText,
        allow_edits: $("#codexAllowEdits").checked,
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
    showCodexTasks(false);
    await refreshCodex();
    $("#codexInput").focus();
  } catch (error) {
    $("#codexStatus").textContent = error.message;
  }
}

function showCodexTasks(open = true) {
  const history = $("#codexHistory");
  state.codexTasksVisible = Boolean(open);
  history.hidden = !state.codexTasksVisible;
  $("#codexPanel").classList.toggle("history-mode", state.codexTasksVisible);
  $("#codexBackBtn").hidden = state.codexTasksVisible;
  if (state.codexTasksVisible) {
    state.codexHistoryExpanded = false;
    state.codexConversationSignature = "";
    renderCodexHistory(state.codexConversations);
  }
}

async function selectCodexConversation(conversationId) {
  if (!conversationId || state.codexBusy) return;
  try {
    await api("/api/codex_conversation", {
      method: "POST",
      body: JSON.stringify({ id: conversationId }),
    });
    state.codexMessagesSignature = "";
    showCodexTasks(false);
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = error.message;
  }
}

async function cancelCodexTurn() {
  $("#cancelCodexBtn").disabled = true;
  $("#codexStatus").textContent = "Cancelling Codex turn...";
  try {
    await api("/api/codex_cancel", { method: "POST", body: "{}" });
    await refreshCodex();
  } catch (error) {
    $("#codexStatus").textContent = error.message;
    $("#cancelCodexBtn").disabled = false;
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
        ${project.valid ? `<div class="project-source-meta">${Number(project.source_count || 0)} source tab(s)</div>` : ""}
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
  hydrateAppIdentity(payload.app || {});
  const projects = payload.arduino_projects || [];
  const arduino = payload.arduino || {};
  const selectedPath = normalizedWindowsPath(arduino.path);
  const selectedProject = projects.find((project) => (
    normalizedWindowsPath(project.path) === selectedPath && project.fqbn === arduino.fqbn
  ))
    || projects.find((project) => normalizedWindowsPath(project.path) === selectedPath)
    || {};
  const versionText = $("#modeLine").dataset.version ? ` | ${$("#modeLine").dataset.version}` : "";
  $("#modeLine").textContent = `${payload.role}${versionText} | ${payload.root}`;
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

async function verifyArduinoWorkspace(source = verifySource()) {
  setOutputView("verify");
  renderVerifyOutput(null, "Copying sketch folder to sandbox and running arduino-cli compile...");
  state.arduinoVerifyRunning = true;
  $("#verifyArduinoBtn").disabled = true;
  try {
    const result = await api("/api/arduino_verify", {
      method: "POST",
      body: JSON.stringify({
        path: $("#arduinoPathInput").value,
        fqbn: state.arduinoFqbnFull || $("#arduinoFqbnInput").value,
        source,
      }),
    });
    renderVerifyOutput(result);
    state.arduinoDirty = false;
    await refreshRunHistory();
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
  restorePaneWidths();
  applyRailPinned(railPinned());
  applyCodexPanel(codexPanelOpen());
  showCodexTasks(true);
  bindExplorerSplitter("#explorerSplitter");
  $$(".nav").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#pinRailBtn").addEventListener("click", () => applyRailPinned(!railPinned()));
  $("#refreshBtn").addEventListener("click", refresh);
  $("#refreshWorkspaceBtn").addEventListener("click", refresh);
  $("#saveArduinoBtn").addEventListener("click", async () => {
    await saveArduinoWorkspace();
    await refreshAfterWorkspaceMutation();
  });
  $("#verifyArduinoBtn").addEventListener("click", () => verifyArduinoWorkspace());
  $("#verifyOutputTab").addEventListener("click", () => setOutputView("verify"));
  $("#runHistoryTab").addEventListener("click", async () => {
    setOutputView("history");
    await refreshRunHistory();
  });
  $("#saveSettingsBtn").addEventListener("click", saveSettings);
  $("#copyFilesBtn").addEventListener("click", () => copyText(fileListText(), "#arduinoMeta"));
  $("#copyIssuesBtn").addEventListener("click", () => copyText(state.lastIssueText));
  $("#copyVerifyBtn").addEventListener("click", () => copyText(state.lastVerifyText));
  $("#editInTalosBtn").addEventListener("click", () => setLocalEditMode(!state.localEditMode));
  $("#saveFileBtn").addEventListener("click", saveWorkspaceFile);
  $("#saveAndVerifyBtn").addEventListener("click", saveAndVerifyWorkspace);
  $("#rollbackFileBtn").addEventListener("click", rollbackWorkspaceFile);
  $("#applyCodexPatchBtn").addEventListener("click", () => applyCodexPatch());
  $("#verifyCodexPatchBtn").addEventListener("click", verifyCodexPatch);
  $("#rejectCodexPatchBtn").addEventListener("click", rejectCodexPatch);
  $("#applyCodexTurnBtn").addEventListener("click", () => resolveCodexTurn("apply"));
  $("#rejectCodexTurnBtn").addEventListener("click", () => resolveCodexTurn("reject"));
  $("#keepExternalConflictBtn").addEventListener("click", keepExternalConflict);
  $("#toggleCodexBtn").addEventListener("click", () => applyCodexPanel(!codexPanelOpen()));
  $("#closeCodexBtn").addEventListener("click", () => applyCodexPanel(false));
  $("#newCodexThreadBtn").addEventListener("click", newCodexThread);
  $("#codexBackBtn").addEventListener("click", () => showCodexTasks(true));
  $("#cancelCodexBtn").addEventListener("click", cancelCodexTurn);
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
    renderEditorLineNumbers();
    setEditorDirty($("#sourceEditor").value !== state.editorOriginalContent);
    $("#editorStatus").textContent = state.editorDirty ? "Unsaved changes." : "No changes.";
  });
  $("#sourceEditor").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveWorkspaceFile();
    }
  });
  $("#sourceEditor").addEventListener("scroll", () => {
    $("#editorLineNumbers").scrollTop = $("#sourceEditor").scrollTop;
  });
  $("#editorLineNumbers").addEventListener("mousedown", (event) => {
    event.preventDefault();
    selectEditorLine(lineFromGutterEvent(event));
  });
  $("#editorLineNumbers").addEventListener("mousemove", (event) => {
    if (event.buttons === 1) {
      event.preventDefault();
      selectEditorLine(lineFromGutterEvent(event));
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
bindCodexSuggestions();
renderEditorLineNumbers();
const requestedView = new URLSearchParams(window.location.search).get("view");
if (["dashboard", "workspace", "logs", "settings"].includes(requestedView)) {
  setView(requestedView);
}
refresh();
setInterval(maybeRefresh, REFRESH_TICK_MS);
setInterval(checkActiveFileOnDisk, ACTIVE_FILE_POLL_MS);
refreshCodex();
