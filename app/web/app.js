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
const detailControls = document.querySelector("#detail-controls");
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

// Transport icons are drawn in the same 24px grid as the static SVG icons in index.html.
const transportIcons = {
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  start: '<path d="M6 5v14"/><path d="M18 5.5v13L8.5 12z" fill="currentColor" stroke="none"/>',
  metronome: '<path d="M9 3h6l4 18H5z"/><path d="M12 16l5-9"/><path d="M7 15h10"/>',
  countin: '<text x="6.5" y="17" font-size="14" font-weight="700" fill="currentColor" stroke="none" text-anchor="middle">4</text><path d="M12.5 6.5v11l8.5-5.5z" fill="currentColor" stroke="none"/>',
};

function setIcon(button, name) {
  button.innerHTML = `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${transportIcons[name]}</svg>`;
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
    playbackPanel.replaceChildren(label, bar);
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
  selectedJobState = selected;
  if (!finishedStates.has(selected.job_status)) {
    if (!activeControls) renderJobProgress(selected);
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
  detailTime.textContent = "";
  playbackKey = "";
}

const STEM_LABELS = { vocals: "Vocal", drums: "Drums", bass: "Bass", guitar: "Guitar", piano: "Piano", other: "Other" };

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

function renderProject(project) {
  // Re-rendering the same project (tab click, job-finished refresh) must not restart playback;
  // rebuild only when the project or its stem files change.
  const key = `${project.project_id}:${project.assets.map((a) => `${a.asset_id}${a.available ? "" : "?"}`).join(",")}`;
  const rebuild = key !== playbackKey;
  if (rebuild) cleanupPlayback();
  selectedProjectId = project.project_id;
  selectedProjectTitle = project.title || project.input_value;
  projectEmpty.hidden = true;
  projectDetail.hidden = false;
  detailTitle.textContent = selectedProjectTitle;
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

async function setupProjectPlayback(assets, setupId, startOffset = 0, project = {}) {
  const stemOrder = ["vocals", "drums", "bass", "guitar", "piano", "other"];
  const stemLabels = STEM_LABELS;
  const stemsByName = new Map(assets.filter((asset) => asset.asset_type === "stem").map((asset) => [asset.stem_name, asset]));
  // Four-stem models have no guitar and piano rows.
  const stems = stemOrder.map((name) => stemsByName.get(name)).filter(Boolean);
  const heading = document.createElement("p");
  heading.className = "playback-message";
  if (!stems.length || stems.some((asset) => !asset.available || !["wav", "flac"].includes(asset.media_format))) {
    heading.textContent = "Для синхронного воспроизведения нужны все дорожки проекта.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
    return;
  }

  try {
    const { MixerSession, SynchronizedTransport, Metronome, StretchEngine, countInPlan, formatBpm, metronomeGain, rhythmFound, snapFaderPercent, tempoAt } = await import("./playback.mjs");
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
    let duration = 0;
    const formatTime = (seconds) => {
      const safe = Math.max(0, Math.floor(seconds || 0));
      return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
    };
    const updateTime = (seconds) => {
      const ratio = duration > 0 ? Math.min(1, Math.max(0, seconds / duration)) : 0;
      playbackPanel.style.setProperty("--progress", String(ratio));
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
    const bpmLabel = document.createElement("span");
    bpmLabel.className = "metronome-bpm";
    const showTempo = (seconds) => {
      const text = formatBpm(tempoAt(songTempo, seconds), metronome.rate);
      if (bpmLabel.textContent !== text) bpmLabel.textContent = text;  // changes at most every four beats
    };
    const transport = new SynchronizedTransport(tracks, {
      onTimeUpdate: (seconds) => {
        updateTime(seconds);
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
      name.textContent = stemLabels[asset.stem_name];
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
      drawWaveform(waveSvg, asset.asset_id);
      row.append(left, wave);
      mixerRows.append(row);
    });

    // The master row under the stems: 0-100 % for the whole mix, remembered for this project.
    const masterRow = document.createElement("div");
    masterRow.className = "mixer-master";  // not a .mixer-row: the stem rows are counted and selected by that class
    masterRow.style.gridRow = String(stems.length + 1);
    const masterLeft = document.createElement("div");
    masterLeft.className = "mixer-left";
    const masterHeading = document.createElement("div");
    masterHeading.className = "mixer-heading";
    const masterName = document.createElement("span");
    masterName.className = "mixer-name";
    masterName.textContent = "Общая";
    masterHeading.append(masterName);
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
    masterLeft.append(masterHeading, masterFader);
    masterRow.append(masterLeft, document.createElement("div"));
    mixerRows.append(masterRow);

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
    metronomeButton.className = "footer-toggle";
    metronomeButton.setAttribute("aria-pressed", "false");
    metronomeButton.innerHTML = `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${transportIcons.metronome}</svg><span>Метроном</span>`;
    const metronomeLabel = metronomeButton.querySelector("span");
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
    const speedLabel = document.createElement("span");
    speedLabel.className = "footer-speed-label";
    speedLabel.textContent = "Скорость";
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
    const speedValue = document.createElement("span");
    speedValue.className = "footer-speed-value";
    const scale = document.createElement("div");
    scale.className = "speed-scale";
    scale.setAttribute("aria-hidden", "true");
    scale.innerHTML = [25, 100, 150].map((value) => `<span style="left: ${((value - 25) / 125) * 100}%">${value}%</span>`).join("");
    speedFader.append(scale, speed);
    speedWrap.append(speedLabel, speedFader, speedValue);
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
    const showUpgrade = (state) => {
      const busy = state.status === "queued" || state.status === "running";
      upgradeButton.disabled = Boolean(state.best) || busy;
      if (busy) upgradeButton.dataset.analyzing = "true";
      else delete upgradeButton.dataset.analyzing;
      // Only while it runs: an inline value outlives the stylesheet's «not analysing → 0 %», so a project upgraded
      // here kept a full fill on its finished button while one separated with the best model had none.
      if (busy) upgradeButton.style.setProperty("--job-progress", `${Math.round((state.progress || 0) * 100)}%`);
      else upgradeButton.style.removeProperty("--job-progress");
      const left = state.eta_seconds !== null && state.eta_seconds !== undefined ? ` · осталось ${formatMinutes(state.eta_seconds)}` : "";
      if (state.best) upgradeButton.textContent = "Улучшенное качество разделения";
      else if (state.status === "queued") upgradeButton.textContent = `Улучшение в очереди${left}`;
      else if (state.status === "running") upgradeButton.textContent = `Улучшаем · ${Math.round(state.progress * 100)}%${left}`;
      else upgradeButton.textContent = `Улучшить качество${state.estimate_seconds ? ` · ${formatMinutes(state.estimate_seconds)}` : ""}`;
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

    footer.append(metronomeButton, bpmLabel, volumeWrap, upgradeButton, speedWrap);

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
          metronomeLabel.textContent = `Метроном · ${Math.round(state.progress * 100)}%`;
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          state = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}/beats`);
        }
        delete metronomeButton.dataset.analyzing;
        metronomeButton.style.removeProperty("--job-progress");
        metronomeLabel.textContent = "Метроном";
        if (state.status !== "done") throw new Error(state.error || "Не удалось определить доли");
        if (!rhythmFound(state.result)) {
          setMetronome(false);
          metronomeLabel.textContent = "Ритм не найден";
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
        metronomeLabel.textContent = "Метроном";
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
    detailControls.replaceChildren(buttons);
    playheadLayer.style.gridRow = `1 / span ${stems.length}`;
    mixerRows.append(playheadLayer);
    playbackPanel.style.setProperty("--progress", "0");
    playbackPanel.replaceChildren(error, mixerRows, footer, ...tracks);
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
      duration: () => duration,
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

function renderReleases(releases) {
  if (releases.length === 0) {
    releaseList.replaceChildren();
    updatesMessage.textContent = "В GitHub пока нет опубликованных стабильных релизов.";
    return;
  }
  updatesMessage.textContent = "Релизы получены из GitHub. Перед обновлением остановите worker.";
  releaseList.replaceChildren(...releases.map((release, index) => {
    const card = document.createElement("article");
    card.className = "release-card";
    const title = document.createElement("h3");
    title.textContent = `${release.name} (${release.tag_name})`;
    const published = document.createElement("p");
    published.textContent = release.published_at;
    const notes = document.createElement("p");
    notes.className = "release-notes";
    notes.textContent = release.notes || "В этом релизе нет описания.";
    const link = document.createElement("a");
    link.href = release.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Открыть релиз на GitHub";
    card.append(title, published, notes, link);
    if (index === 0 && release.updatable) {
      const apply = document.createElement("button");
      apply.type = "button";
      apply.className = "primary-button";
      apply.textContent = "Обновить зависимости";
      apply.addEventListener("click", async () => {
        const confirmed = window.confirm(`Установить обновления из requirements.txt для релиза ${release.tag_name}? Остановите worker перед продолжением. После установки перезапустите API и worker.`);
        if (!confirmed) return;
        apply.disabled = true;
        updatesMessage.textContent = "Устанавливаем зависимости…";
        try {
          await requestJson("/api/v1/updates/apply", {
            method: "POST",
            body: JSON.stringify({ tag_name: release.tag_name, confirmed: true }),
          });
          updatesMessage.textContent = "Зависимости обновлены. Перезапустите API и worker.";
        } catch (error) {
          updatesMessage.textContent = error.message;
          apply.disabled = false;
        }
      });
      card.append(apply);
    } else if (index === 0) {
      const unavailable = document.createElement("p");
      unavailable.textContent = "Для этого релиза нет проверенного manifest с точными версиями зависимостей.";
      card.append(unavailable);
    }
    return card;
  }));
}

async function checkUpdates() {
  checkUpdatesButton.disabled = true;
  updatesMessage.textContent = "Проверяем опубликованные релизы GitHub…";
  try {
    const result = await requestJson("/api/v1/updates");
    renderReleases(result.releases);
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
    setFormMessage(error.message, true);
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
  // Tab never walks the focus (and its focus rings) outside dialogs; in a project it picks the next track.
  if (event.key === "Tab" && searchOverlay.hidden && settingsOverlay.hidden && !deleteDialog.open && !isTextEntry(event.target)) {
    event.preventDefault();
    if (activeControls && !event.ctrlKey && !event.metaKey && !event.altKey) {
      activeControls.cycleSelection(event.shiftKey ? -1 : 1);
    }
    return;
  }
  if (!activeControls || !searchOverlay.hidden || !settingsOverlay.hidden || deleteDialog.open) return;
  if (event.ctrlKey || event.metaKey || event.altKey || isTextEntry(event.target)) return;
  const controls = activeControls;
  const actions = {
    ArrowLeft: () => controls.seekBy(-5),
    ArrowRight: () => controls.seekBy(5),
    Home: () => controls.seekTo(0),
    Backspace: () => controls.toStartAndPause(),  // the ⏮ button: back to the start, paused
    End: () => controls.seekTo(controls.duration()),
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
if (location.hash === "#settings") openSettings();
