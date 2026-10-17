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
const modelField = document.querySelector("#model");
const formMessage = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const searchOverlay = document.querySelector("#search-overlay");
const searchTrigger = document.querySelector("#search-trigger");
const projectTabs = document.querySelector("#project-tabs");
const projectWorkspace = document.querySelector("#project-workspace");
const projectEmpty = document.querySelector("#project-empty");
const projectDetail = document.querySelector("#project-detail");
const detailTitle = document.querySelector("#detail-title");
const detailInput = document.querySelector("#detail-input");
const detailError = document.querySelector("#detail-error");
const detailModel = document.querySelector("#detail-model");
const assetList = document.querySelector("#asset-list");
const playbackPanel = document.querySelector("#playback-panel");
const deleteProjectButton = document.querySelector("#delete-project");
const deleteDialog = document.querySelector("#delete-dialog");
const deleteDescription = document.querySelector("#delete-description");
const deleteError = document.querySelector("#delete-error");
const confirmDeleteButton = document.querySelector("#confirm-delete");
const updatesMessage = document.querySelector("#updates-message");
const releaseList = document.querySelector("#release-list");
const checkUpdatesButton = document.querySelector("#check-updates");
const screens = new Map([
  ["project", document.querySelector("#screen-project")],
  ["settings", document.querySelector("#screen-settings")],
  ["help", document.querySelector("#screen-help")],
]);
let pollingTimer = null;
let activeTransport = null;
let activeMixer = null;
let activeAudioContext = null;
let playbackSetupId = 0;
let selectedProjectId = null;
let selectedProjectTitle = "";

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

function activateScreen(name) {
  for (const [screenName, screen] of screens) screen.hidden = screenName !== name;
  document.querySelectorAll("[data-screen]").forEach((link) => {
    if (link.matches("nav a")) {
      if (link.dataset.screen === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  });
  if (name === "project") loadProjects();
}

function statusBucket(status) {
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  return status;
}

function renderProjects(projects) {
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
    const nameText = project.title || project.input_value;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-tab";
    button.dataset.projectId = project.project_id;
    button.dataset.jobId = project.job_id;
    button.dataset.status = statusBucket(project.job_status);
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(project.project_id === selectedProjectId));
    button.setAttribute("aria-label", `${nameText}. ${stateDescriptions[project.job_status] || project.job_status}`);
    button.title = `${nameText} — ${stateDescriptions[project.job_status] || project.job_status}`;
    const name = document.createElement("span");
    name.className = "project-tab-name";
    name.textContent = nameText;
    button.append(name);
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
    const waveform = await requestJson(`/api/v1/assets/${encodeURIComponent(assetId)}/waveform?levels=256`);
    if (waveform.asset_id !== assetId) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const center = canvas.height / 2;
    const step = canvas.width / Math.max(1, waveform.levels.length);
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#b8ef68";
    waveform.levels.forEach((peak, index) => {
      const height = Math.max(1, peak * (canvas.height - 4));
      context.fillRect(index * step, center - height / 2, Math.max(1, step - 1), height);
    });
  } catch (_error) {
    canvas.setAttribute("aria-label", "Форма волны недоступна");
  }
}

function cleanupPlayback() {
  playbackSetupId += 1;
  if (activeTransport) activeTransport.stop();
  activeTransport = null;
  activeMixer = null;
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
}

function renderAssets(assets) {
  const rows = assets.map((asset) => {
    const row = document.createElement("div");
    row.className = "asset-row";
    const name = document.createElement("span");
    name.textContent = assetLabel(asset);
    const value = document.createElement("span");
    value.className = asset.available ? "asset-available" : "asset-missing";
    value.textContent = asset.available
      ? `${asset.media_format || "файл"} · ${Math.ceil(asset.byte_size / 1024)} КБ`
      : "Файл не найден";
    row.append(name, value);
    if (asset.available) {
      const link = document.createElement("a");
      link.href = `/api/v1/assets/${encodeURIComponent(asset.asset_id)}`;
      link.textContent = "Открыть";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(link);
    }
    return row;
  });
  assetList.replaceChildren(...rows);
  if (assets.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Файлы этого проекта пока не сохранены.";
    assetList.append(empty);
  }
}

function renderProject(project) {
  cleanupPlayback();
  selectedProjectId = project.project_id;
  selectedProjectTitle = project.title || project.input_value;
  projectEmpty.hidden = true;
  projectDetail.hidden = false;
  detailTitle.textContent = selectedProjectTitle;
  detailInput.textContent = project.input_value;
  detailError.textContent = project.job.error_summary || "";
  detailError.hidden = !project.job.error_summary;
  const options = project.job.separation_options;
  detailModel.textContent = options ? `Модель: ${options.model_name} · ${options.device}` : "";
  deleteProjectButton.dataset.projectId = project.project_id;
  renderAssets(project.assets);
  setupProjectPlayback(project.assets, ++playbackSetupId);
  document.querySelectorAll(".project-tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.projectId === project.project_id));
  });
}

async function setupProjectPlayback(assets, setupId) {
  const stemOrder = ["vocals", "drums", "bass", "guitar", "piano", "other"];
  const stemLabels = { vocals: "Vocal", drums: "Drums", bass: "Bass", guitar: "Guitar", piano: "Piano", other: "Other" };
  const stemsByName = new Map(assets.filter((asset) => asset.asset_type === "stem").map((asset) => [asset.stem_name, asset]));
  const stems = stemOrder.map((name) => stemsByName.get(name));
  const heading = document.createElement("p");
  heading.className = "playback-message";
  if (stems.some((asset) => !asset || !asset.available || asset.media_format !== "wav")) {
    heading.textContent = "Для синхронного воспроизведения нужны все шесть доступных WAV-дорожек.";
    playbackPanel.replaceChildren(heading);
    playbackPanel.hidden = false;
    return;
  }

  try {
    const { MixerSession, SynchronizedTransport, snapFaderPercent } = await import("./playback.mjs");
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
    const audioContext = new AudioContextClass();
    const gainNodes = tracks.map((audio) => {
      const source = audioContext.createMediaElementSource(audio);
      const gain = audioContext.createGain();
      source.connect(gain);
      gain.connect(audioContext.destination);
      audio.volume = 1;
      audio.dataset.effectiveGain = "1";
      return gain;
    });
    activeAudioContext = audioContext;
    const mixer = new MixerSession(gainNodes, (values) => {
      tracks.forEach((audio, index) => { audio.dataset.effectiveGain = String(values[index]); });
    });
    activeMixer = mixer;

    const time = document.createElement("span");
    time.className = "playback-time";
    const seek = document.createElement("input");
    seek.type = "range";
    seek.min = "0";
    seek.max = "0";
    seek.step = "0.01";
    seek.value = "0";
    seek.dataset.control = "seek";
    seek.setAttribute("aria-label", "Позиция воспроизведения");
    const formatTime = (seconds) => {
      const safe = Math.max(0, Math.floor(seconds || 0));
      return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
    };
    const updateTime = (seconds) => {
      seek.value = String(seconds);
      time.textContent = `${formatTime(seconds)} / ${formatTime(Number(seek.max))}`;
    };
    const play = document.createElement("button");
    play.type = "button";
    play.className = "transport-button";
    play.dataset.action = "toggle";
    play.textContent = "▶";
    play.setAttribute("aria-label", "Воспроизвести");
    play.title = "Воспроизвести";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "transport-button";
    reset.dataset.action = "reset";
    reset.textContent = "|◀";
    reset.setAttribute("aria-label", "В начало и пауза");
    reset.title = "В начало и пауза";
    const buttons = document.createElement("div");
    buttons.className = "playback-buttons";
    buttons.append(play, reset);
    const error = document.createElement("span");
    error.className = "playback-error";
    const mixerRows = document.createElement("div");
    mixerRows.className = "mixer-list";

    const updateTransportButton = (isPlaying) => {
      play.textContent = isPlaying ? "Ⅱ" : "▶";
      play.setAttribute("aria-label", isPlaying ? "Пауза" : "Воспроизвести");
      play.title = isPlaying ? "Пауза" : "Воспроизвести";
    };
    const transport = new SynchronizedTransport(tracks, {
      onTimeUpdate: updateTime,
      onEnded: () => updateTransportButton(false),
      beforePlay: () => audioContext.resume(),
    });
    activeTransport = transport;

    stems.forEach((asset, index) => {
      const row = document.createElement("div");
      row.className = "mixer-row";
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
      gain.dataset.control = "gain";
      gain.setAttribute("aria-label", `Громкость ${stemLabels[asset.stem_name]}`);
      const gainValue = document.createElement("span");
      gainValue.className = "gain-value";
      gainValue.textContent = "100%";
      gain.addEventListener("input", () => {
        const percent = snapFaderPercent(Number(gain.value));
        gain.value = String(percent);
        gainValue.textContent = `${percent}%`;
        mixer.setGain(index, percent / 100);
      });
      fader.append(gain, gainValue);
      left.append(name, switches, fader);
      const wave = document.createElement("div");
      wave.className = "mixer-waveform";
      const canvas = document.createElement("canvas");
      canvas.className = "waveform-preview";
      canvas.width = 1200;
      canvas.height = 64;
      canvas.setAttribute("aria-label", `Форма волны: ${stemLabels[asset.stem_name]}`);
      wave.append(canvas);
      drawWaveform(canvas, asset.asset_id);
      row.append(left, wave);
      mixerRows.append(row);
    });

    play.addEventListener("click", async () => {
      error.textContent = "";
      if (transport.playing) {
        transport.pause();
        updateTransportButton(false);
        return;
      }
      try {
        await transport.play();
        updateTransportButton(true);
      } catch (_reason) {
        updateTransportButton(false);
        error.textContent = "Браузер не разрешил запустить все дорожки. Нажмите ▶ ещё раз.";
      }
    });
    reset.addEventListener("click", () => {
      transport.stop();
      updateTransportButton(false);
    });
    seek.addEventListener("input", () => transport.seek(Number(seek.value)));
    tracks.forEach((audio) => audio.addEventListener("loadedmetadata", () => {
      const duration = transport.duration;
      if (duration > 0) {
        seek.max = String(duration);
        updateTime(transport.tracks[0].currentTime);
      }
    }));
    const toolbar = document.createElement("div");
    toolbar.className = "playback-toolbar";
    toolbar.append(buttons, seek, time);
    playbackPanel.replaceChildren(toolbar, error, mixerRows, ...tracks);
    playbackPanel.hidden = false;
    tracks.forEach((audio) => audio.load());
  } catch (_error) {
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
      if (matching) await openProject(matching.dataset.projectId);
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

async function loadModels() {
  try {
    const models = await requestJson("/api/v1/models");
    modelField.replaceChildren(...models.map((model) => {
      const option = document.createElement("option");
      option.value = model.name;
      option.textContent = model.name;
      option.selected = Boolean(model.default);
      return option;
    }));
  } catch (error) {
    modelField.replaceChildren(new Option("Не удалось загрузить модели", ""));
    modelField.disabled = true;
    setFormMessage(error.message, true);
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
      body: JSON.stringify({ inputs, model_name: modelField.value }),
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

async function confirmProjectDeletion() {
  const projectId = deleteProjectButton.dataset.projectId;
  if (!projectId) return;
  confirmDeleteButton.disabled = true;
  deleteError.hidden = true;
  try {
    await requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
    deleteDialog.close("deleted");
    if (activeTransport) activeTransport.stop();
    cleanupPlayback();
    selectedProjectId = null;
    selectedProjectTitle = "";
    await loadProjects();
  } catch (error) {
    deleteError.textContent = error.message;
    deleteError.hidden = false;
  } finally {
    confirmDeleteButton.disabled = false;
  }
}

searchTrigger.addEventListener("click", openSearch);
document.querySelector("#close-search").addEventListener("click", () => closeSearch());
searchOverlay.addEventListener("click", (event) => {
  if (event.target === searchOverlay) closeSearch();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !searchOverlay.hidden) closeSearch();
});
document.querySelectorAll("[data-screen]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    const name = link.dataset.screen;
    activateScreen(name);
    if (name === "project") history.replaceState(null, "", "#project");
    else history.replaceState(null, "", `#${name}`);
  });
});
form.addEventListener("submit", submitSearch);
inputField.addEventListener("input", () => {
  inputField.style.height = "auto";
  inputField.style.height = `${Math.min(inputField.scrollHeight, window.innerHeight * 0.4)}px`;
});
deleteProjectButton.addEventListener("click", () => {
  deleteDescription.textContent = `Проект «${selectedProjectTitle}», исходное аудио и все дорожки будут удалены.`;
  deleteError.hidden = true;
  deleteDialog.showModal();
});
confirmDeleteButton.addEventListener("click", confirmProjectDeletion);
document.querySelector("#cancel-delete").addEventListener("click", () => deleteDialog.close("cancel"));
document.querySelector("#refresh-projects").addEventListener("click", () => loadProjects());
checkUpdatesButton.addEventListener("click", checkUpdates);

const initialScreen = location.hash.slice(1);
activateScreen(screens.has(initialScreen) ? initialScreen : "project");
loadModels();
