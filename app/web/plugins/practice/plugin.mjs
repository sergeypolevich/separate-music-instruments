// Practice (version 24): play your part along with the song. A target button in a stem row picks the stem you
// play (you mute it yourself with M); a lane over the stems shows 3 s around the playhead (at 70 %), the stem's
// peaks above and your audio input below, drawn as it arrives. The stems play through the decoded engine while
// practising, so a captured frame's AudioContext time maps to the song second heard then (ctx.songTimeAt);
// the input is drawn earlier by the measured latency (the latency tuner, Settings → Plugins → Practice).

export const SPAN = 3;  // seconds in the lane
export const PLAYHEAD = 0.7;  // where the playhead stands in it
const TOLERANCE = { min: 5, max: 40, fallback: 15 };  // ms

export function choice(settings = {}) {
  const tolerance = Number(settings.tolerance);
  return {
    input: typeof settings.input === "string" ? settings.input : "",
    tolerance: Number.isFinite(tolerance) ? Math.min(TOLERANCE.max, Math.max(TOLERANCE.min, tolerance)) : TOLERANCE.fallback,
    latency: settings.latency && typeof settings.latency === "object" ? settings.latency : {},
  };
}

// A block of input heard from song second `from` to `to` raises the peaks of the buckets it covers (-1: nothing yet).
export function addPeaks(peaks, bucket, from, to, samples) {
  const step = (to - from) / samples.length;
  let index = -1;
  for (let i = 0; i < samples.length; i++) {
    const at = Math.floor((from + i * step) / bucket);
    if (at < 0 || at >= peaks.length) continue;
    if (at !== index) {
      index = at;
      if (peaks[at] < 0) peaks[at] = 0;
    }
    const value = Math.abs(samples[i]);
    if (value > peaks[at]) peaks[at] = value;
  }
}

// The input and output a latency belongs to: another interface or headphones need their own measurement.
// Runs of input in song time: a block that follows the last one goes on its run, else (a seek) starts a new one.
export function addToRuns(runs, from, to, samples) {
  const last = runs[runs.length - 1];
  if (last && Math.abs(from - last.to) < 0.005) {
    last.chunks.push(samples);
    last.to = to;
  } else runs.push({ from, to, chunks: [samples] });
}

// The note starts of the take (song seconds) and the original's notes inside what was recorded (0.1 s in from the
// edges, where a note may be cut).
export function takeNotes(runs, reference) {
  const played = [], covered = [];
  for (const run of runs) {
    const length = run.chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const samples = new Float32Array(length);
    let at = 0;
    for (const chunk of run.chunks) { samples.set(chunk, at); at += chunk.length; }
    played.push(...onsets(samples, length / (run.to - run.from)).map((time) => run.from + time));
    covered.push(...reference.filter((time) => time >= run.from + 0.1 && time <= run.to - 0.1));
  }
  return { played: played.sort((a, b) => a - b), covered };
}

export function latencyKey(input, output) {
  return `${input || "default"}>${output || "default"}`;
}

import { openTuner } from "./tuner.mjs";
import { match, onsets, showScore } from "./score.mjs";

const TARGET = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>';
const captureModules = new WeakMap();  // AudioContext → the worklet module, added once

function stylesheet(parent, add) {
  return add(parent, Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/practice/plugin.css" }));
}

// The default output's device group: the system's output may change (speakers, headphones), and with it the latency.
async function outputGroup() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const output = devices.find((device) => device.kind === "audiooutput" && device.deviceId === "default") || devices.find((device) => device.kind === "audiooutput");
    return (output && output.groupId) || "";
  } catch (_error) {
    return "";
  }
}

export async function project(ctx) {
  stylesheet(document.head, ctx.add);
  const lane = ctx.addLane(document.createElement("div"));
  lane.className = "practice-lane";
  lane.hidden = true;
  const canvas = lane.appendChild(document.createElement("canvas"));
  canvas.setAttribute("aria-hidden", "true");
  const bar = lane.appendChild(document.createElement("div"));
  bar.className = "practice-bar";
  const record = bar.appendChild(document.createElement("button"));
  record.type = "button";
  record.className = "practice-record";
  record.dataset.action = "practice-record";
  record.setAttribute("aria-pressed", "false");
  record.textContent = ctx.t("Record");
  record.title = ctx.t("Record your input while the song plays");
  const measure = bar.appendChild(document.createElement("button"));
  measure.type = "button";
  measure.className = "practice-record practice-latency";
  measure.dataset.action = "practice-latency";
  const status = bar.appendChild(document.createElement("span"));
  status.className = "practice-status";
  status.setAttribute("aria-live", "polite");
  const youName = lane.appendChild(document.createElement("span"));
  youName.className = "practice-name";
  youName.style.top = "calc(50% + 4px)";
  youName.textContent = ctx.t("You");

  let settings = choice(ctx.settings);
  ctx.onSettings((values) => {
    settings = choice(values);
    if (measure.isConnected) showLatency();
  });
  const data = (await ctx.loadData()) || {};
  if (!ctx.current) return undefined;
  let stem = ctx.stems.findIndex((item) => item.name === data.stem);
  let levels = null, levelMax = 1, take = null, takeMax = 0.05, beats = [], downbeats = [];
  let capture = null;  // the recording's input: {key, close()}
  let measuredKey = null;  // the input and output of the last recording or measurement
  let runs = [];  // the take being recorded: [{from, to, chunks}]
  let reference = null;  // the chosen stem's note starts (a promise), found once
  const scores = Array.isArray(data.scores) ? data.scores : [];

  const buttons = ctx.stems.map((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mixer-toggle practice-target";
    button.dataset.control = "practice";
    button.innerHTML = TARGET;
    button.setAttribute("aria-pressed", "false");
    const label = ctx.t("Practice with {stem}", { stem: item.label });
    button.title = label;
    button.setAttribute("aria-label", label);
    ctx.addStemControl(index, button);
    ctx.on(button, "click", () => {
      const next = stem === index ? -1 : index;
      data.stem = next < 0 ? null : ctx.stems[next].name;
      ctx.saveData(data);
      choose(next);
    });
    return button;
  });

  async function choose(index) {
    stem = index;
    reference = null;
    runs = [];
    buttons.forEach((button, at) => button.setAttribute("aria-pressed", String(at === index)));
    lane.hidden = index < 0;
    levels = null;
    if (index < 0) {
      stopRecording();
      return;
    }
    const peaks = await ctx.levels(index);
    if (!ctx.current || stem !== index) return;
    levels = Float32Array.from(peaks);
    levelMax = Math.max(0.05, ...levels);
    take = new Float32Array(levels.length).fill(-1);
    takeMax = 0.05;
    youName.textContent = ctx.t("You");
    lane.querySelector(".practice-original")?.remove();
    const name = lane.appendChild(document.createElement("span"));
    name.className = "practice-name practice-original";
    name.style.top = "42px";
    name.textContent = ctx.stems[index].label;
  }

  // The song seconds a peak covers; the length is known only once the stems' metadata has loaded.
  const bucketSize = () => (levels && ctx.duration > 0 ? ctx.duration / levels.length : 0);
  const latency = () => (capture ? Number(settings.latency[capture.key]) || 0 : 0);

  function onBlock({ data: block }) {
    const bucket = bucketSize();
    if (!take || !bucket || !ctx.playing) return;
    const rate = ctx.audioContext.sampleRate, lag = latency();
    const from = ctx.songTimeAt(block.frame / rate - lag);
    const to = ctx.songTimeAt((block.frame + block.samples.length) / rate - lag);
    if (from === null || to === null || !(to > from) || to - from > 1) return;  // paused, a seek or the loop's jump back
    addPeaks(take, bucket, from, to, block.samples);
    addToRuns(runs, from, to, block.samples);
    for (const value of block.samples) if (Math.abs(value) > takeMax) takeMax = Math.abs(value);
  }

  // The audio input in blocks of {frame, samples} (capture.js) on the AudioContext the song plays on, without the
  // browser's echo cancellation, noise suppression and gain control (they delay and change the sound).
  async function openInput(onBlock) {
    const audio = ctx.audioContext;
    if (!captureModules.has(audio)) captureModules.set(audio, audio.audioWorklet.addModule("plugins/practice/capture.js"));
    await captureModules.get(audio);
    const stream = await navigator.mediaDevices.getUserMedia({ audio: {
      deviceId: settings.input ? { exact: settings.input } : undefined,
      echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1,
    } });
    const output = await outputGroup();
    const source = audio.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audio, "practice-capture");
    const sink = audio.createGain();  // silent: Chrome runs a worklet only while it leads to the output
    sink.gain.value = 0;
    source.connect(node).connect(sink).connect(audio.destination);
    node.port.onmessage = onBlock;
    const key = latencyKey(settings.input, output);
    measuredKey = key;
    showLatency();
    return {
      key,
      close() {
        node.port.onmessage = null;
        source.disconnect();
        node.disconnect();
        sink.disconnect();
        stream.getTracks().forEach((track) => track.stop());
      },
    };
  }

  async function startRecording() {
    record.setAttribute("aria-pressed", "true");
    status.textContent = ctx.t("Preparing…");
    try {
      if (!(await ctx.precise()) || !ctx.current) return;
      const index = stem;
      reference = reference || ctx.stemAudio(index).then(({ samples, sampleRate }) => onsets(samples, sampleRate));
      reference.catch(() => { if (stem === index) reference = null; });
      const opened = await openInput(onBlock);
      if (!ctx.current || record.getAttribute("aria-pressed") !== "true") {
        opened.close();
        return;
      }
      capture = opened;
      showStatus();
    } catch (error) {
      record.setAttribute("aria-pressed", "false");
      status.textContent = error && error.name === "NotAllowedError"
        ? ctx.t("The browser did not allow the audio input")
        : ctx.t("The audio input did not start: {error}", { error: (error && error.message) || error });
    }
  }

  function stopRecording() {
    record.setAttribute("aria-pressed", "false");
    if (capture) capture.close();
    capture = null;
    status.textContent = "";
    finishTake();
  }

  // A take ends when the song stops or recording is turned off: its score, kept with the last 20.
  async function finishTake() {
    const recorded = runs, chosen = stem, tolerance = settings.tolerance / 1000;
    runs = [];
    if (!recorded.length || !reference || chosen < 0) return;
    let notes;
    try {
      notes = takeNotes(recorded, await reference);
    } catch (_error) {
      return;  // the stem could not be decoded: no score, the recording still showed
    }
    if (!ctx.current || notes.covered.length < 4) return;  // too short to say anything
    const offsets = match(notes.covered, notes.played);
    const history = scores.slice(-5);
    const result = showScore(ctx, { reference: notes.covered, offsets, tolerance, history,
      from: recorded[0].from, to: recorded[recorded.length - 1].to });
    scores.push({ at: new Date().toISOString(), stem: ctx.stems[chosen].name, tolerance: settings.tolerance,
      ...Object.fromEntries(Object.entries(result).map(([key, value]) => [key, Math.round(value * 10000) / 10000])) });
    data.scores = scores.slice(-20);
    ctx.saveData(data);
  }
  let wasPlaying = false;

  function showStatus() {
    if (!capture) return;
    status.textContent = settings.latency[capture.key] === undefined ? ctx.t("Latency not measured") : "";
  }

  // The latency of the input and output in use (known after the first recording or measurement).
  function showLatency() {
    const value = measuredKey && settings.latency[measuredKey];
    measure.textContent = value
      ? ctx.t("Latency {latency}", { latency: `${ctx.formatDecimal(Math.round(value * 10000) / 10)} ms` })
      : ctx.t("Measure latency");
    measure.title = ctx.t("Measure the delay of your input and output");
  }
  showLatency();
  outputGroup().then((output) => {  // the key before any recording: the chosen input and the system's output now
    if (measuredKey === null && ctx.current) measuredKey = latencyKey(settings.input, output);
    showLatency();
  });
  ctx.on(measure, "click", () => {
    stopRecording();
    openTuner(ctx, {
      openInput,
      known: measuredKey ? settings.latency[measuredKey] : null,
      save: (key, seconds) => {
        settings.latency = { ...settings.latency, [key]: Math.round(seconds * 100000) / 100000 };
        ctx.saveSettings({ ...settings });
        showLatency();
      },
    });
  });

  ctx.on(record, "click", () => (record.getAttribute("aria-pressed") === "true" ? stopRecording() : startRecording()));

  ctx.beats().then((result) => {
    beats = (result && result.beats) || [];
    downbeats = (result && result.downbeats) || [];
  }).catch(() => {});  // no beat lines without the analysis

  // Drawing: one frame per screen refresh, only while the lane shows; about a hundred bars a lane.
  const context2d = canvas.getContext("2d");
  let frame = 0;
  const color = (name) => getComputedStyle(lane).getPropertyValue(name).trim();
  function draw() {
    frame = requestAnimationFrame(draw);
    if (wasPlaying && !ctx.playing) finishTake();  // a pause ends the take
    wasPlaying = ctx.playing;
    const bucket = bucketSize();
    if (lane.hidden || !bucket) return;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.round(canvas.clientWidth * dpr), height = Math.round(canvas.clientHeight * dpr);
    if (!width || !height) return;
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const now = ctx.position, start = now - PLAYHEAD * SPAN, x = (time) => ((time - start) / SPAN) * width;
    const half = height / 2;
    context2d.clearRect(0, 0, width, height);
    const line = color("--line"), strong = color("--line-strong");
    const bars = new Set(downbeats.map((time) => Math.round(time * 1000)));
    for (const time of beats) {
      if (time < start || time > start + SPAN) continue;
      const bold = bars.has(Math.round(time * 1000));
      context2d.fillStyle = bold ? strong : line;
      context2d.fillRect(Math.round(x(time)), 0, Math.max(1, Math.round((bold ? 2 : 1) * dpr)), height);
    }
    context2d.fillStyle = line;
    context2d.fillRect(0, Math.round(half), width, Math.max(1, Math.round(dpr)));
    const lanePeaks = (peaks, top, scale, fill) => {
      context2d.fillStyle = fill;
      const mid = top + half / 2, room = half * 0.42, barWidth = Math.max(1, (bucket / SPAN) * width - dpr);
      const first = Math.max(0, Math.floor(start / bucket)), last = Math.min(peaks.length - 1, Math.ceil((start + SPAN) / bucket));
      for (let i = first; i <= last; i++) {
        if (peaks[i] < 0) continue;
        const size = Math.max(dpr, Math.min(1, peaks[i] / scale) * room);
        context2d.fillRect(x(i * bucket), mid - size, barWidth, size * 2);
      }
    };
    lanePeaks(levels, 0, levelMax, color("--accent"));
    lanePeaks(take, half, takeMax, color("--warn"));
    context2d.fillStyle = color("--text");
    context2d.fillRect(Math.round(x(now)), 0, Math.max(1, Math.round(dpr)), height);
  }
  frame = requestAnimationFrame(draw);

  if (stem >= 0) choose(stem);
  return () => {
    cancelAnimationFrame(frame);
    stopRecording();
  };
}

export function settings(panel, api) {
  const value = choice(api.values);
  const inputLabel = document.createElement("label");
  inputLabel.className = "plugin-setting";
  const inputName = document.createElement("span");
  inputName.textContent = api.t("Audio input");
  const select = document.createElement("select");
  select.dataset.setting = "input";
  select.setAttribute("aria-label", api.t("Audio input"));
  select.append(Object.assign(document.createElement("option"), { value: "", textContent: api.t("Default input") }));
  select.style.gridColumn = "1 / -1";
  inputLabel.append(inputName, select);
  navigator.mediaDevices?.enumerateDevices?.().then((devices) => {
    devices.filter((device) => device.kind === "audioinput" && device.deviceId && device.deviceId !== "default")
      .forEach((device, index) => select.append(Object.assign(document.createElement("option"), {
        value: device.deviceId, textContent: device.label || api.t("Input {number}", { number: index + 1 }) })));
    select.value = value.input;
    if (select.value !== value.input) select.value = "";
  }).catch(() => {});
  select.addEventListener("change", () => {
    value.input = select.value;
    api.save(value);
  });

  const tolerance = document.createElement("label");
  tolerance.className = "plugin-setting";
  const toleranceName = document.createElement("span");
  toleranceName.textContent = api.t("On time within");
  const toleranceValue = document.createElement("output");
  const slider = Object.assign(document.createElement("input"), { type: "range", min: TOLERANCE.min, max: TOLERANCE.max, step: 1, value: value.tolerance });
  slider.dataset.setting = "tolerance";
  slider.setAttribute("aria-label", api.t("On time within"));
  const showTolerance = () => { toleranceValue.textContent = `±${value.tolerance} ms`; };
  slider.addEventListener("input", () => {
    value.tolerance = Number(slider.value);
    showTolerance();
    api.save(value);
  });
  showTolerance();
  tolerance.append(toleranceName, toleranceValue, slider);
  panel.append(inputLabel, tolerance);
}
