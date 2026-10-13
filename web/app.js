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

async function refreshBatch(batchId) {
  try {
    const batch = await requestJson(`/api/v1/batches/${encodeURIComponent(batchId)}`);
    renderBatch(batch);
    if (["queued", "processing"].includes(batch.status)) {
      pollingTimer = window.setTimeout(() => refreshBatch(batchId), 1200);
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
