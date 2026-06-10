const state = {
  arduinoDirty: false,
  arduinoVerifyRunning: false,
  refreshPromise: null,
  arduinoFqbnFull: "",
  arduinoBoardName: "",
};

const THEMES = ["light", "dark", "neutral"];
const THEME_KEY = "talos-theme";

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
}

function currentTheme() {
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

function setBoardField(fqbn = "", boardName = "") {
  state.arduinoFqbnFull = fqbn || "";
  state.arduinoBoardName = boardName || state.arduinoBoardName || "";
  $("#arduinoFqbnInput").value = compactBoardLabel(state.arduinoFqbnFull, state.arduinoBoardName);
  $("#boardInfoPanel").textContent = boardInfoText(state.arduinoFqbnFull, state.arduinoBoardName);
  $("#boardInfoBtn").disabled = !state.arduinoFqbnFull;
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
  if (!state.arduinoDirty || force) {
    $("#arduinoPathInput").value = arduino.path || "";
    setBoardField(arduino.fqbn || "", ide.board_name || state.arduinoBoardName || "");
  }
  $("#arduinoStatus").textContent = arduino.message || "No Arduino sketch folder configured.";
  $("#arduinoMeta").textContent = arduino.valid
    ? `${arduino.files.length} file(s) | main sketch: ${arduino.main_sketch}`
    : "Set a sketch folder that contains at least one .ino file.";
  $("#verifyArduinoBtn").disabled = state.arduinoVerifyRunning || !arduino.configured;
  $("#arduinoFiles").innerHTML = (arduino.files || []).map((file) => `
    <tr>
      <td>${escapeHtml(file.path)}</td>
      <td>${Number(file.lines || 0)}</td>
      <td>${Number(file.bytes || 0)}</td>
    </tr>
  `).join("");
}

function renderArduinoProjects(projects = []) {
  if (!projects.length) {
    $("#arduinoProjects").innerHTML = `<div class="project-row"><div><div class="project-title">No open Arduino sketches detected</div><div class="project-path">Open one or more .ino sketches in Arduino IDE, then refresh.</div></div></div>`;
    return;
  }
  $("#arduinoProjects").innerHTML = projects.map((project, index) => `
    <div class="project-row">
      <div>
        <div class="project-title">${escapeHtml(project.sketch || "Arduino sketch")} ${project.valid ? "" : "(folder not found)"}</div>
      </div>
      <button class="button ghost select-project" data-index="${index}" ${project.valid ? "" : "disabled"}>Select</button>
    </div>
  `).join("");
  $$(".select-project").forEach((button) => {
    button.addEventListener("click", async () => {
      const project = projects[Number(button.dataset.index)];
      if (!project?.path) return;
      $("#arduinoPathInput").value = project.path;
      if (project.fqbn) setBoardField(project.fqbn, project.board_name || "");
      state.arduinoDirty = true;
      await saveArduinoWorkspace();
    });
  });
}

function render(payload) {
  $("#modeLine").textContent = `${payload.role} | ${payload.root}`;
  $("#toolList").textContent = (payload.tools || []).join("\n");
  renderStats(payload);
  renderArduino(payload.arduino, false, payload.arduino_ide || {});
  renderArduinoProjects(payload.arduino_projects || []);
  $("#logText").textContent = (payload.events || []).join("\n");
}

async function refresh() {
  if (state.refreshPromise) return state.refreshPromise;
  state.refreshPromise = api("/api/state")
    .then(render)
    .finally(() => {
      state.refreshPromise = null;
    });
  return state.refreshPromise;
}

async function saveArduinoWorkspace() {
  const result = await api("/api/arduino_workspace", {
    method: "POST",
    body: JSON.stringify({
      path: $("#arduinoPathInput").value,
      fqbn: state.arduinoFqbnFull || $("#arduinoFqbnInput").value,
    }),
  });
  state.arduinoDirty = false;
  renderArduino(result.arduino, true);
  await refresh();
  return result;
}

async function verifyArduinoWorkspace() {
  $("#arduinoOutput").textContent = "Copying sketch folder to sandbox and running arduino-cli compile...";
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
    $("#arduinoOutput").textContent = [
      `Status: ${result.status}`,
      result.command ? `Command: ${result.command}` : "",
      result.sandbox ? `Sandbox: ${result.sandbox}` : "",
      "",
      result.output || "",
    ].filter(Boolean).join("\n");
    state.arduinoDirty = false;
    await refresh();
    return result;
  } finally {
    state.arduinoVerifyRunning = false;
    $("#verifyArduinoBtn").disabled = false;
  }
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
  applyTheme(currentTheme());
  $$(".nav").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#refreshBtn").addEventListener("click", refresh);
  $("#saveArduinoBtn").addEventListener("click", saveArduinoWorkspace);
  $("#verifyArduinoBtn").addEventListener("click", verifyArduinoWorkspace);
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
setInterval(refresh, 1800);
