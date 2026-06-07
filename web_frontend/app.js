const state = {
  arduinoDirty: false,
  arduinoVerifyRunning: false,
  refreshPromise: null,
};

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

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function renderStats(payload) {
  const arduino = payload.arduino || {};
  const rows = [
    ["Role", payload.role || "Tool server"],
    ["Arduino", arduino.valid ? "Ready" : "Not ready"],
    ["Files", String((arduino.files || []).length)],
    ["FQBN", arduino.fqbn || "Unset"],
  ];
  $("#stats").innerHTML = rows
    .map(([label, value]) => `<div class="stat"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`)
    .join("");
}

function renderArduino(arduino, force = false) {
  if (!arduino) return;
  if (!state.arduinoDirty || force) {
    $("#arduinoPathInput").value = arduino.path || "";
    $("#arduinoFqbnInput").value = arduino.fqbn || "";
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

function render(payload) {
  $("#modeLine").textContent = `${payload.role} | ${payload.root}`;
  $("#toolList").textContent = (payload.tools || []).join("\n");
  renderStats(payload);
  renderArduino(payload.arduino);
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
      fqbn: $("#arduinoFqbnInput").value,
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
        fqbn: $("#arduinoFqbnInput").value,
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
  $$(".nav").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#refreshBtn").addEventListener("click", refresh);
  $("#saveArduinoBtn").addEventListener("click", saveArduinoWorkspace);
  $("#verifyArduinoBtn").addEventListener("click", verifyArduinoWorkspace);
  $$("#workspace input").forEach((input) => {
    input.addEventListener("input", () => {
      state.arduinoDirty = true;
      $("#verifyArduinoBtn").disabled = false;
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
