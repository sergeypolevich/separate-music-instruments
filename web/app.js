const stateLabels = {
  queued: "В очереди",
  searching: "Поиск источника",
  downloading: "Загрузка аудио",
  downloaded: "Аудио загружено",
  separating: "Разделение дорожек",
  completed: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};

const batchLabels = {
  queued: "В очереди",
  processing: "Выполняется",
  completed: "Готово",
  partial: "Завершено частично",
  failed: "Не удалось выполнить",
  cancelled: "Отменено",
};

const form = document.querySelector("#submit-form");
const inputField = document.querySelector("#inputs");
const modelField = document.querySelector("#model");
const deviceField = document.querySelector("#device");
const message = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const progressSection = document.querySelector("#progress-section");
const batchTitle = document.querySelector("#batch-title");
const batchStatus = document.querySelector("#batch-status");
const jobList = document.querySelector("#job-list");
const projectList = document.querySelector("#project-list");
const projectDetail = document.querySelector("#project-detail");
const detailTitle = document.querySelector("#detail-title");
const detailStatus = document.querySelector("#detail-status");
const detailInput = document.querySelector("#detail-input");
const detailModel = document.querySelector("#detail-model");
const assetList = document.querySelector("#asset-list");
const playbackPanel = document.querySelector("#playback-panel");
const deleteProjectButton = document.querySelector("#delete-project");
let pollingTimer = null;
let activeTransport = null;
let playbackSetupId = 0;

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error?.message || "Не удалось выполнить запрос");
  }
  return body;
}

function setMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("error", isError);
}

function createJobCard(job) {
  const card = document.createElement("article");
  card.className = "job-card";
  card.dataset.status = job.status;

  const row = document.createElement("div");
  row.className = "job-row";
  const title = document.createElement("span");
  title.className = "job-title";
  title.textContent = job.input_value || `Задание ${job.job_id.slice(0, 8)}`;
  const state = document.createElement("span");
  state.className = "job-status";
  state.dataset.status = job.status;
  state.textContent = stateLabels[job.status] || job.status;
  row.append(title, state);
  card.append(row);

  if (job.error_summary) {
    const error = document.createElement("p");
    error.className = "job-error";
    error.textContent = job.error_summary;
    card.append(error);
  }
  const track = document.createElement("div");
  track.className = "progress-track";
  const fill = document.createElement("div");
  fill.className = "progress-fill";
  track.append(fill);
  card.append(track);
  return card;
}

function renderBatch(batch) {
  batchTitle.textContent = `${batch.jobs.length} ${batch.jobs.length === 1 ? "задача" : "задачи"}`;
  batchStatus.dataset.status = batch.status;
  batchStatus.textContent = batchLabels[batch.status] || batch.status;
  jobList.replaceChildren(...batch.jobs.map(createJobCard));
  progressSection.hidden = false;
}

function renderProjects(projects) {
  if (projects.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Здесь появятся обработанные и текущие проекты.";
    projectList.replaceChildren(empty);
    return;
  }
  projectList.replaceChildren(...projects.map((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-card";
    button.setAttribute("aria-label", `Открыть проект ${project.title || project.input_value}`);
    const name = document.createElement("span");
    name.className = "project-name";
    name.textContent = project.title || project.input_value;
    const status = document.createElement("span");
    status.className = "project-state";
    status.textContent = stateLabels[project.job_status] || project.job_status;
    button.append(name, status);
    button.addEventListener("click", () => openProject(project.project_id));
    return button;
  }));
}

function assetLabel(asset) {
  if (asset.asset_type === "source_audio") return "Исходное аудио";
  return asset.stem_name ? `Стем: ${asset.stem_name}` : "Стем";
}

async function drawWaveform(canvas, assetId) {
  try {
    const waveform = await requestJson(
      `/api/v1/assets/${encodeURIComponent(assetId)}/waveform?levels=128`,
    );
    if (waveform.asset_id !== assetId) return;
    const context = canvas.getContext("2d");
    const center = canvas.height / 2;
    const step = canvas.width / Math.max(1, waveform.levels.length);
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#b8ef68";
    waveform.levels.forEach((peak, index) => {
      const height = Math.max(1, peak * (canvas.height - 2));
      context.fillRect(index * step, center - height / 2, Math.max(1, step - 1), height);
    });
  } catch (_error) {
    canvas.setAttribute("aria-label", "Форма волны недоступна");
  }
}

function renderProject(project) {
  playbackSetupId += 1;
  if (activeTransport) activeTransport.stop();
  activeTransport = null;
  playbackPanel.replaceChildren();
  playbackPanel.hidden = true;
  detailTitle.textContent = project.title || project.input_value;
  deleteProjectButton.dataset.projectId = project.project_id;
  detailStatus.dataset.status = project.job.status;
  detailStatus.textContent = stateLabels[project.job.status] || project.job.status;
  detailInput.textContent = project.input_value;
  const options = project.job.separation_options;
  detailModel.textContent = options
    ? `Модель: ${options.model_name} · ${options.device}`
    : "Параметры обработки не сохранены";
  assetList.replaceChildren(...project.assets.map((asset) => {
    const row = document.createElement("div");
    row.className = "asset-row";
    const name = document.createElement("span");
    name.textContent = assetLabel(asset);
    const availability = document.createElement("span");
    availability.className = asset.available ? "asset-available" : "asset-missing";
    availability.textContent = asset.available
      ? `${asset.media_format || "файл"} · ${Math.ceil(asset.byte_size / 1024)} КБ`
      : "Файл не найден";
    row.append(name);
    if (asset.available && asset.media_format === "wav") {
      const waveform = document.createElement("canvas");
      waveform.className = "waveform-preview";
      waveform.width = 320;
      waveform.height = 30;
      waveform.setAttribute("aria-label", `Форма волны: ${assetLabel(asset)}`);
      row.append(waveform);
      drawWaveform(waveform, asset.asset_id);
    }
    row.append(availability);
    return row;
  }));
  if (project.assets.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Файлы для этого проекта ещё не сохранены.";
    assetList.append(empty);
  }
  setupProjectPlayback(project.assets, playbackSetupId);
  projectDetail.hidden = false;
}

async function setupProjectPlayback(assets, setupId) {
  const stemOrder = ["vocals", "drums", "bass", "guitar", "piano", "other"];
  const stemsByName = new Map(assets
    .filter((asset) => asset.asset_type === "stem")
    .map((asset) => [asset.stem_name, asset]));
  const stems = stemOrder.map((name) => stemsByName.get(name));
  const heading = document.createElement("p");
  heading.className = "playback-message";
  if (stems.some((asset) => !asset || !asset.available || asset.media_format !== "wav")) {
    heading.textContent = "Для синхронного воспроизведения нужны все шесть доступных WAV-дорожек.";
    playbackPanel.append(heading);
    playbackPanel.hidden = false;
    return;
  }

  playbackPanel.hidden = false;
  try {
    const { MixerSession, SynchronizedTransport } = await import("/static/playback.mjs");
    // The project can change while the module is loading; avoid attaching stale audio.
    if (setupId !== playbackSetupId) return;
    const tracks = stems.map((asset) => {
      const audio = document.createElement("audio");
      audio.preload = "metadata";
      audio.src = `/api/v1/assets/${encodeURIComponent(asset.asset_id)}`;
      audio.setAttribute("aria-label", asset.stem_name);
      return audio;
    });
    const time = document.createElement("span");
    time.className = "playback-time";
    const seek = document.createElement("input");
    seek.type = "range";
    seek.min = "0";
    seek.max = "0";
    seek.step = "0.01";
    seek.value = "0";
    seek.setAttribute("aria-label", "Позиция воспроизведения");
    const formatTime = (seconds) => {
      const safe = Math.max(0, Math.floor(seconds || 0));
      return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
    };
    const updateTime = (seconds) => {
      seek.value = String(seconds);
      time.textContent = `${formatTime(seconds)} / ${formatTime(Number(seek.max))}`;
    };
    const transport = new SynchronizedTransport(tracks, { onTimeUpdate: updateTime });
    const mixer = new MixerSession(tracks);
    activeTransport = transport;
    const buttons = document.createElement("div");
    buttons.className = "playback-buttons";
    const play = document.createElement("button");
    play.type = "button";
    play.textContent = "Воспроизвести";
    const pause = document.createElement("button");
    pause.type = "button";
    pause.className = "quiet-button";
    pause.textContent = "Пауза";
    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "quiet-button";
    stop.textContent = "Стоп";
    const error = document.createElement("span");
    error.className = "playback-error";
    const mixerRows = document.createElement("div");
    mixerRows.className = "mixer-list";
    const stemLabels = {
      vocals: "Вокал", drums: "Ударные", bass: "Бас", guitar: "Гитара", piano: "Пианино", other: "Остальное",
    };
    stems.forEach((asset, index) => {
      const row = document.createElement("div");
      row.className = "mixer-row";
      const name = document.createElement("span");
      name.className = "mixer-name";
      name.textContent = stemLabels[asset.stem_name];
      const gain = document.createElement("input");
      gain.type = "range";
      gain.min = "0";
      gain.max = "1";
      gain.step = "0.01";
      gain.value = "1";
      gain.setAttribute("aria-label", `Громкость: ${stemLabels[asset.stem_name]}`);
      gain.addEventListener("input", () => mixer.setGain(index, Number(gain.value)));
      const muteLabel = document.createElement("label");
      muteLabel.className = "mixer-toggle";
      const mute = document.createElement("input");
      mute.type = "checkbox";
      mute.setAttribute("aria-label", `Заглушить: ${stemLabels[asset.stem_name]}`);
      mute.addEventListener("change", () => mixer.setMute(index, mute.checked));
      muteLabel.append(mute, document.createTextNode("Mute"));
      const soloLabel = document.createElement("label");
      soloLabel.className = "mixer-toggle";
      const solo = document.createElement("input");
      solo.type = "checkbox";
      solo.setAttribute("aria-label", `Оставить: ${stemLabels[asset.stem_name]}`);
      solo.addEventListener("change", () => mixer.setSolo(index, solo.checked));
      soloLabel.append(solo, document.createTextNode("Solo"));
      row.append(name, gain, muteLabel, soloLabel);
      mixerRows.append(row);
    });
    play.addEventListener("click", async () => {
      error.textContent = "";
      try { await transport.play(); } catch (_reason) {
        error.textContent = "Браузер не разрешил запустить все дорожки.";
      }
    });
    pause.addEventListener("click", () => transport.pause());
    stop.addEventListener("click", () => transport.stop());
    seek.addEventListener("input", () => transport.seek(Number(seek.value)));
    tracks.forEach((audio) => audio.addEventListener("loadedmetadata", () => {
      const duration = transport.duration;
      if (duration > 0) {
        seek.max = String(duration);
        updateTime(transport.tracks[0].currentTime);
      }
    }));
    buttons.append(play, pause, stop);
    playbackPanel.replaceChildren(heading, buttons, seek, time, error, mixerRows, ...tracks);
    playbackPanel.hidden = false;
  } catch (_error) {
    heading.textContent = "Не удалось подготовить синхронное воспроизведение.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
  }
}

async function openProject(projectId) {
  try {
    const project = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`);
    renderProject(project);
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function deleteProject() {
  const projectId = deleteProjectButton.dataset.projectId;
  if (!projectId || !window.confirm("Удалить проект, исходное аудио и сохранённые дорожки?")) return;
  deleteProjectButton.disabled = true;
  try {
    await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    if (activeTransport) activeTransport.stop();
    activeTransport = null;
    projectDetail.hidden = true;
    await loadProjects();
    setMessage("Проект удалён.");
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    deleteProjectButton.disabled = false;
  }
}

async function loadProjects() {
  try {
    const projects = await requestJson("/api/v1/projects");
    renderProjects(projects);
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function refreshBatch(batchId) {
  try {
    const batch = await requestJson(`/api/v1/batches/${encodeURIComponent(batchId)}`);
    renderBatch(batch);
    if (["queued", "processing"].includes(batch.status)) {
      pollingTimer = window.setTimeout(() => refreshBatch(batchId), 1200);
    } else {
      await loadProjects();
    }
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function loadModels() {
  try {
    const models = await requestJson("/api/v1/models");
    modelField.replaceChildren();
    for (const model of models) {
      const option = document.createElement("option");
      option.value = model.name;
      option.textContent = model.name;
      option.selected = Boolean(model.default);
      modelField.append(option);
    }
  } catch (error) {
    modelField.replaceChildren(new Option("Не удалось загрузить модели", ""));
    modelField.disabled = true;
    setMessage(error.message, true);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pollingTimer !== null) window.clearTimeout(pollingTimer);
  const inputs = inputField.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (inputs.length === 0) {
    setMessage("Введите название или ссылку.", true);
    return;
  }

  submitButton.disabled = true;
  setMessage("Добавляем в очередь…");
  try {
    const created = await requestJson("/api/v1/batches", {
      method: "POST",
      body: JSON.stringify({
        inputs,
        model_name: modelField.value,
        device: deviceField.value,
      }),
    });
    setMessage("Задача добавлена. Можно оставить эту страницу открытой.");
    await refreshBatch(created.batch_id);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    submitButton.disabled = false;
  }
});

loadModels();
loadProjects();
document.querySelector("#refresh-projects").addEventListener("click", loadProjects);
deleteProjectButton.addEventListener("click", deleteProject);
