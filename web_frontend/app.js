const state = {
  tasks: [],
  selectedIds: new Set(),
  activeTaskId: null,
  queueSplitY: null,
  renderedDetailTaskId: null,
  settingsDirty: false,
  refreshPromise: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function setView(viewId) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === viewId));
  $$(".nav").forEach((button) => button.classList.toggle("active", button.dataset.view === viewId));
}

function renderStats(counts) {
  $("#stats").innerHTML = ["queued", "running", "done", "failed"]
    .map((key) => `<div class="stat"><span>${key.toUpperCase()}</span><b>${counts[key] ?? 0}</b></div>`)
    .join("");
}

function taskDetail(task) {
  if (!task) return "Select a task to inspect output.";
  return [
    `Task #${task.id} [${task.status}]`,
    `Created: ${task.created_at}`,
    `Updated: ${task.updated_at}`,
    "",
    "Prompt:",
    task.prompt || "",
    "",
    "Result:",
    task.result || "",
    "",
    "Error:",
    task.error || "",
  ].join("\n");
}

function renderTasks(tasks) {
  const rows = tasks.map((task) => {
    const id = Number(task.id);
    const status = safeStatus(task.status);
    const checked = state.selectedIds.has(id) ? "checked" : "";
    const selected = state.activeTaskId === Number(task.id) ? "selected" : "";
    return `
      <tr class="${selected}" data-id="${id}">
        <td><input class="row-check" type="checkbox" data-id="${id}" ${checked}></td>
        <td class="status-${status}">${escapeHtml(status)}</td>
        <td>${escapeHtml(task.created_at || "")}</td>
        <td>${escapeHtml(task.preview || "")}</td>
      </tr>
    `;
  });
  $("#taskRows").innerHTML = rows.join("");
  $("#selectionText").textContent = `${state.selectedIds.size} selected`;
  $("#selectAll").checked = tasks.length > 0 && state.selectedIds.size === tasks.length;
  const activeTask = tasks.find((task) => Number(task.id) === state.activeTaskId);
  const detail = $("#taskDetail");
  const detailTaskId = activeTask ? Number(activeTask.id) : null;
  detail.textContent = taskDetail(activeTask);
  if (state.renderedDetailTaskId !== detailTaskId) {
    detail.scrollTop = 0;
    state.renderedDetailTaskId = detailTaskId;
  }
}

function renderSettings(config, force = false) {
  if (state.settingsDirty && !force) return;
  $("#modelInput").value = config.model ?? "";
  $("#urlInput").value = config.ollama_url ?? "";
  $("#ctxInput").value = config.num_ctx ?? 4096;
  $("#tempInput").value = config.temperature ?? 0.4;
  $("#languageInput").value = config.language ?? "vi";
  $("#modelEnabledInput").checked = Boolean(config.model_enabled);
  $("#shellInput").checked = Boolean(config.allow_shell);
}

function render(payload) {
  state.tasks = payload.tasks;
  const currentIds = new Set(payload.tasks.map((task) => Number(task.id)));
  state.selectedIds = new Set([...state.selectedIds].filter((id) => currentIds.has(id)));
  if (state.activeTaskId && !currentIds.has(state.activeTaskId)) {
    state.activeTaskId = null;
  }
  $("#modeLine").textContent = `${payload.mode} | ${payload.language} | ${payload.shell} | ${payload.root}`;
  renderStats(payload.counts);
  renderTasks(payload.tasks);
  renderSettings(payload.config);
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

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function safeStatus(value) {
  const status = String(value || "");
  return ["queued", "running", "done", "failed"].includes(status) ? status : "queued";
}

async function queueTask() {
  const prompt = $("#promptInput").value.trim();
  if (!prompt) return;
  await api("/api/tasks", { method: "POST", body: JSON.stringify({ prompt }) });
  $("#promptInput").value = "";
  await refresh();
}

async function saveSettings() {
  const result = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      model: $("#modelInput").value,
      ollama_url: $("#urlInput").value,
      num_ctx: Number($("#ctxInput").value),
      temperature: Number($("#tempInput").value),
      language: $("#languageInput").value,
      model_enabled: $("#modelEnabledInput").checked,
      allow_shell: $("#shellInput").checked,
    }),
  });
  state.settingsDirty = false;
  await refresh();
  return result;
}

function bindEvents() {
  $$(".nav").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#refreshBtn").addEventListener("click", refresh);
  $("#queueBtn").addEventListener("click", queueTask);
  $("#promptInput").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") queueTask();
  });
  $("#taskRows").addEventListener("click", (event) => {
    const checkbox = event.target.closest(".row-check");
    if (checkbox) {
      const id = Number(checkbox.dataset.id);
      checkbox.checked ? state.selectedIds.add(id) : state.selectedIds.delete(id);
      renderTasks(state.tasks);
      return;
    }
    const row = event.target.closest("tr");
    if (!row) return;
    state.activeTaskId = Number(row.dataset.id);
    state.renderedDetailTaskId = null;
    renderTasks(state.tasks);
  });
  $("#selectAll").addEventListener("change", (event) => {
    state.selectedIds = event.target.checked ? new Set(state.tasks.map((task) => Number(task.id))) : new Set();
    renderTasks(state.tasks);
  });
  $("#clearSelectedBtn").addEventListener("click", async () => {
    await api("/api/clear_selected", { method: "POST", body: JSON.stringify({ ids: [...state.selectedIds] }) });
    state.selectedIds.clear();
    await refresh();
  });
  $("#clearDoneBtn").addEventListener("click", async () => {
    await api("/api/clear_done", { method: "POST", body: JSON.stringify({}) });
    await refresh();
  });
  $("#saveSettingsBtn").addEventListener("click", saveSettings);
  $$("#settings input, #settings select").forEach((input) => {
    input.addEventListener("input", () => {
      state.settingsDirty = true;
    });
    input.addEventListener("change", () => {
      state.settingsDirty = true;
    });
  });
  $("#checkModelBtn").addEventListener("click", async () => {
    $("#modelStatus").textContent = "Checking Ollama...";
    const result = await api("/api/check_model", { method: "POST", body: JSON.stringify({}) });
    $("#modelStatus").textContent = result.message;
  });
  $("#minimizeBtn").addEventListener("click", () => window.pywebview?.api?.minimize?.());
  $("#maximizeBtn").addEventListener("click", () => window.pywebview?.api?.toggle_maximize?.());
  $("#closeBtn").addEventListener("click", () => window.pywebview?.api?.close?.());
  bindQueueResizer();
}

function bindQueueResizer() {
  const resizer = $("#queueResizer");
  const grid = $(".queue-grid");
  if (!resizer || !grid) return;
  const initialRatio = 0.38;

  const cssPixels = (name, fallback) => {
    const value = Number.parseFloat(window.getComputedStyle(document.documentElement).getPropertyValue(name));
    return Number.isFinite(value) ? value : fallback;
  };

  const splitBounds = () => {
    const rect = grid.getBoundingClientRect();
    const minQueue = cssPixels("--queue-min-height", 132);
    const minDetail = cssPixels("--detail-min-height", 140);
    const gap = cssPixels("--queue-pane-gap", 22);
    const available = Math.max(1, rect.height - gap);
    if (available < minQueue + minDetail) {
      return { min: 1, max: Math.max(1, available - 1) };
    }
    return {
      min: minQueue,
      max: Math.max(minQueue, rect.height - gap - minDetail),
    };
  };

  const clampSplitY = (desiredY) => {
    const { min, max } = splitBounds();
    return Math.max(min, Math.min(max, desiredY));
  };

  const setSplitY = (desiredY) => {
    const next = clampSplitY(desiredY);
    state.queueSplitY = next;
    grid.style.setProperty("--queue-split-y", `${Math.round(next)}px`);
  };

  const currentSplitY = () => {
    if (Number.isFinite(state.queueSplitY)) return state.queueSplitY;
    const tableRect = $(".queue-grid > .table-panel")?.getBoundingClientRect();
    return tableRect?.height || Math.round(grid.getBoundingClientRect().height * initialRatio);
  };

  const setSplitYFromPointer = (clientY) => {
    const rect = grid.getBoundingClientRect();
    setSplitY(clientY - rect.top);
  };

  resizer.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    const pointerId = event.pointerId;
    const gridTop = grid.getBoundingClientRect().top;
    const dragOffsetY = event.clientY - gridTop - currentSplitY();
    document.body.classList.add("queue-resizing");
    resizer.setPointerCapture?.(pointerId);

    const onMove = (moveEvent) => {
      moveEvent.preventDefault();
      setSplitYFromPointer(moveEvent.clientY - dragOffsetY);
    };

    const onUp = (upEvent) => {
      document.body.classList.remove("queue-resizing");
      if (resizer.hasPointerCapture?.(pointerId)) resizer.releasePointerCapture(pointerId);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };

    setSplitYFromPointer(event.clientY - dragOffsetY);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  });

  const resizeObserver = new ResizeObserver(() => {
    const rect = grid.getBoundingClientRect();
    const initialY = Math.round(rect.height * initialRatio);
    setSplitY(state.queueSplitY ?? initialY);
  });
  resizeObserver.observe(grid);
}

bindEvents();
refresh();
setInterval(refresh, 1800);
