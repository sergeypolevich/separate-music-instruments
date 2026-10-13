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
let pollingTimer = null;

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
  detailTitle.textContent = project.title || project.input_value;
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
  projectDetail.hidden = false;
}

async function openProject(projectId) {
  try {
    const project = await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`);
    renderProject(project);
  } catch (error) {
    setMessage(error.message, true);
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
