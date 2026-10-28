const stateDescriptions = {
  queued: "В очереди",
  searching: "Ищем источник",
  downloading: "Загружаем аудио",
  downloaded: "Аудио загружено",
  separating: "Разделяем дорожки",
  completed: "Обработка завершена",
  failed: "При обработке возникла ошибка",
  cancelled: "Задание отменено",
};

const batchActiveStates = new Set(["queued", "processing"]);
const form = document.querySelector("#submit-form");
const inputField = document.querySelector("#inputs");
const modelOptions = document.querySelector("#model-options");
let selectedModel = "";
const formMessage = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const openFileButton = document.querySelector("#open-file");
const fileInput = document.querySelector("#file-input");
const dropOverlay = document.querySelector("#drop-overlay");
const searchOverlay = document.querySelector("#search-overlay");
const searchTrigger = document.querySelector("#search-trigger");
const projectTabs = document.querySelector("#project-tabs");
const projectEmpty = document.querySelector("#project-empty");
const projectDetail = document.querySelector("#project-detail");
const detailTitle = document.querySelector("#detail-title");
// At most two lines (CSS); a longer title fades out at the end of the second line instead of scrolling.
const markClippedTitle = () => { detailTitle.toggleAttribute("data-clipped", detailTitle.scrollHeight > detailTitle.clientHeight + 1); };
new ResizeObserver(markClippedTitle).observe(detailTitle);
const detailControls = document.querySelector("#detail-controls");  // the master volume, over the mixer column
const detailTransport = document.querySelector("#detail-transport");  // ▶ ▶4 ⏮, before the title
const detailTime = document.querySelector("#detail-time");
const detailError = document.querySelector("#detail-error");
const playbackPanel = document.querySelector("#playback-panel");
const detailSize = document.querySelector("#detail-size");
let selectedProjectBytes = { disk: 0, shared: 0 };
const deleteProjectButton = document.querySelector("#delete-project");
const deleteDialog = document.querySelector("#delete-dialog");
const deleteDescription = document.querySelector("#delete-description");
const deleteError = document.querySelector("#delete-error");
const confirmDeleteButton = document.querySelector("#confirm-delete");
const updatesMessage = document.querySelector("#updates-message");
const releaseList = document.querySelector("#release-list");
const checkUpdatesButton = document.querySelector("#check-updates");
const themeButtons = document.querySelectorAll("[data-theme-choice]");
const accentHueField = document.querySelector("#accent-hue");
const themeColorMeta = document.querySelector('meta[name="theme-color"]');
const settingsOverlay = document.querySelector("#settings-overlay");
const helpOverlay = document.querySelector("#help-overlay");
const queueTrigger = document.querySelector("#queue-trigger");
const queuePopover = document.querySelector("#queue-popover");
let pollingTimer = null;
let activeTransport = null;
let activeMixer = null;
// Actions of the open project's player, used by the global hotkeys (null while none is ready).
let activeControls = null;
let activeAudioContext = null;
let playbackSetupId = 0;
let playbackKey = "";
// Position, play state, speed and metronome to restore when a project switches to upgraded stems.
let pendingResume = null;
let selectedProjectId = null;
let selectedProjectTitle = "";
let selectedJobId = null;

// Icons are drawn in the same 24px grid as the static SVG icons in index.html (lucide style, ISC). The stem icons
// are our own solid silhouettes (the user's reference: a Stratocaster headstock); holes by even-odd.
const transportIcons = {
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  start: '<path d="M6 5v14"/><path d="M18 5.5v13L8.5 12z" fill="currentColor" stroke="none"/>',
  metronome: '<path d="M9 3h6l4 18H5z"/><path d="M12 16l5-9"/><path d="M7 15h10"/>',
  countin: '<text x="6.5" y="17" font-size="14" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">4</text><path d="M12.5 6.5v11l8.5-5.5z" fill="currentColor" stroke="none"/>',
  vocals: '<g fill="currentColor" stroke="none"><rect x="8.5" y="1.5" width="7" height="12" rx="3.5" fill="currentColor"/><path d="M19 10.5v1a7 7 0 0 1-14 0v-1M12 18.5v3.5M8.5 22h7" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></g>',
  drums: '<g fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M2 9.5c0-2.8 4.5-5 10-5s10 2.2 10 5v7.5c0 2.8-4.5 5-10 5s-10-2.2-10-5zM4.1 9.5c0 1.5 3.5 3.2 7.9 3.2s7.9-1.7 7.9-3.2-3.5-3.2-7.9-3.2-7.9 1.7-7.9 3.2z" fill="currentColor"/><path d="m3 2 7 6.3M21 2l-7 6.3" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></g>',
  bass: '<g fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M10.6 23.5V20.9h3.7v2.6zM10 20.2h4.9v-1.1H10zM10.4 18.6 9.1 5.6C8.9 2.9 10.6 1 13.4.9c2.9-.1 5 1.4 5 3.6 0 1.9-1.6 3-2.6 4.4-.9 1.2-1.1 2.6-1.2 4.1l-.2 5.6zM10.399999999999999 6a0.8 0.8 0 1 0 1.6 0a0.8 0.8 0 1 0 -1.6 0zM10.61 9a0.8 0.8 0 1 0 1.6 0a0.8 0.8 0 1 0 -1.6 0zM10.819999999999999 12a0.8 0.8 0 1 0 1.6 0a0.8 0.8 0 1 0 -1.6 0zM11.03 15a0.8 0.8 0 1 0 1.6 0a0.8 0.8 0 1 0 -1.6 0z"/><path d="M5.6 5.15h2a0.85 0.85 0 0 1 0 1.7h-2a0.85 0.85 0 0 1 0-1.7zM7.6 5.7h1.6v0.6h-1.6zM5.81 8.15h2a0.85 0.85 0 0 1 0 1.7h-2a0.85 0.85 0 0 1 0-1.7zM7.81 8.7h1.6v0.6h-1.6zM6.02 11.15h2a0.85 0.85 0 0 1 0 1.7h-2a0.85 0.85 0 0 1 0-1.7zM8.02 11.7h1.6v0.6h-1.6zM6.2299999999999995 14.15h2a0.85 0.85 0 0 1 0 1.7h-2a0.85 0.85 0 0 1 0-1.7zM8.23 14.7h1.6v0.6h-1.6z"/></g>',
  guitar: '<g fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M10.6 23.5V20.9h3.7v2.6zM10 20.2h4.9v-1.1H10zM10.4 18.6 9.2 5.8C9 3.3 10.5 1.4 13.1 1.2c2.5-.2 4.5 1 4.7 3 .2 1.8-1.2 2.9-2.2 4.2-.8 1.1-1 2.4-1.1 3.8l-.2 6.4zM10.48 5.6a0.62 0.62 0 1 0 1.24 0a0.62 0.62 0 1 0 -1.24 0zM10.606 7.7a0.62 0.62 0 1 0 1.24 0a0.62 0.62 0 1 0 -1.24 0zM10.732000000000001 9.8a0.62 0.62 0 1 0 1.24 0a0.62 0.62 0 1 0 -1.24 0zM10.858 11.9a0.62 0.62 0 1 0 1.24 0a0.62 0.62 0 1 0 -1.24 0zM10.984 14a0.62 0.62 0 1 0 1.24 0a0.62 0.62 0 1 0 -1.24 0zM11.110000000000001 16.1a0.62 0.62 0 1 0 1.24 0a0.62 0.62 0 1 0 -1.24 0z"/><path d="M6.3 4.975h1.4a0.625 0.625 0 0 1 0 1.25h-1.4a0.625 0.625 0 0 1 0-1.25zM7.699999999999999 5.3h1.6v0.6h-1.6zM6.447 7.075h1.4a0.625 0.625 0 0 1 0 1.25h-1.4a0.625 0.625 0 0 1 0-1.25zM7.8469999999999995 7.4h1.6v0.6h-1.6zM6.594 9.175h1.4a0.625 0.625 0 0 1 0 1.25h-1.4a0.625 0.625 0 0 1 0-1.25zM7.994 9.5h1.6v0.6h-1.6zM6.741 11.275h1.4a0.625 0.625 0 0 1 0 1.25h-1.4a0.625 0.625 0 0 1 0-1.25zM8.141 11.6h1.6v0.6h-1.6zM6.888 13.375h1.4a0.625 0.625 0 0 1 0 1.25h-1.4a0.625 0.625 0 0 1 0-1.25zM8.288 13.7h1.6v0.6h-1.6zM7.035 15.475000000000001h1.4a0.625 0.625 0 0 1 0 1.25h-1.4a0.625 0.625 0 0 1 0-1.25zM8.435 15.8h1.6v0.6h-1.6z"/></g>',
  piano: '<g fill="currentColor" stroke="none"><path fill-rule="evenodd" d="M4 3.5h16a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2zM3.9 5.4v13.2h16.2V5.4z"/><path d="M5.7 5.4h2.6v7.8H5.7zM10.7 5.4h2.6v7.8h-2.6zM15.7 5.4h2.6v7.8h-2.6zM6.55 13.2h.9v5.4h-.9zM11.55 13.2h.9v5.4h-.9zM16.55 13.2h.9v5.4h-.9z"/></g>',
  other: '<g fill="currentColor" stroke="none"><path d="M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></g>',
  turtle: '<path d="m12 10 2 4v3a1 1 0 0 0 1 1h2a1 1 0 0 0 1-1v-3a8 8 0 1 0-16 0v3a1 1 0 0 0 1 1h2a1 1 0 0 0 1-1v-3l2-4h4z"/><path d="M4.82 7.9 8 10"/><path d="M15.18 7.9 12 10"/><path d="M16.93 10H20a2 2 0 0 1 0 4H2"/>',
  rabbit: '<path d="M13 16a3 3 0 0 1 2.24 5"/><path d="M18 12h.01"/><path d="M18 21h-8a4 4 0 0 1-4-4 7 7 0 0 1 7-7h.2L9.6 6.4a1 1 0 1 1 2.8-2.8L15.8 7h.2c3.3 0 6 2.7 6 6v1a2 2 0 0 1-2 2h-1a3 3 0 0 0-3 3"/><path d="M20 8.54V4a2 2 0 1 0-4 0v3"/><path d="M7.612 12.524a3 3 0 1 0-1.6 4.3"/>',
  sparkles: '<path d="M9.9 15.5a2 2 0 0 0-1.4-1.4l-6.1-1.6a.5.5 0 0 1 0-1l6.1-1.6a2 2 0 0 0 1.4-1.4l1.6-6.1a.5.5 0 0 1 1 0l1.6 6.1a2 2 0 0 0 1.4 1.4l6.1 1.6a.5.5 0 0 1 0 1l-6.1 1.6a2 2 0 0 0-1.4 1.4l-1.6 6.1a.5.5 0 0 1-1 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/>',
  original: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  speaker: '<path d="M11 4.7a.7.7 0 0 0-1.2-.5L6.4 7.6A1.4 1.4 0 0 1 5.4 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.4a1.4 1.4 0 0 1 1 .4l3.4 3.4a.7.7 0 0 0 1.2-.5z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.4 18.4a9 9 0 0 0 0-12.8"/>',
};

function iconSvg(name) {
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${transportIcons[name]}</svg>`;
}

function setIcon(button, name) {
  button.innerHTML = iconSvg(name);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error?.message || "Не удалось выполнить запрос");
  return body;
}

function setFormMessage(text, isError = false) {
  formMessage.textContent = text;
  formMessage.classList.toggle("error", isError);
}

function openSearch() {
  searchOverlay.hidden = false;
  document.body.classList.add("search-open");
  window.setTimeout(() => inputField.focus(), 0);
}

function closeSearch({ restoreFocus = true } = {}) {
  if (searchOverlay.hidden) return;
  searchOverlay.hidden = true;
  document.body.classList.remove("search-open");
  if (restoreFocus) searchTrigger.focus();
}

const diskUsage = document.querySelector("#disk-usage");
const diskMessage = document.querySelector("#disk-message");
const cleanStorageButton = document.querySelector("#clean-storage");
const deleteAllButton = document.querySelector("#delete-all-projects");
let cleanableBytes = 0;
let projectsBytes = 0;

async function loadStorage() {
  try {
    const report = await requestJson("/api/v1/storage");
    cleanableBytes = report.unused_bytes + report.temp_bytes;
    const rows = [["Проекты", report.projects_bytes], ["Файлы без проекта", report.unused_bytes],
      ["Временные файлы", report.temp_bytes], ["Модели разделения", report.models_bytes]];
    diskUsage.replaceChildren(...rows.flatMap(([label, bytes]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const value = document.createElement("dd");
      value.textContent = formatBytes(bytes);
      return [term, value];
    }));
    cleanStorageButton.disabled = report.busy || cleanableBytes === 0;
    projectsBytes = report.projects_bytes;
    deleteAllButton.disabled = report.busy;
    deleteAllButton.textContent = projectsBytes ? `Удалить все проекты (${formatBytes(projectsBytes)})` : "Удалить все проекты";
    cleanStorageButton.textContent = cleanableBytes ? `Очистить ${formatBytes(cleanableBytes)}` : "Очистить";
    diskMessage.textContent = report.busy ? "Дождитесь окончания разделения: пока оно идёт, очистка и удаление недоступны." : "";
  } catch (error) {
    diskMessage.textContent = error.message;
  }
}

async function cleanStorage() {
  const confirmed = window.confirm(`Удалить файлы без проекта и временные файлы (${formatBytes(cleanableBytes)})? Проекты не пострадают.`);
  if (!confirmed) return;
  cleanStorageButton.disabled = true;
  try {
    const result = await requestJson("/api/v1/storage/cleanup", { method: "POST", body: JSON.stringify({ confirmed: true }) });
    await loadStorage();
    diskMessage.textContent = `Освобождено ${formatBytes(result.freed_bytes)}.`;
  } catch (error) {
    diskMessage.textContent = error.message;
    cleanStorageButton.disabled = false;
  }
}

// Deletes every project one by one through the same route as the bin, so shared files and locked
// files are handled the same way.
async function deleteAllProjects() {
  let projects;
  try {
    projects = await requestJson("/api/v1/projects");
  } catch (error) {
    diskMessage.textContent = error.message;
    return;
  }
  if (!projects.length) {
    diskMessage.textContent = "Проектов нет.";
    return;
  }
  const freed = projectsBytes;
  if (!window.confirm(`Удалить все проекты (${projects.length}) вместе с их аудио и дорожками — ${formatBytes(freed)}? Это нельзя отменить.`)) return;
  deleteAllButton.disabled = cleanStorageButton.disabled = true;
  cleanupPlayback();  // Windows cannot delete a file the browser is still reading
  const failed = [];
  for (const project of projects) {
    try {
      await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}`, { method: "DELETE" });
    } catch (_error) {
      failed.push(project.title || project.input_value);
    }
  }
  selectedProjectId = null;
  selectedProjectTitle = "";
  await loadProjects();
  await loadStorage();
  diskMessage.textContent = failed.length ? `Не удалось удалить: ${failed.join(", ")}.` : `Удалено проектов: ${projects.length}. Освобождено ${formatBytes(freed)}.`;
}

// Settings open over the project (which keeps playing) and close on a click outside, Esc or ×.
function openSettings() {
  settingsOverlay.hidden = false;
  document.body.classList.add("search-open");
  history.replaceState(null, "", "#settings");
  loadStorage();
  loadModels();
}

function closeSettings() {
  if (settingsOverlay.hidden) return;
  settingsOverlay.hidden = true;
  document.body.classList.remove("search-open");
  history.replaceState(null, "", "#project");
}

// What the keydown handler below does, for the help window («?» in the header). Keep the two in step.
const HOTKEYS = [
  ["Воспроизвести / пауза", "Пробел, Enter"],
  ["Назад / вперёд на 5 секунд", "←, →"],
  ["В начало", "Home"],
  ["В начало и пауза", "Backspace"],
  ["Выбор дорожки: следующая / предыдущая", "↓, ↑"],
  ["Следующая / предыдущая дорожка по кругу", "Tab, Shift+Tab"],
  ["Выбрать Vocal, Drums, Bass, Guitar, Piano, Other", "V, D, B, G, P, O"],
  ["Solo выбранной дорожки", "S"],
  ["Mute выбранной дорожки", "M"],
  ["Громкость выбранной дорожки +10 % / −10 %", "+, −"],
  ["Открыть файл или найти песню", /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘F" : "Ctrl+F"],
  ["Ещё одна строка в поле поиска", "Shift+Enter"],
  ["Закрыть окно", "Esc"],
];

function openHelp() {
  const list = document.querySelector("#hotkey-list");
  if (!list.childElementCount) {
    list.innerHTML = HOTKEYS.map(([action, keys]) => `<div><dt>${action}</dt><dd>${keys.split(", ").map((key) => `<kbd>${key}</kbd>`).join(", ")}</dd></div>`).join("");
  }
  helpOverlay.hidden = false;
  document.body.classList.add("search-open");
}

function closeHelp() {
  if (helpOverlay.hidden) return;
  helpOverlay.hidden = true;
  document.body.classList.remove("search-open");
}

// The queue: what the worker does and what waits (GET /api/v1/queue), with a cancel button for each task.
const workingStates = new Set(["searching", "downloading", "separating", "running"]);
let queueTimer = null;

// The share done is the row's background fill, not a number.
function queueText(item) {
  const left = item.eta_seconds !== null && item.eta_seconds !== undefined ? ` · осталось ${formatMinutes(item.eta_seconds)}` : "";
  if (item.kind === "upgrade") return `Улучшение качества${item.status === "queued" ? " · в очереди" : ""}${left}`;
  return `${stateDescriptions[item.status] || item.status}${left}`;
}

function renderQueue(items) {
  queueTrigger.hidden = !items.length;
  if (!items.length) closeQueue();
  const current = items.find((item) => workingStates.has(item.status) && item.progress > 0);
  if (current) {
    queueTrigger.dataset.share = "true";
    queueTrigger.style.setProperty("--queue-progress", String(Math.max(2, Math.round(current.progress * 100))));
  } else {
    delete queueTrigger.dataset.share;
    queueTrigger.style.removeProperty("--queue-progress");
  }
  const label = items.length === 1 ? "Очередь: 1 задача" : `Очередь: ${items.length} ${items.length < 5 ? "задачи" : "задач"}`;
  queueTrigger.setAttribute("aria-label", label);
  queueTrigger.title = label;
  document.querySelector("#queue-list").replaceChildren(...items.map((item) => {
    const row = document.createElement("div");
    row.className = "queue-item";
    row.dataset.kind = item.kind;
    if (workingStates.has(item.status)) row.style.setProperty("--job-progress", `${Math.round((item.progress || 0) * 100)}%`);
    const title = document.createElement("span");
    title.className = "queue-title";
    title.textContent = item.title;
    title.title = item.title;
    const meta = document.createElement("span");
    meta.className = "queue-meta";
    meta.textContent = queueText(item);
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "icon-button";
    cancel.dataset.action = "cancel-task";
    cancel.setAttribute("aria-label", `Отменить: ${item.title}`);
    cancel.title = "Отменить";
    cancel.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
    cancel.addEventListener("click", () => cancelTask(item));
    row.append(title, meta, cancel);
    return row;
  }));
}

async function refreshQueue() {
  window.clearTimeout(queueTimer);
  let items = [];
  try {
    items = await requestJson("/api/v1/queue");
  } catch (_error) { /* the API is restarting: the next look shows it */ }
  renderQueue(items);
  queueTimer = window.setTimeout(refreshQueue, items.length ? 2000 : 10000);
}

// A task that runs loses its progress when cancelled, so that one asks first.
async function cancelTask(item) {
  const what = item.kind === "upgrade" ? `улучшение «${item.title}»` : `«${item.title}»`;
  if (workingStates.has(item.status) && !window.confirm(`Отменить ${what}?`)) return;
  try {
    if (item.kind === "upgrade") {
      await requestJson(`/api/v1/projects/${encodeURIComponent(item.project_id)}/upgrade`, { method: "DELETE" });
    } else {
      await requestJson(`/api/v1/jobs/${encodeURIComponent(item.job_id)}/cancel`, { method: "POST" });
    }
  } catch (error) {
    detailError.textContent = error.message;
    detailError.hidden = false;
  }
  await refreshQueue();
  await loadProjects();
}

function openQueue() {
  queuePopover.hidden = false;
  queueTrigger.setAttribute("aria-expanded", "true");
}

function closeQueue() {
  queuePopover.hidden = true;
  queueTrigger.setAttribute("aria-expanded", "false");
}

function activateScreen(name) {
  if (name === "settings") {
    openSettings();
  } else {
    closeSettings();
    loadProjects();
  }
}

function statusBucket(status) {
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  return status;
}

let projectPollTimer = null;
const finishedStates = new Set(["completed", "failed", "cancelled"]);

// While a job runs, the tab fills with its progress; poll until every project has finished.
function scheduleProjectPoll(projects) {
  if (projectPollTimer !== null || !projects.some((project) => !finishedStates.has(project.job_status))) return;
  projectPollTimer = window.setTimeout(() => {
    projectPollTimer = null;
    loadProjects();
  }, 1000);
}

function updateProjectTab(button, project) {
  const nameText = project.title || project.input_value;
  const state = stateDescriptions[project.job_status] || project.job_status;
  button.dataset.status = statusBucket(project.job_status);
  button.style.setProperty("--job-progress", `${Math.round((project.progress || 0) * 100)}%`);
  button.setAttribute("aria-selected", String(project.project_id === selectedProjectId));
  button.setAttribute("aria-label", `${nameText}. ${state}`);
  button.title = `${nameText} — ${state}`;
  button.firstElementChild.textContent = nameText;
}

// The open project's job, as last seen by the poll: drives its progress bar and the switch to the mixer.
let selectedJobState = null;

// Separation time for one minute of music: "~55 с" or "~11 мин".
function perMinuteOfSong(secondsPerSecond) {
  const seconds = secondsPerSecond * 60;
  return seconds < 60 ? `~${Math.max(5, Math.round(seconds / 5) * 5)} с` : `~${Math.round(seconds / 60)} мин`;
}

function formatMinutes(seconds) {
  return seconds < 60 ? "меньше минуты" : `~${Math.round(seconds / 60)} мин`;
}

function etaText(state) {
  if (state.eta_seconds === null || state.eta_seconds === undefined) return "";
  return state.job_status === "separating"
    ? ` · осталось ${formatMinutes(state.eta_seconds)}`
    : ` · разделение займёт ${formatMinutes(state.eta_seconds)}`;
}

function renderJobProgress(state) {
  let bar = playbackPanel.querySelector(".job-progress");
  if (!bar) {
    const label = document.createElement("p");
    label.className = "job-progress-label";
    bar = document.createElement("div");
    bar.className = "job-progress";
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.append(document.createElement("span"));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "quiet-button job-cancel";
    cancel.dataset.action = "cancel-job";
    cancel.textContent = "Отменить";
    cancel.addEventListener("click", () => cancelTask({
      kind: "job", job_id: selectedJobId, title: selectedProjectTitle, status: selectedJobState?.job_status || "searching" }));
    playbackPanel.querySelector(".separate-again")?.remove();  // «Разделить» was pressed: progress again
    if (activeControls) playbackPanel.prepend(label, bar, cancel);  // over the original audio, which plays meanwhile
    else playbackPanel.replaceChildren(label, bar, cancel);
    playbackPanel.hidden = false;
  }
  const percent = Math.round((state.progress || 0) * 100);
  bar.firstElementChild.style.width = `${percent}%`;
  bar.setAttribute("aria-valuenow", String(percent));
  playbackPanel.querySelector(".job-progress-label").textContent =
    `${stateDescriptions[state.job_status] || state.job_status} · ${percent}%${etaText(state)}`;
}

function followSelectedJob(projects) {
  const selected = projects.find((project) => project.project_id === selectedProjectId);
  if (!selected) return;
  const wasRunning = selectedJobState && !finishedStates.has(selectedJobState.job_status);
  const sourceReady = (state) => state === "downloaded" || state === "separating";  // polling may miss «downloaded»
  const downloadedNow = sourceReady(selected.job_status) && !sourceReady(selectedJobState?.job_status);
  selectedJobState = selected;
  if (downloadedNow) {
    openProject(selected.project_id);  // the source audio is there: play it while the stems are made
  } else if (!finishedStates.has(selected.job_status)) {
    renderJobProgress(selected);
  } else if (wasRunning) {
    openProject(selected.project_id);  // finished while open: show the mixer (or the error)
  }
}

function renderProjects(projects) {
  scheduleProjectPoll(projects);
  followSelectedJob(projects);
  const tabs = [...projectTabs.querySelectorAll(".project-tab")];
  // Same projects in the same order: update in place, so polling never resets hover, drag or scroll.
  if (tabs.length && tabs.length === projects.length && tabs.every((tab, index) => tab.dataset.projectId === projects[index].project_id)) {
    tabs.forEach((tab, index) => updateProjectTab(tab, projects[index]));
    return;
  }
  if (projects.length === 0) {
    const empty = document.createElement("p");
    empty.className = "tabs-empty";
    empty.textContent = "Проекты появятся здесь";
    projectTabs.replaceChildren(empty);
    selectedProjectId = null;
    selectedProjectTitle = "";
    projectEmpty.hidden = false;
    projectDetail.hidden = true;
    return;
  }
  projectTabs.replaceChildren(...projects.map((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-tab";
    button.dataset.projectId = project.project_id;
    button.dataset.jobId = project.job_id;
    button.setAttribute("role", "tab");
    const name = document.createElement("span");
    name.className = "project-tab-name";
    button.append(name);
    updateProjectTab(button, project);
    button.addEventListener("click", () => openProject(project.project_id));
    return button;
  }));
  updateTabFades();
}

// Fades mark the edges where more tabs are hidden.
function updateTabFades() {
  const hidden = projectTabs.scrollWidth - projectTabs.clientWidth;
  projectTabs.classList.toggle("fade-left", projectTabs.scrollLeft > 1);
  projectTabs.classList.toggle("fade-right", projectTabs.scrollLeft < hidden - 1);
}

projectTabs.addEventListener("scroll", updateTabFades, { passive: true });
window.addEventListener("resize", updateTabFades);
// The mouse wheel scrolls the tab strip sideways.
projectTabs.addEventListener("wheel", (event) => {
  if (projectTabs.scrollWidth <= projectTabs.clientWidth) return;
  event.preventDefault();
  projectTabs.scrollLeft += Math.abs(event.deltaY) > Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
}, { passive: false });
// Dragging scrolls the strip; past a 5px threshold the drag no longer counts as a tab click.
let tabDrag = null;
let suppressTabClick = false;
projectTabs.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || event.pointerType === "touch") return; // touch already scrolls natively
  tabDrag = { id: event.pointerId, x: event.clientX, left: projectTabs.scrollLeft, moved: false };
});
projectTabs.addEventListener("pointermove", (event) => {
  if (!tabDrag || event.pointerId !== tabDrag.id) return;
  const dx = event.clientX - tabDrag.x;
  if (!tabDrag.moved && Math.abs(dx) > 5) {
    tabDrag.moved = true;
    projectTabs.setPointerCapture(event.pointerId);
    projectTabs.classList.add("dragging");
  }
  if (tabDrag.moved) projectTabs.scrollLeft = tabDrag.left - dx;
});
function endTabDrag() {
  if (!tabDrag) return;
  if (tabDrag.moved) {
    suppressTabClick = true;
    window.setTimeout(() => { suppressTabClick = false; }, 0);
  }
  projectTabs.classList.remove("dragging");
  tabDrag = null;
}
projectTabs.addEventListener("pointerup", endTabDrag);
projectTabs.addEventListener("pointercancel", endTabDrag);
projectTabs.addEventListener("click", (event) => {
  if (!suppressTabClick) return;
  event.preventDefault();
  event.stopPropagation();
}, true);

const SVG_NS = "http://www.w3.org/2000/svg";

// One filled shape: the peak envelope on top and its mirror below, in a 1024x100 viewBox that the
// browser scales to any width (preserveAspectRatio="none"), so nothing overlaps at any size.
function waveformPath(levels) {
  const step = 1024 / Math.max(1, levels.length);
  const point = (peak, index, sign) => `${((index + 0.5) * step).toFixed(2)} ${(50 + sign * Math.min(1, peak) * 48).toFixed(2)}`;
  const top = levels.map((peak, index) => point(peak, index, -1));
  const bottom = levels.map((peak, index) => point(peak, index, 1)).reverse();
  return `M0 50L${top.join("L")}L1024 50L${bottom.join("L")}Z`;
}

async function drawWaveform(svg, assetId) {
  try {
    const waveform = await requestJson(`/api/v1/assets/${encodeURIComponent(assetId)}/waveform`);
    if (waveform.asset_id !== assetId) return;
    const axis = document.createElementNS(SVG_NS, "line");
    axis.setAttribute("class", "waveform-axis");
    axis.setAttribute("x1", "0");
    axis.setAttribute("y1", "50");
    axis.setAttribute("x2", "1024");
    axis.setAttribute("y2", "50");
    const shape = document.createElementNS(SVG_NS, "path");
    shape.setAttribute("class", "waveform-shape");
    shape.setAttribute("d", waveformPath(waveform.levels));
    svg.replaceChildren(axis, shape);
  } catch (_error) {
    svg.setAttribute("aria-label", "Форма волны недоступна");
  }
}

function cleanupPlayback() {
  playbackSetupId += 1;
  if (activeTransport) activeTransport.stop();
  activeTransport = null;
  activeMixer = null;
  activeControls = null;
  if (activeAudioContext && activeAudioContext.state !== "closed") activeAudioContext.close().catch(() => {});
  activeAudioContext = null;
  // Detached <audio> keeps downloading and holds the browser's per-host connections; abort it.
  playbackPanel.querySelectorAll("audio").forEach((audio) => {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  });
  playbackPanel.replaceChildren();
  playbackPanel.hidden = true;
  detailControls.replaceChildren();
  detailTransport.replaceChildren();
  detailTime.textContent = "";
  playbackKey = "";
}

const STEM_LABELS = { vocals: "Vocal", drums: "Drums", bass: "Bass", guitar: "Guitar", piano: "Piano", other: "Other", original: "Original" };

// Files of a finished project were removed from disk by hand: say which, and offer to make them again.
function renderMissingFiles(project, missing) {
  const message = document.createElement("p");
  message.className = "playback-message";
  const names = missing.map((asset) => (asset.asset_type === "stem" ? STEM_LABELS[asset.stem_name] || asset.stem_name : "исходное аудио"));
  message.textContent = `С диска удалены файлы проекта: ${names.join(", ")}.`;
  if (project.input_type === "local_file") {  // nothing to download again: the user has the file
    message.textContent += " Удалите проект и откройте файл заново.";
    playbackPanel.replaceChildren(message);
    playbackPanel.hidden = false;
    return;
  }
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "primary-button";
  retry.textContent = "Разделить заново";
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    try {
      // The broken project goes (what is left of it too) and the same song is queued again.
      await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}`, { method: "DELETE" });
      const model = project.job.separation_options && project.job.separation_options.model_name;
      const created = await requestJson("/api/v1/batches", {
        method: "POST",
        body: JSON.stringify(model ? { inputs: [project.input_value], model_name: model } : { inputs: [project.input_value] }),
      });
      selectedProjectId = null;
      await loadProjects(created.job_ids[0]);
    } catch (error) {
      message.textContent = error.message;
      retry.disabled = false;
    }
  });
  playbackPanel.replaceChildren(message, retry);
  playbackPanel.hidden = false;
}

const sourceAudio = (project) => project.assets.find((asset) => asset.asset_type === "source_audio" && asset.available);

function renderSeparateAgain(project) {
  const box = document.createElement("div");
  box.className = "separate-again";
  const note = document.createElement("p");
  note.className = "playback-message";
  note.textContent = project.job.status === "cancelled" ? "Разделение отменено." : "Разделить не удалось.";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "primary-button";
  button.dataset.action = "separate";
  button.textContent = "Разделить";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await requestJson(`/api/v1/projects/${encodeURIComponent(project.project_id)}/separate`, { method: "POST" });
      await loadProjects();
      refreshQueue();
    } catch (error) {
      note.textContent = error.message;
      button.disabled = false;
    }
  });
  box.append(note, button);
  playbackPanel.prepend(box);
}

function renderProject(project) {
  // Re-rendering the same project (tab click, job-finished refresh) must not restart playback;
  // rebuild only when the project or its stem files change.
  const key = `${project.project_id}:${project.assets.map((a) => `${a.asset_id}${a.available ? "" : "?"}`).join(",")}`;
  const rebuild = key !== playbackKey;
  if (rebuild) cleanupPlayback();
  selectedProjectId = project.project_id;
  selectedProjectTitle = project.title || project.input_value;
  selectedJobId = project.job.job_id;
  projectEmpty.hidden = true;
  projectDetail.hidden = false;
  detailTitle.textContent = selectedProjectTitle;
  detailTitle.title = selectedProjectTitle;
  markClippedTitle();
  detailError.textContent = project.job.error_summary || "";
  detailError.hidden = !project.job.error_summary;
  deleteProjectButton.dataset.projectId = project.project_id;
  selectedProjectBytes = { disk: project.disk_bytes || 0, shared: project.shared_bytes || 0 };
  detailSize.textContent = formatBytes(selectedProjectBytes.disk + selectedProjectBytes.shared);
  detailSize.title = "Место на диске: исходное аудио и дорожки";
  if (rebuild) {
    playbackKey = key;
    const missing = project.assets.filter((asset) => !asset.available);
    if (!finishedStates.has(project.job.status)) {
      const known = selectedJobState && selectedJobState.project_id === project.project_id ? selectedJobState : null;
      renderJobProgress(known || { job_status: project.job.status, progress: 0 });
      // Downloaded: one track, «Original», to check it is the right song while it is separated.
      const source = sourceAudio(project);
      if (source) setupProjectPlayback([{ ...source, asset_type: "stem", stem_name: "original" }], ++playbackSetupId, 0, project, true);
    } else if (["cancelled", "failed"].includes(project.job.status) && sourceAudio(project)) {
      // Stopped before its stems: listen to the song and separate it again («Разделить»).
      setupProjectPlayback([{ ...sourceAudio(project), asset_type: "stem", stem_name: "original" }], ++playbackSetupId, 0, project, true);
    } else if (project.job.status === "completed" && missing.some((asset) => asset.asset_type === "stem")) {
      renderMissingFiles(project, missing);
    } else {
      setupProjectPlayback(project.assets, ++playbackSetupId, project.start_seconds || 0, project);
    }
  }
  document.querySelectorAll(".project-tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.projectId === project.project_id));
  });
}

async function setupProjectPlayback(assets, setupId, startOffset = 0, project = {}, original = false) {
  const stemOrder = ["vocals", "drums", "bass", "guitar", "piano", "other", "original"];
  const stemLabels = STEM_LABELS;
  const stemsByName = new Map(assets.filter((asset) => asset.asset_type === "stem").map((asset) => [asset.stem_name, asset]));
  // Four-stem models have no guitar and piano rows.
  const stems = stemOrder.map((name) => stemsByName.get(name)).filter(Boolean);
  const heading = document.createElement("p");
  heading.className = "playback-message";
  if (!stems.length || stems.some((asset) => !asset.available || (!original && !["wav", "flac"].includes(asset.media_format)))) {
    heading.textContent = "Для синхронного воспроизведения нужны все дорожки проекта.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
    return;
  }

  try {
    const { MixerSession, SynchronizedTransport, Metronome, PlayheadClock, StretchEngine, countInPlan, formatBpm, metronomeGain, rhythmFound, snapFaderPercent, tempoAt } = await import("./playback.mjs");
    const projectId = selectedProjectId;
    if (setupId !== playbackSetupId) return;
    const tracks = stems.map((asset) => {
      const audio = document.createElement("audio");
      audio.preload = "metadata";
      // Browsers allow six HTTP/1.1 connections per host, and six buffering stems would take
      // them all; loading stems from the other loopback name keeps API calls responsive.
      const path = `/api/v1/assets/${encodeURIComponent(asset.asset_id)}`;
      const url = new URL(path, window.location.href);
      url.hostname = { "127.0.0.1": "localhost", localhost: "127.0.0.1" }[url.hostname] || url.hostname;
      audio.crossOrigin = "anonymous";
      audio.src = url.href;
      audio.addEventListener("error", () => {
        if (audio.getAttribute("src") === url.href && url.href !== new URL(path, window.location.href).href) audio.src = path;
      }, { once: true });
      audio.setAttribute("aria-label", stemLabels[asset.stem_name]);
      audio.dataset.stem = asset.stem_name;
      return audio;
    });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("Web Audio is not supported in this browser");
    // The stems' own rate (both models write 44.1 kHz). At the device's rate (usually 48 kHz) Chrome resamples
    // every <audio> stem on its own and the six no longer add up cleanly: crackle on bright sounds. At 44.1 kHz
    // the mix reaches the output unchanged and is resampled once, as a whole, by the audio output.
    const audioContext = new AudioContextClass({ sampleRate: 44100 });
    // «Общая»: every stem (streamed or stretched, both end in its gain node) passes it; the metronome does not.
    const masterGain = audioContext.createGain();
    masterGain.connect(audioContext.destination);
    const gainNodes = tracks.map((audio) => {
      const source = audioContext.createMediaElementSource(audio);
      const gain = audioContext.createGain();
      source.connect(gain);
      gain.connect(masterGain);
      audio.volume = 1;
      audio.dataset.effectiveGain = "1";
      return gain;
    });
    activeAudioContext = audioContext;
    const mixerRowElements = [];
    const mixer = new MixerSession(gainNodes, (values, audible) => {
      tracks.forEach((audio, index) => { audio.dataset.effectiveGain = String(values[index]); });
      mixerRowElements.forEach((row, index) => { row.dataset.silenced = String(!audible[index]); });
    });
    activeMixer = mixer;

    const time = detailTime;
    // One playhead layer over the waveform column of all stem rows; it is the position slider
    // for both pointer and keyboard.
    const playheadLayer = document.createElement("div");
    playheadLayer.className = "playhead-layer";
    playheadLayer.dataset.control = "seek";
    playheadLayer.tabIndex = 0;
    playheadLayer.setAttribute("role", "slider");
    playheadLayer.setAttribute("aria-label", "Позиция воспроизведения");
    playheadLayer.setAttribute("aria-valuemin", "0");
    playheadLayer.setAttribute("aria-valuemax", "0");
    playheadLayer.setAttribute("aria-valuenow", "0");
    const playhead = document.createElement("span");
    playhead.className = "playhead";
    playheadLayer.append(playhead);
    // One screen pixel wide, on whole screen pixels: at 125-150 % scaling a 1px line at a fractional position was
    // drawn one or two pixels wide by turns (it flickered). A transform moves it without a layout.
    const playheadClock = new PlayheadClock();
    let playheadRatio = 0;
    let layerBox = { left: 0, width: 0 };
    const drawPlayhead = () => {
      const scale = window.devicePixelRatio || 1;
      const x = Math.round((layerBox.left + playheadRatio * layerBox.width) * scale) / scale - layerBox.left;
      playhead.style.width = `${1 / scale}px`;
      playhead.style.transform = `translateX(${x}px)`;
    };
    new ResizeObserver(() => {
      const box = playheadLayer.getBoundingClientRect();
      layerBox = { left: box.left + window.scrollX, width: box.width };
      drawPlayhead();
    }).observe(playheadLayer);
    let duration = 0;
    const formatTime = (seconds) => {
      const safe = Math.max(0, Math.floor(seconds || 0));
      return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
    };
    const updateTime = (seconds, playing = false, rate = 1) => {
      const shown = playheadClock.position(seconds, performance.now(), playing, rate);
      playheadRatio = duration > 0 ? Math.min(1, Math.max(0, shown / duration)) : 0;
      drawPlayhead();
      playheadLayer.setAttribute("aria-valuenow", seconds.toFixed(1));
      playheadLayer.setAttribute("aria-valuetext", formatTime(seconds));
      time.textContent = `${formatTime(seconds)} / ${formatTime(duration)}`;
    };
    const play = document.createElement("button");
    play.type = "button";
    play.className = "transport-button";
    play.dataset.action = "toggle";
    setIcon(play, "play");
    play.setAttribute("aria-label", "Воспроизвести");
    play.title = "Воспроизвести";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "transport-button";
    reset.dataset.action = "reset";
    setIcon(reset, "start");
    reset.setAttribute("aria-label", "В начало и пауза");
    reset.title = "В начало и пауза";
    const countIn = document.createElement("button");
    countIn.type = "button";
    countIn.className = "transport-button";
    countIn.dataset.action = "count-in";
    setIcon(countIn, "countin");
    countIn.setAttribute("aria-label", "Отсчёт на четыре и воспроизведение");
    countIn.title = "Отсчёт на четыре и воспроизведение";
    const buttons = document.createElement("div");
    buttons.className = "playback-buttons";
    buttons.append(play, countIn, reset);
    countIn.hidden = original;  // no beats before the stems exist
    const error = document.createElement("span");
    error.className = "playback-error";
    const mixerRows = document.createElement("div");
    mixerRows.className = "mixer-grid";

    const updateTransportButton = (isPlaying) => {
      setIcon(play, isPlaying ? "pause" : "play");
      play.setAttribute("aria-label", isPlaying ? "Пауза" : "Воспроизвести");
      play.title = isPlaying ? "Пауза" : "Воспроизвести";
    };
    let selectedStem = 0;
    const selectStem = (index) => {
      selectedStem = Math.min(stems.length - 1, Math.max(0, index));
      mixerRowElements.forEach((row, i) => { row.dataset.selected = String(i === selectedStem); });
    };
    // Metronome: its own bus with a limiter, so a loud click never distorts; the project gain is untouched.
    const metronomeBus = audioContext.createGain();
    const limiter = audioContext.createDynamicsCompressor();
    limiter.threshold.value = -2;
    limiter.ratio.value = 20;
    limiter.attack.value = 0.001;
    limiter.release.value = 0.05;
    metronomeBus.connect(limiter);
    limiter.connect(audioContext.destination);
    const metronome = new Metronome(audioContext, metronomeBus);
    let metronomeOn = false;
    let songTempo = null;  // [[time, bpm], ...] from the analysis, shown next to the metronome
    let noRhythm = false;  // said where the tempo would be: the icon-only button has no room for it
    const bpmLabel = document.createElement("span");
    bpmLabel.className = "metronome-bpm";
    const showTempo = (seconds) => {
      const text = noRhythm ? "Ритм не найден" : formatBpm(tempoAt(songTempo, seconds), metronome.rate);
      if (bpmLabel.textContent !== text) bpmLabel.textContent = text;  // changes at most every four beats
    };
    const transport = new SynchronizedTransport(tracks, {
      onTimeUpdate: (seconds) => {
        updateTime(seconds, transport.playing, transport.rate);
        showTempo(seconds);
        if (metronomeOn && transport.playing) metronome.update(seconds);
        else metronome.reset();
        metronomeButton.dataset.clicks = String(metronome.clicks);
      },
      onEnded: () => {
        metronome.reset();
        updateTransportButton(false);
      },
      beforePlay: () => audioContext.resume(),
      startOffset,
    });
    activeTransport = transport;
    const seekFromPointer = (event, element) => {
      const rect = element.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      if (duration > 0) transport.seek(ratio * duration);
    };
    const attachPointerSeek = (element) => {
      element.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        element.setPointerCapture(event.pointerId);
        seekFromPointer(event, element);
      });
      element.addEventListener("pointermove", (event) => {
        if (element.hasPointerCapture(event.pointerId)) seekFromPointer(event, element);
      });
    };
    // The layer covers the whole waveform column, gaps included, so a click anywhere there seeks.
    attachPointerSeek(playheadLayer);

    stems.forEach((asset, index) => {
      const row = document.createElement("div");
      row.className = "mixer-row";
      mixerRowElements.push(row);
      row.addEventListener("pointerdown", () => selectStem(index));
      // Explicit rows: the playhead layer spans all stem rows, which would push auto-placed rows down.
      row.style.gridRow = String(index + 1);
      row.dataset.stem = asset.stem_name;
      const left = document.createElement("div");
      left.className = "mixer-left";
      const name = document.createElement("span");
      name.className = "mixer-name";
      // On a narrow screen only the first letter shows (CSS), after the icon; the letter is also the stem's hotkey.
      const label = stemLabels[asset.stem_name] || asset.stem_name;
      name.innerHTML = `${transportIcons[asset.stem_name] ? iconSvg(asset.stem_name) : ""}<span class="mixer-label" data-short="${label[0]}">${label}</span>`;
      const switches = document.createElement("div");
      switches.className = "mixer-switches";
      const solo = document.createElement("button");
      solo.type = "button";
      solo.className = "mixer-toggle";
      solo.textContent = "S";
      solo.dataset.control = "solo";
      solo.setAttribute("aria-label", `Solo: ${stemLabels[asset.stem_name]}`);
      solo.setAttribute("aria-pressed", "false");
      solo.addEventListener("click", () => {
        const selected = solo.getAttribute("aria-pressed") !== "true";
        solo.setAttribute("aria-pressed", String(selected));
        mixer.setSolo(index, selected);
      });
      const mute = document.createElement("button");
      mute.type = "button";
      mute.className = "mixer-toggle";
      mute.textContent = "M";
      mute.dataset.control = "mute";
      mute.setAttribute("aria-label", `Mute: ${stemLabels[asset.stem_name]}`);
      mute.setAttribute("aria-pressed", "false");
      mute.addEventListener("click", () => {
        const muted = mute.getAttribute("aria-pressed") !== "true";
        mute.setAttribute("aria-pressed", String(muted));
        mixer.setMute(index, muted);
      });
      switches.append(solo, mute);
      const fader = document.createElement("div");
      fader.className = "mixer-fader";
      const gain = document.createElement("input");
      gain.type = "range";
      gain.min = "0";
      gain.max = "150";
      gain.step = "1";
      gain.value = "100";
      // Filled part of the custom track, as a share of the 0-150 % range.
      const showFill = () => gain.style.setProperty("--fill", `${(Number(gain.value) / 150) * 100}%`);
      showFill();
      gain.dataset.control = "gain";
      gain.tabIndex = -1; // volume is mouse-only; arrow keys belong to the global hotkeys
      gain.setAttribute("aria-label", `Громкость ${stemLabels[asset.stem_name]}`);
      gain.addEventListener("input", () => {
        const percent = snapFaderPercent(Number(gain.value));
        gain.value = String(percent);
        showFill();
        mixer.setGain(index, percent / 100);
      });
      fader.append(gain);
      const heading = document.createElement("div");
      heading.className = "mixer-heading";
      heading.append(name, switches);
      left.append(heading, fader);
      const wave = document.createElement("div");
      wave.className = "mixer-waveform";
      const waveSvg = document.createElementNS(SVG_NS, "svg");
      waveSvg.setAttribute("class", "waveform-preview");
      waveSvg.setAttribute("viewBox", "0 0 1024 100");
      waveSvg.setAttribute("preserveAspectRatio", "none");
      waveSvg.setAttribute("role", "img");
      waveSvg.setAttribute("aria-label", `Форма волны: ${stemLabels[asset.stem_name]}`);
      wave.append(waveSvg);
      if (!original) drawWaveform(waveSvg, asset.asset_id);  // none for the source audio (WAV/FLAC only)
      row.append(left, wave);
      mixerRows.append(row);
    });

    // The master volume in the header over the mixer column: 0-100 % for the whole mix, remembered for this project.
    const masterRow = document.createElement("div");
    masterRow.className = "mixer-master";  // not a .mixer-row: the stem rows are counted and selected by that class
    const masterName = document.createElement("span");
    masterName.className = "mixer-name";
    masterName.innerHTML = iconSvg("speaker");  // the fader carries the name «Общая громкость»
    const masterFader = document.createElement("div");
    masterFader.className = "mixer-fader master-fader";
    const master = document.createElement("input");
    master.type = "range";
    master.min = "0";
    master.max = "100";
    master.step = "1";
    master.dataset.control = "master-volume";
    master.tabIndex = -1;
    master.setAttribute("aria-label", "Общая громкость");
    const masterKey = `audio-stems-master-volume:${projectId}`;
    let savedMaster = "100";
    try { savedMaster = localStorage.getItem(masterKey) || "100"; } catch (_error) { /* storage blocked */ }
    master.value = savedMaster;
    const applyMaster = () => {
      masterGain.gain.value = Number(master.value) / 100;
      master.style.setProperty("--fill", `${master.value}%`);
      playbackPanel.dataset.masterGain = String(Number(master.value) / 100);  // the AudioParam is float32
    };
    applyMaster();
    master.addEventListener("input", () => {
      applyMaster();
      storeSetting(masterKey, master.value);
    });
    masterFader.append(master);
    masterRow.append(masterName, masterFader);

    let countInTimer = null;
    const cancelCountIn = () => {
      window.clearTimeout(countInTimer);
      countInTimer = null;
      countIn.removeAttribute("aria-pressed");
      metronome.reset();  // drops the count-in clicks that have not sounded yet
    };
    const togglePlayback = async () => {
      error.textContent = "";
      if (countInTimer !== null) {  // Space or ▶ during the count-in stops it
        cancelCountIn();
        return;
      }
      if (transport.playing) {
        transport.pause();
        metronome.reset();
        updateTransportButton(false);
        return;
      }
      try {
        await transport.play();
        updateTransportButton(transport.playing);
      } catch (_reason) {
        updateTransportButton(false);
        error.textContent = "Браузер не разрешил запустить все дорожки. Нажмите ▶ ещё раз.";
      }
    };
    play.addEventListener("click", togglePlayback);
    reset.addEventListener("click", () => {
      if (countInTimer !== null) cancelCountIn();
      transport.stop();
      metronome.reset();
      updateTransportButton(false);
    });

    // Footer under the tracks: the metronome and its volume.
    const footer = document.createElement("div");
    footer.className = "project-footer";
    const metronomeButton = document.createElement("button");
    metronomeButton.type = "button";
    metronomeButton.className = "footer-toggle metronome-toggle";
    metronomeButton.setAttribute("aria-pressed", "false");
    setIcon(metronomeButton, "metronome");
    // Icon only: the name (and the analysis progress, also shown as the fill) is the tooltip.
    const nameMetronome = (text) => {
      metronomeButton.title = text;
      metronomeButton.setAttribute("aria-label", text);
    };
    nameMetronome("Метроном");
    const volumeWrap = document.createElement("div");
    volumeWrap.className = "mixer-fader footer-fader";
    const volume = document.createElement("input");
    volume.type = "range";
    volume.min = "0";
    volume.max = "100";
    volume.step = "1";
    volume.dataset.control = "metronome-volume";
    volume.tabIndex = -1;
    volume.setAttribute("aria-label", "Громкость метронома");
    let savedVolume = "50";
    try { savedVolume = localStorage.getItem("audio-stems-metronome-volume") || "50"; } catch (_error) { /* storage blocked */ }
    volume.value = savedVolume;
    const showVolume = () => {
      volume.style.setProperty("--fill", `${volume.value}%`);
      metronomeBus.gain.value = metronomeGain(volume.value);
    };
    showVolume();
    volume.addEventListener("input", () => {
      showVolume();
      storeSetting("audio-stems-metronome-volume", volume.value);
    });
    volumeWrap.append(volume);

    // Speed 25-150 % in 5 % steps, pitch kept. The first change decodes the stems (about half a GB for
    // a 4-minute song, released with the project) and plays them through Signalsmith Stretch.
    const speedWrap = document.createElement("div");
    speedWrap.className = "footer-speed";
    const slower = document.createElement("span");
    slower.className = "speed-end";
    slower.innerHTML = iconSvg("turtle");
    const faster = document.createElement("span");
    faster.className = "speed-end";
    faster.innerHTML = iconSvg("rabbit");
    const speedFader = document.createElement("div");
    speedFader.className = "mixer-fader speed-fader";
    const speed = document.createElement("input");
    speed.type = "range";
    speed.min = "25";
    speed.max = "150";
    speed.step = "5";
    speed.value = "100";
    speed.dataset.control = "speed";
    speed.tabIndex = -1;
    speed.setAttribute("aria-label", "Скорость воспроизведения");
    const speedValue = document.createElement("span");  // a tooltip over the thumb (hover, drag, preparing)
    speedValue.className = "footer-speed-value";
    speedValue.setAttribute("aria-hidden", "true");  // the slider itself reports its value
    const scale = document.createElement("div");
    scale.className = "speed-scale";
    scale.setAttribute("aria-hidden", "true");
    scale.innerHTML = [25, 100, 150].map((value) => `<span style="left: ${((value - 25) / 125) * 100}%">${value}%</span>`).join("");
    speedFader.append(scale, speed, speedValue);
    speedWrap.append(slower, speedFader, faster);
    let stretchLoading = null;
    const loadStretch = () => {
      stretchLoading = stretchLoading || (async () => {
        // Three steps per stem (downloaded, decoded, handed to its node), shown as a fill on the block.
        let steps = 0;
        const step = () => {
          steps += 1;
          const percent = Math.round((steps / (3 * tracks.length)) * 100);
          speedWrap.style.setProperty("--job-progress", `${percent}%`);
          speedValue.textContent = `подготовка ${percent}%`;
        };
        speedWrap.dataset.loading = "true";
        speedWrap.style.setProperty("--job-progress", "0%");
        speedValue.textContent = "подготовка 0%";
        const { default: createStretch } = await import("./vendor/signalsmith-stretch-1.3.2.mjs");
        const decoded = await Promise.all(tracks.map(async (audio) => {
          const response = await fetch(audio.currentSrc || audio.src);
          const data = await response.arrayBuffer();
          step();
          const buffer = await audioContext.decodeAudioData(data);
          step();
          return buffer;
        }));
        transport.stretch = await StretchEngine.create(audioContext, createStretch, decoded, gainNodes, step);
        delete speedWrap.dataset.loading;
        speedWrap.style.removeProperty("--job-progress");
      })().catch((reason) => {
        stretchLoading = null;
        delete speedWrap.dataset.loading;
        speedWrap.style.removeProperty("--job-progress");
        error.textContent = `Не удалось включить изменение скорости: ${reason.message}`;
        throw reason;
      });
      return stretchLoading;
    };
    const showSpeed = () => {
      speed.style.setProperty("--fill", `${((Number(speed.value) - 25) / 125) * 100}%`);
      speedFader.style.setProperty("--share", String((Number(speed.value) - 25) / 125));
      if (!speedWrap.dataset.loading) speedValue.textContent = `${speed.value}%`;
    };
    const applySpeed = async () => {
      showSpeed();
      const rate = Number(speed.value) / 100;
      if (rate !== 1 && !transport.stretch) {
        try { await loadStretch(); } catch (_reason) { speed.value = "100"; showSpeed(); return; }
        if (setupId !== playbackSetupId) return;
      }
      const target = Number(speed.value) / 100;  // the fader may have moved while loading
      metronome.reset();  // clicks already scheduled were timed for the old speed
      metronome.rate = target;
      showTempo(transport.currentTime);  // the tempo heard at the new speed
      await transport.setPlaybackRate(target);
      speedWrap.dataset.engine = transport.stretching ? "stretch" : "stream";
      showSpeed();
    };
    speed.addEventListener("input", applySpeed);
    speed.addEventListener("dblclick", () => {
      speed.value = "100";
      applySpeed();
    });
    showSpeed();

    // «Улучшить качество»: the worker separates the song again with the best model while this project
    // keeps playing; when the new stems are ready the player switches to them at the same position.
    const upgradeButton = document.createElement("button");
    upgradeButton.type = "button";
    upgradeButton.className = "footer-toggle footer-upgrade";
    upgradeButton.dataset.action = "upgrade";
    upgradeButton.innerHTML = `${iconSvg("sparkles")}<span></span>`;
    const upgradeText = upgradeButton.querySelector("span");
    const upgradeCancel = document.createElement("button");  // only while the upgrade waits or runs
    upgradeCancel.type = "button";
    upgradeCancel.className = "icon-button upgrade-cancel";
    upgradeCancel.dataset.action = "cancel-upgrade";
    upgradeCancel.setAttribute("aria-label", "Отменить улучшение");
    upgradeCancel.title = "Отменить улучшение";
    upgradeCancel.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
    upgradeCancel.hidden = true;
    let upgradeStatus = "none";
    upgradeCancel.addEventListener("click", () => cancelTask({ kind: "upgrade", project_id: projectId, title: selectedProjectTitle, status: upgradeStatus }));
    const showUpgrade = (state) => {
      const busy = state.status === "queued" || state.status === "running";
      upgradeStatus = state.status;
      upgradeCancel.hidden = !busy;
      upgradeButton.disabled = Boolean(state.best) || busy;
      if (busy) upgradeButton.dataset.analyzing = "true";
      else delete upgradeButton.dataset.analyzing;
      // Only while it runs: an inline value outlives the stylesheet's «not analysing → 0 %», so a project upgraded
      // here kept a full fill on its finished button while one separated with the best model had none.
      if (busy) upgradeButton.style.setProperty("--job-progress", `${Math.round((state.progress || 0) * 100)}%`);
      else upgradeButton.style.removeProperty("--job-progress");
      const left = state.eta_seconds !== null && state.eta_seconds !== undefined ? ` · осталось ${formatMinutes(state.eta_seconds)}` : "";
      if (state.best) upgradeText.textContent = "Улучшенное качество разделения";
      else if (state.status === "queued") upgradeText.textContent = `Улучшение в очереди${left}`;
      else if (state.status === "running") upgradeText.textContent = `Улучшаем · ${Math.round(state.progress * 100)}%${left}`;
      else upgradeText.textContent = `Улучшить качество${state.estimate_seconds ? ` · ${formatMinutes(state.estimate_seconds)}` : ""}`;
    };
    const followUpgrade = async (state) => {
      while (state.status === "queued" || state.status === "running") {
        showUpgrade(state);
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        if (setupId !== playbackSetupId) return;
        state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/upgrade`);
        if (setupId !== playbackSetupId) return;
      }
      showUpgrade(state);
      if (state.status === "failed") error.textContent = state.error || "Не удалось улучшить качество";
      if (state.status === "done" && state.best) {
        pendingResume = activeControls ? activeControls.snapshot() : null;
        await openProject(projectId);  // new stem files: the player is rebuilt on them
      }
    };
    const followSafely = (state) => followUpgrade(state).catch((reason) => {
      error.textContent = reason.message;
      upgradeButton.disabled = false;
    });
    upgradeButton.addEventListener("click", async () => {
      upgradeButton.disabled = true;
      try {
        await followSafely(await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/upgrade`, { method: "POST" }));
      } catch (reason) {
        error.textContent = reason.message;
        upgradeButton.disabled = false;
      }
    });
    const upgrade = project.upgrade || { status: "none", best: false };
    showUpgrade(upgrade);
    if (upgrade.status === "queued" || upgrade.status === "running") followSafely(upgrade);

    footer.append(metronomeButton, bpmLabel, volumeWrap, upgradeButton, upgradeCancel, speedWrap);

    const setMetronome = (on) => {
      metronomeOn = on;
      metronomeButton.setAttribute("aria-pressed", String(on));
      if (!on) metronome.reset();
    };
    // Beats are detected on the first use only (it takes about half a minute), with progress on the button.
    let beatsRequest = null;
    const loadBeats = () => {
      beatsRequest = beatsRequest || (async () => {
        let state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/beats`, { method: "POST" });
        while (state.status === "queued" || state.status === "running") {
          if (setupId !== playbackSetupId) return null;
          metronomeButton.dataset.analyzing = "true";
          metronomeButton.style.setProperty("--job-progress", `${Math.round(state.progress * 100)}%`);
          nameMetronome(`Метроном · ${Math.round(state.progress * 100)}%`);
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/beats`);
        }
        delete metronomeButton.dataset.analyzing;
        metronomeButton.style.removeProperty("--job-progress");
        nameMetronome("Метроном");
        if (state.status !== "done") throw new Error(state.error || "Не удалось определить доли");
        if (!rhythmFound(state.result)) {
          setMetronome(false);
          nameMetronome("Ритм не найден");
          noRhythm = true;
          showTempo(transport.currentTime);
          metronomeButton.disabled = volume.disabled = countIn.disabled = true;
          return null;
        }
        metronome.setBeats(state.result.beats);
        songTempo = state.result.tempo && state.result.tempo.length ? state.result.tempo : [[0, state.result.bpm]];
        showTempo(transport.currentTime);
        return state.result.beats;
      })().catch((reason) => {
        beatsRequest = null;
        delete metronomeButton.dataset.analyzing;
        metronomeButton.style.removeProperty("--job-progress");
        nameMetronome("Метроном");
        error.textContent = reason.message;
        setMetronome(false);
        return null;
      });
      return beatsRequest;
    };
    countIn.addEventListener("click", async () => {
      if (countInTimer !== null || transport.playing) {
        await togglePlayback();  // a second press stops, like ▶
        return;
      }
      const beats = metronome.beats.length ? metronome.beats : await loadBeats();
      if (!beats || setupId !== playbackSetupId) return;
      await audioContext.resume();
      const plan = countInPlan(beats, transport.currentTime, audioContext.currentTime + 0.05, 4, transport.rate);
      if (!plan) return;
      plan.clicks.forEach((when) => metronome.click(when));
      metronomeButton.dataset.clicks = String(metronome.clicks);
      countIn.setAttribute("aria-pressed", "true");
      // Without the metronome only these four clicks sound; with it, it carries on from the song's beats.
      countInTimer = window.setTimeout(async () => {
        countInTimer = null;
        countIn.removeAttribute("aria-pressed");
        await togglePlayback();
      }, Math.max(0, (plan.audioStart - audioContext.currentTime) * 1000));
    });
    metronomeButton.addEventListener("click", async () => {
      if (metronomeOn) {
        setMetronome(false);
        return;
      }
      setMetronome(true);
      await loadBeats();
    });
    let positioned = false;
    tracks.forEach((audio) => audio.addEventListener("loadedmetadata", () => {
      if (transport.duration > 0) {
        duration = transport.duration;
        playheadLayer.setAttribute("aria-valuemax", duration.toFixed(1));
        // Once all stems know their length, move them past the leading silence.
        if (!positioned) {
          positioned = true;
          const resume = pendingResume && pendingResume.projectId === projectId ? pendingResume : null;
          pendingResume = null;
          transport.seek(resume ? resume.position : 0);
          if (resume) restore(resume);
        } else {
          updateTime(transport.currentTime);
        }
      }
    }));
    detailControls.replaceChildren(masterRow);
    detailTransport.replaceChildren(buttons);
    playheadLayer.style.gridRow = `1 / span ${stems.length}`;
    mixerRows.append(playheadLayer);
    if (original) playbackPanel.replaceChildren(error, mixerRows, ...tracks);  // metronome, upgrade, speed: with the stems
    else playbackPanel.replaceChildren(error, mixerRows, footer, ...tracks);
    playbackPanel.hidden = false;
    selectStem(0);
    // After an upgrade the new player picks up where the old one was.
    const restore = async (resume) => {
      if (resume.speed !== speed.value) {
        speed.value = resume.speed;
        await applySpeed();
      }
      if (resume.metronome) {
        setMetronome(true);
        await loadBeats();
      }
      if (resume.playing && setupId === playbackSetupId) await togglePlayback();
    };
    activeControls = {
      snapshot: () => ({ projectId, position: transport.currentTime, playing: transport.playing, speed: speed.value, metronome: metronomeOn }),
      togglePlayback,
      seekBy: (seconds) => transport.seek(transport.currentTime + seconds),
      seekTo: (seconds) => transport.seek(seconds),
      moveSelection: (step) => selectStem(selectedStem + step),
      cycleSelection: (step) => selectStem((selectedStem + step + stems.length) % stems.length),
      selectStemByName: (name) => {
        const index = stems.findIndex((asset) => asset.stem_name === name);
        if (index >= 0) selectStem(index);
      },
      press: (control) => mixerRowElements[selectedStem].querySelector(`[data-control="${control}"]`).click(),
      toStartAndPause: () => reset.click(),
      nudgeGain: (step) => {  // through the slider's own input handler: the same 0-150 % range and 100 % snap
        const gain = mixerRowElements[selectedStem].querySelector('[data-control="gain"]');
        gain.value = String(Number(gain.value) + step);
        gain.dispatchEvent(new Event("input"));
      },
    };
    tracks.forEach((audio) => audio.load());
    if (original && finishedStates.has(project.job.status)) renderSeparateAgain(project);
    else if (original) renderJobProgress(selectedJobState || { job_status: project.job.status, progress: 0 });
  } catch (reason) {
    console.error("Playback setup failed", reason);
    heading.textContent = "Не удалось подготовить синхронное воспроизведение в этом браузере.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
  }
}

async function openProject(projectId) {
  if (projectId !== selectedProjectId) cleanupPlayback();
  try {
    const project = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`);
    renderProject(project);
    renderProjects(await requestJson("/api/v1/projects"));
  } catch (error) {
    detailError.textContent = error.message;
    detailError.hidden = false;
  }
}

async function loadProjects(selectJobId = null) {
  try {
    const projects = await requestJson("/api/v1/projects");
    renderProjects(projects);
    if (selectJobId) {
      const match = projects.find((project) => project.job_id === selectJobId);
      if (match) await openProject(match.project_id);
    } else if (!selectedProjectId && projects.length) {
      await openProject(projects[0].project_id);
    } else if (selectedProjectId) {
      const selected = projects.find((project) => project.project_id === selectedProjectId);
      if (!selected) {
        selectedProjectId = null;
        projectDetail.hidden = true;
        projectEmpty.hidden = false;
      }
    }
  } catch (error) {
    projectTabs.replaceChildren();
    const message = document.createElement("p");
    message.className = "tabs-empty";
    message.textContent = error.message;
    projectTabs.append(message);
  }
}

async function refreshBatch(batchId, selectedJobId) {
  try {
    const batch = await requestJson(`/api/v1/batches/${encodeURIComponent(batchId)}`);
    await loadProjects();
    if (!batchActiveStates.has(batch.status)) {
      const matching = [...projectTabs.querySelectorAll(".project-tab")].find((tab) => tab.dataset.jobId === selectedJobId);
      // Show the finished project only if the user has not moved on to another one meanwhile.
      if (matching && (!selectedProjectId || selectedProjectId === matching.dataset.projectId)) {
        await openProject(matching.dataset.projectId);
      }
      pollingTimer = null;
      return;
    }
    pollingTimer = window.setTimeout(() => refreshBatch(batchId, selectedJobId), 1200);
  } catch (_error) {
    pollingTimer = window.setTimeout(() => refreshBatch(batchId, selectedJobId), 2500);
  }
}

// Settings → Обновления: the releases newer than this copy; the newest can be installed.
function renderReleases(result) {
  const releases = result.releases;
  if (releases.length === 0) {
    releaseList.replaceChildren();
    updatesMessage.textContent = `Установлена версия ${result.current} — это последняя.`;
    return;
  }
  updatesMessage.textContent = `Установлена версия ${result.current}. Есть новая:`;
  releaseList.replaceChildren(...releases.map((release, index) => {
    const card = document.createElement("article");
    card.className = "release-card";
    const title = document.createElement("h3");
    title.textContent = release.name || release.tag_name;
    const published = document.createElement("p");
    published.textContent = new Date(release.published_at).toLocaleDateString("ru-RU");
    const notes = document.createElement("p");
    notes.className = "release-notes";
    notes.textContent = release.notes || "В этом релизе нет описания.";
    const link = document.createElement("a");
    link.href = release.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Открыть на GitHub";
    card.append(title, published, notes, link);
    if (index === 0) {
      const apply = document.createElement("button");
      apply.type = "button";
      apply.className = "primary-button";
      apply.dataset.action = "install-update";
      apply.textContent = `Обновить до ${release.tag_name}`;
      apply.addEventListener("click", async () => {
        if (!window.confirm(`Установить «${release.name || release.tag_name}»? Задачи в очереди должны закончиться. Ваши песни и настройки не затрагиваются. После установки приложение нужно запустить заново.`)) return;
        apply.disabled = true;
        updatesMessage.textContent = "Скачиваем обновление…";
        try {
          const done = await requestJson("/api/v1/updates/apply", {
            method: "POST",
            body: JSON.stringify({ tag_name: release.tag_name, confirmed: true }),
          });
          updatesMessage.textContent = done.state === "applied"
            ? "Обновление установлено. Закройте окно Audio Stems и запустите приложение снова."
            : "Обновление скачано. Закройте окно Audio Stems и запустите приложение снова: оно установится при запуске.";
        } catch (error) {
          updatesMessage.textContent = error.message;
          apply.disabled = false;
        }
      });
      card.append(apply);
    }
    return card;
  }));
}

async function checkUpdates() {
  checkUpdatesButton.disabled = true;
  updatesMessage.textContent = "Проверяем новые версии на GitHub…";
  try {
    renderReleases(await requestJson("/api/v1/updates"));
  } catch (error) {
    updatesMessage.textContent = error.message;
  } finally {
    checkUpdatesButton.disabled = false;
  }
}

// The separation model is chosen in Settings and remembered in this browser.
async function loadModels() {
  let saved = "";
  try { saved = localStorage.getItem("audio-stems-model") || ""; } catch (_error) { /* storage blocked: use the default */ }
  try {
    const models = await requestJson("/api/v1/models");
    selectedModel = (models.find((model) => model.name === saved) || models.find((model) => model.default) || models[0] || {}).name || "";
    modelOptions.replaceChildren(...models.map((model) => {
      const option = document.createElement("label");
      option.className = "model-option";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "model";
      radio.value = model.name;
      radio.checked = model.name === selectedModel;
      radio.addEventListener("change", () => {
        selectedModel = model.name;
        storeSetting("audio-stems-model", model.name);
      });
      const name = document.createElement("span");
      name.className = "model-option-name";
      name.textContent = `${model.label || model.name} · ${model.stems.length} дорожек`.replace("4 дорожек", "4 дорожки");
      const note = document.createElement("span");
      note.className = "model-option-note";
      note.textContent = model.description || "";
      if (model.seconds_per_audio_second) {
        const time = document.createElement("span");
        time.className = "model-option-time";
        time.textContent = `${perMinuteOfSong(model.seconds_per_audio_second)} на минуту песни · ${model.measured
          ? "по последним разделениям на этом компьютере" : "оценка, уточнится после первого разделения"}`;
        note.append(document.createElement("br"), time);
      }
      option.append(radio, name, note);
      return option;
    }));
  } catch (error) {
    modelOptions.textContent = `Не удалось загрузить модели: ${error.message}`;
  }
}

async function submitSearch(event) {
  event.preventDefault();
  if (pollingTimer !== null) window.clearTimeout(pollingTimer);
  const inputs = inputField.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (inputs.length === 0) {
    setFormMessage("Введите название песни или ссылку.", true);
    return;
  }
  submitButton.disabled = true;
  setFormMessage("Добавляем в очередь…");
  try {
    const created = await requestJson("/api/v1/batches", {
      method: "POST",
      body: JSON.stringify(selectedModel ? { inputs, model_name: selectedModel } : { inputs }),
    });
    inputField.value = "";
    inputField.style.height = "";
    closeSearch({ restoreFocus: false });
    await loadProjects(created.job_ids[0]);
    pollingTimer = window.setTimeout(() => refreshBatch(created.batch_id, created.job_ids[0]), 1200);
  } catch (error) {
    // The API names the problem with a link in English; the only place the dialog mentions YouTube.
    setFormMessage(/URL/.test(error.message) ? "Ссылка не подходит: поддерживаются ссылки YouTube (youtube.com, youtu.be)." : error.message, true);
  } finally {
    submitButton.disabled = false;
  }
}

async function uploadFiles(files) {
  if (files.length === 0) return;
  openSearch();
  if (pollingTimer !== null) window.clearTimeout(pollingTimer);
  submitButton.disabled = openFileButton.disabled = true;
  let created = null;
  try {
    for (const file of files) {
      setFormMessage(`Загружаем «${file.name}»…`);
      const query = selectedModel ? `?model_name=${encodeURIComponent(selectedModel)}` : "";
      // The body is the file itself (no form encoding); the server reads its name from X-Filename.
      const response = await fetch(`/api/v1/uploads${query}`, {
        method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(`«${file.name}»: ${body.error?.message || "не удалось загрузить"}`);
      created = body;
    }
    setFormMessage("");
    closeSearch({ restoreFocus: false });
  } catch (error) {
    setFormMessage(error.message, true);
  } finally {
    submitButton.disabled = openFileButton.disabled = false;
    fileInput.value = "";
  }
  if (created) {
    await loadProjects(created.job_ids[0]);
    pollingTimer = window.setTimeout(() => refreshBatch(created.batch_id, created.job_ids[0]), 1200);
  }
}

function formatBytes(bytes) {
  if (bytes >= 2 ** 30) return `${(bytes / 2 ** 30).toFixed(1).replace(".", ",")} ГБ`;
  return `${Math.max(bytes ? 1 : 0, Math.round(bytes / 2 ** 20))} МБ`;
}

async function confirmProjectDeletion() {
  const projectId = deleteProjectButton.dataset.projectId;
  if (!projectId) return;
  confirmDeleteButton.disabled = true;
  // Stop the stems first: Windows cannot delete a file the browser is still reading.
  cleanupPlayback();
  deleteError.hidden = true;
  try {
    await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    deleteDialog.close("deleted");
    selectedProjectId = null;
    selectedProjectTitle = "";
    await loadProjects();
  } catch (error) {
    deleteError.textContent = error.message;
    deleteError.hidden = false;
    openProject(projectId);  // the project stays: bring its player back
  } finally {
    confirmDeleteButton.disabled = false;
  }
}

// Appearance: theme and accent hue live on <html>; index.html re-applies the saved values on load.
function storeSetting(key, value) {
  try { localStorage.setItem(key, value); } catch (_error) { /* private mode: setting lasts this page only */ }
}

function applyTheme(theme) {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  themeButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.themeChoice === theme)));
  themeColorMeta.content = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
}

themeButtons.forEach((button) => button.addEventListener("click", () => {
  applyTheme(button.dataset.themeChoice);
  storeSetting("audio-stems-theme", button.dataset.themeChoice);
}));
accentHueField.addEventListener("input", () => {
  document.documentElement.style.setProperty("--accent-h", accentHueField.value);
  storeSetting("audio-stems-accent-hue", accentHueField.value);
});
accentHueField.value = getComputedStyle(document.documentElement).getPropertyValue("--accent-h").trim() || "197";
applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");

searchTrigger.addEventListener("click", openSearch);
document.querySelector("#close-search").addEventListener("click", () => closeSearch());
searchOverlay.addEventListener("click", (event) => {
  if (event.target === searchOverlay) closeSearch();
});
document.querySelector("#close-settings").addEventListener("click", closeSettings);
document.querySelector("#help-trigger").addEventListener("click", openHelp);
queueTrigger.addEventListener("click", () => (queuePopover.hidden ? openQueue() : closeQueue()));
document.addEventListener("click", (event) => {
  if (!queuePopover.hidden && !queuePopover.contains(event.target) && !queueTrigger.contains(event.target)) closeQueue();
});
document.querySelector("#close-help").addEventListener("click", closeHelp);
helpOverlay.addEventListener("click", (event) => {
  if (event.target === helpOverlay) closeHelp();
});
settingsOverlay.addEventListener("click", (event) => {
  if (event.target === settingsOverlay) closeSettings();
});
function isTextEntry(target) {
  if (!(target instanceof Element)) return false;
  if (target.isContentEditable || target.matches("textarea, select")) return true;
  return target.matches("input") && !["range", "checkbox", "radio", "button", "submit"].includes(target.type);
}

// Hotkeys work whenever a project is open and no text field has focus; event.code keeps
// S/M working on any keyboard layout.
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.code === "KeyF") {
    event.preventDefault();
    openSearch();
    return;
  }
  if (event.key === "Escape" && !searchOverlay.hidden) {
    closeSearch();
    return;
  }
  if (event.key === "Escape" && !settingsOverlay.hidden) {
    closeSettings();
    return;
  }
  if (event.key === "Escape" && !queuePopover.hidden) {
    closeQueue();
    return;
  }
  if (event.key === "Escape" && !helpOverlay.hidden) {
    closeHelp();
    return;
  }
  // Tab never walks the focus (and its focus rings) outside dialogs; in a project it picks the next track.
  if (event.key === "Tab" && searchOverlay.hidden && settingsOverlay.hidden && helpOverlay.hidden && !deleteDialog.open && !isTextEntry(event.target)) {
    event.preventDefault();
    if (activeControls && !event.ctrlKey && !event.metaKey && !event.altKey) {
      activeControls.cycleSelection(event.shiftKey ? -1 : 1);
    }
    return;
  }
  if (!activeControls || !searchOverlay.hidden || !settingsOverlay.hidden || !helpOverlay.hidden || deleteDialog.open) return;
  if (event.ctrlKey || event.metaKey || event.altKey || isTextEntry(event.target)) return;
  const controls = activeControls;
  const actions = {
    ArrowLeft: () => controls.seekBy(-5),
    ArrowRight: () => controls.seekBy(5),
    Home: () => controls.seekTo(0),
    Backspace: () => controls.toStartAndPause(),  // the ⏮ button: back to the start, paused
    ArrowUp: () => controls.moveSelection(-1),
    ArrowDown: () => controls.moveSelection(1),
    Space: () => controls.togglePlayback(),
    Enter: () => controls.togglePlayback(),
    NumpadEnter: () => controls.togglePlayback(),
    KeyS: () => controls.press("solo"),
    KeyM: () => controls.press("mute"),
    KeyV: () => controls.selectStemByName("vocals"),
    KeyD: () => controls.selectStemByName("drums"),
    KeyB: () => controls.selectStemByName("bass"),
    KeyG: () => controls.selectStemByName("guitar"),
    KeyP: () => controls.selectStemByName("piano"),
    KeyO: () => controls.selectStemByName("other"),
    // The «+» and «−» keys, by position, so any layout works: =/+ and -/_ on the main keys, and the numpad.
    Equal: () => controls.nudgeGain(10),
    NumpadAdd: () => controls.nudgeGain(10),
    Minus: () => controls.nudgeGain(-10),
    NumpadSubtract: () => controls.nudgeGain(-10),
  };
  const action = actions[event.code];
  if (action) {
    // preventDefault stops a focused button or slider from also reacting to the key.
    event.preventDefault();
    const toggles = ["Space", "Enter", "NumpadEnter", "KeyS", "KeyM"].includes(event.code);
    if (!(toggles && event.repeat)) action();
  } else if (event.target instanceof Element && event.target.matches('[data-control="gain"]')) {
    event.preventDefault(); // PageUp/PageDown must not change volume either
  }
});
document.querySelectorAll("[data-screen]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    activateScreen(link.dataset.screen);
  });
});
form.addEventListener("submit", submitSearch);
inputField.addEventListener("keydown", (event) => {
  // Enter starts the search, Shift+Enter adds a line; Enter that confirms an IME composition is left alone.
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});
openFileButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => uploadFiles(Array.from(fileInput.files)));
let dragDepth = 0;  // dragenter/dragleave fire for every child element crossed
const draggingFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
window.addEventListener("dragenter", (event) => {
  if (!draggingFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  dropOverlay.hidden = false;
});
window.addEventListener("dragover", (event) => {
  if (draggingFiles(event)) event.preventDefault();  // allows the drop
});
window.addEventListener("dragleave", (event) => {
  if (!draggingFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropOverlay.hidden = true;
});
window.addEventListener("drop", (event) => {
  if (!draggingFiles(event)) return;
  event.preventDefault();  // otherwise the browser opens the file itself
  dragDepth = 0;
  dropOverlay.hidden = true;
  uploadFiles(Array.from(event.dataTransfer.files));
});
inputField.addEventListener("input", () => {
  inputField.style.height = "auto";
  inputField.style.height = `${Math.min(inputField.scrollHeight, window.innerHeight * 0.4)}px`;
});
deleteProjectButton.addEventListener("click", () => {
  const shared = selectedProjectBytes.shared
    ? ` ${formatBytes(selectedProjectBytes.shared)} общих файлов останутся: их использует другой проект.` : "";
  deleteDescription.textContent = `Проект «${selectedProjectTitle}», исходное аудио и все дорожки будут удалены. Освободится ${formatBytes(selectedProjectBytes.disk)}.${shared}`;
  deleteError.hidden = true;
  deleteDialog.showModal();
});
confirmDeleteButton.addEventListener("click", confirmProjectDeletion);
document.querySelector("#cancel-delete").addEventListener("click", () => deleteDialog.close("cancel"));
checkUpdatesButton.addEventListener("click", checkUpdates);
cleanStorageButton.addEventListener("click", cleanStorage);
deleteAllButton.addEventListener("click", deleteAllProjects);

loadProjects();
loadModels();
refreshQueue();
if (location.hash === "#settings") openSettings();
