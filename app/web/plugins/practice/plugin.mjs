// Practice (version 24): play your part along with the song. A target button in a stem row picks the stem you
// play (you mute it yourself with M); a mixer row «You» under that stem (ctx.addTrack) shows your audio input on
// the stems' time scale, drawn as it arrives, and plays your last take through its own volume, S, M and EQ.
// The stems play through the decoded engine while practising, so a captured frame's AudioContext time maps to the
// song second heard then (ctx.songTimeAt); the input is placed earlier by the measured latency (the latency tuner).

export const PEAK = 0.001;  // the take's peaks: one a millisecond of song
export const COLUMN = 2;  // screen pixels between points of the take's outline (version 26: bars of 1 or 4 looked busy or coarse)

// A latency per input and output name; lastLatency, the last measured or set, for a pair never measured.
export function choice(settings = {}) {
  const latency = settings.latency && typeof settings.latency === "object" ? settings.latency : {};
  const last = Number.isFinite(settings.lastLatency) ? settings.lastLatency : null;
  const old = Object.values(latency).filter(Number.isFinite);
  return {
    input: typeof settings.input === "string" ? settings.input : "",
    // before version 26 the keys held the output's group, a new number each session: only the last value is kept
    latency: last === null ? {} : latency,
    lastLatency: last === null && old.length ? old[old.length - 1] : last,
  };
}

export function latencyOf(settings, key) {
  return Number.isFinite(settings.latency[key]) ? settings.latency[key] : settings.lastLatency;
}

// The song seconds a captured block covers: it was heard `lag` (the measured latency) before the AudioContext saw it.
// null when the song was not playing on (paused, a seek or the loop's jump back).
export function blockTimes(frame, length, rate, lag, songTimeAt) {
  const from = songTimeAt(frame / rate - lag), to = songTimeAt((frame + length) / rate - lag);
  return from === null || to === null || !(to > from) || to - from > 1 ? null : { from, to };
}

// The loudest peak under each of `width` columns showing song seconds view.start to view.end (-1: nothing heard).
export function columnPeaks(peaks, step, view, width) {
  const out = new Float32Array(width).fill(-1), perColumn = (view.end - view.start) / width;
  for (let x = 0; x < width; x++) {
    const from = Math.max(0, Math.floor((view.start + x * perColumn) / step));
    const to = Math.min(peaks.length, Math.max(from + 1, Math.ceil((view.start + (x + 1) * perColumn) / step)));
    for (let i = from; i < to; i++) if (peaks[i] > out[x]) out[x] = peaks[i];
  }
  return out;
}

// The take's outline: a filled shape, symmetric around `mid`, through one point every `step` pixels (columns[x] its
// height), smooth between them by quadratic curves through the midpoints; a column with nothing heard (-1) breaks it.
export function outline(context2d, columns, step, mid, size) {
  let run = [];
  const close = () => {
    if (!run.length) return;
    const half = (i) => size(run[i][1]);
    context2d.beginPath();
    context2d.moveTo(run[0][0], mid - half(0));
    for (let i = 1; i < run.length; i++) context2d.quadraticCurveTo(run[i - 1][0], mid - half(i - 1), (run[i - 1][0] + run[i][0]) / 2, mid - (half(i - 1) + half(i)) / 2);
    const end = run.length - 1;
    context2d.lineTo(run[end][0], mid - half(end));
    context2d.lineTo(run[end][0], mid + half(end));
    for (let i = end; i > 0; i--) context2d.quadraticCurveTo(run[i][0], mid + half(i), (run[i][0] + run[i - 1][0]) / 2, mid + (half(i) + half(i - 1)) / 2);
    context2d.lineTo(run[0][0], mid + half(0));
    context2d.fill();
    run = [];
  };
  for (let x = 0; x < columns.length; x++) {
    if (columns[x] < 0) close();
    else run.push([x * step + step / 2, columns[x]]);
  }
  close();
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

// Runs of input in song time: a block that follows the last one goes on its run, else (a seek) starts a new one.
export function addToRuns(runs, from, to, samples) {
  const last = runs[runs.length - 1];
  if (last && Math.abs(from - last.to) < 0.005) {
    last.chunks.push(samples);
    last.to = to;
  } else runs.push({ from, to, chunks: [samples] });
}

// A run's samples in one array (a loaded take has them already).
export function samplesOf(run) {
  if (run.samples) return run.samples;
  const samples = new Float32Array(run.chunks.reduce((sum, chunk) => sum + chunk.length, 0));
  let at = 0;
  for (const chunk of run.chunks) { samples.set(chunk, at); at += chunk.length; }
  return samples;
}

// A take's run as a 16-bit mono WAV file, and back.
export function wavBytes(samples, rate) {
  const bytes = new ArrayBuffer(44 + samples.length * 2), view = new DataView(bytes);
  const text = (at, value) => [...value].forEach((char, i) => view.setUint8(at + i, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, i) => view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, value)) * 32767), true));
  return bytes;
}

export function wavSamples(bytes) {
  const view = new DataView(bytes), count = Math.floor((bytes.byteLength - 44) / 2), samples = new Float32Array(count);
  for (let i = 0; i < count; i++) samples[i] = view.getInt16(44 + i * 2, true) / 32767;
  return samples;
}

// The input and output a latency belongs to: another interface or headphones need their own measurement.
export function latencyKey(input, output) {
  return `${input || "default"}>${output || "default"}`;
}

import { openTuner } from "./tuner.mjs";

const TARGET = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>';
const ICONS = {
  record: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="6" fill="currentColor"/></svg>',
  latency: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 2h4M12 14l3-3"/><circle cx="12" cy="14" r="8"/></svg>',
  save: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 15V3M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5"/></svg>',
};
const captureModules = new WeakMap();  // AudioContext → the worklet module, added once
let openStreams = 0;  // inputs this plugin holds open now: a second one on the same device may fail

// After a failed start, what does open (each closed at once), so the log tells the device from the settings asked.
async function probeInputs(input) {
  const tries = [["same_input_plain", input ? { deviceId: { exact: input } } : true], ["default_plain", true]];
  const results = {};
  for (const [name, audio] of tries) {
    const started = performance.now();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio });
      const track = stream.getAudioTracks()[0];
      results[name] = { ok: true, label: track ? track.label : null, ms: Math.round(performance.now() - started),
        rate: (track && track.getSettings().sampleRate) || null };
      stream.getTracks().forEach((item) => item.stop());
    } catch (error) {
      results[name] = { ok: false, error: String((error && error.name) || "Error"), message: String((error && error.message) || error) };
    }
  }
  return results;
}

// The microphone permission as the browser has it now: granted, prompt (it will ask), denied, or unknown.
async function micPermission() {
  try {
    return (await navigator.permissions.query({ name: "microphone" })).state;
  } catch (_error) {
    return "unknown";
  }
}

// The inputs the browser lists; without the permission it hides their names (and often all but the default).
async function inputDevices() {
  try {
    return (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "audioinput")
      .map((device) => ({ id: device.deviceId.slice(0, 8), label: device.label || null, group: device.groupId.slice(0, 8) }));
  } catch (error) {
    return String((error && error.name) || error);
  }
}

// The audio engine as it is: a suspended one plays nothing and its clock stands still.
const engineState = (audio) => ({ state: audio.state, rate: audio.sampleRate, time: Math.round(audio.currentTime * 1000) / 1000,
  base_ms: Math.round((audio.baseLatency || 0) * 10000) / 10, output_ms: Math.round((audio.outputLatency || 0) * 10000) / 10 });

function stylesheet(parent, add) {
  return add(parent, Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/practice/plugin.css" }));
}

// The chosen input's and the system's output's names: the latency changes with them (speakers, headphones), and
// unlike the device and group ids they stay the same from one session to the next.
async function deviceKey(input) {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const name = (kind, id) => {
      const device = devices.find((item) => item.kind === kind && item.deviceId === id) || devices.find((item) => item.kind === kind);
      return (device && device.label) || "";
    };
    return latencyKey(name("audioinput", input || "default"), name("audiooutput", "default"));
  } catch (_error) {
    return latencyKey("", "");
  }
}

export async function project(ctx) {
  stylesheet(document.head, ctx.add);
  const track = ctx.addTrack({ name: "practice", label: ctx.t("You") });  // under the chosen stem
  track.place(-1);
  const lane = track.wave;
  lane.classList.add("practice-lane");
  const canvas = lane.appendChild(document.createElement("canvas"));
  canvas.setAttribute("aria-hidden", "true");
  track.left.classList.add("practice-left");
  const bar = track.left.insertBefore(document.createElement("div"), track.left.firstChild);  // a line over the name, S, M and EQ
  bar.className = "practice-bar";
  const record = bar.appendChild(document.createElement("button"));
  record.type = "button";
  record.className = "practice-record";
  record.dataset.action = "practice-record";
  record.setAttribute("aria-pressed", "false");
  record.innerHTML = ICONS.record;  // icons, the names for screen readers and in the tooltip
  record.title = ctx.t("Record your input while the song plays");
  record.setAttribute("aria-label", ctx.t("Record"));
  const measure = bar.appendChild(document.createElement("button"));
  measure.type = "button";
  measure.className = "practice-record practice-latency";
  measure.dataset.action = "practice-latency";
  // Version 26: one button in the take's waveform's top right corner, once there is a take, saves two FLAC files in
  // Music/Exports/ under one number: the song with the take as heard, and the take alone as recorded.
  const corner = lane.appendChild(document.createElement("div"));
  corner.className = "practice-exports";
  const keep = corner.appendChild(document.createElement("button"));
  keep.type = "button";
  keep.className = "practice-record";
  keep.dataset.action = "practice-save";
  keep.innerHTML = ICONS.save;
  keep.title = ctx.t("Save the song with your take, and your take alone as recorded, as FLAC files");
  keep.setAttribute("aria-label", ctx.t("Save as FLAC"));
  const haveTake = (has) => {
    corner.hidden = !has;
    // the take plays along like a stem (its M silences it), on the song's clock: the decoded engine
    if (has) ctx.precise().catch((error) => ctx.log("precise_failed", { message: String((error && error.message) || error) }, "warning"));
  };
  haveTake(false);
  const status = lane.appendChild(document.createElement("span"));  // over the waveform, only while it says something
  status.className = "practice-status";
  status.setAttribute("aria-live", "polite");

  let settings = choice(ctx.settings);
  ctx.onSettings((values) => {
    settings = choice(values);
    if (measure.isConnected) showLatency();
  });
  const data = (await ctx.loadData()) || {};
  if (!ctx.current) return undefined;
  let stem = ctx.stems.findIndex((item) => item.name === data.stem);
  let take = null, takeMax = 0.05;  // the take's peaks (PEAK apart, made once the song's length is known)
  let capture = null;  // the recording's input: {key, close()}
  let measuredKey = null;  // the input and output of the last recording or measurement
  let runs = [];  // the take being recorded: [{from, to, chunks}]
  const takes = Array.isArray(data.takes) ? data.takes : [];  // saved: [{stem, at, runs: [{file, from, to}]}]
  let lastRuns = [];  // the last take of the chosen stem, to hear again: [{from, to, samples}]
  let shownRuns = null;  // the runs whose peaks the lane shows
  let takeLatency = 0, recordedLatency = 0;  // the latency the shown take was placed with; the recording's
  let player = null;  // the last take playing along: {at, song, speed, sources}

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
    runs = [];
    lastRuns = [];
    stopPlayer();
    haveTake(false);
    buttons.forEach((button, at) => button.setAttribute("aria-pressed", String(at === index)));
    track.place(index);
    ctx.setFollowLead(index >= 0 ? 0.6 : null);  // the take behind the playhead stays in sight while zoomed
    take = null;
    if (index < 0) {
      stopRecording();
      return;
    }
    takeMax = 0.05;
    shownRuns = null;
    loadLastTake(index);
  }

  async function loadLastTake(index) {
    const last = takes.filter((item) => item.stem === ctx.stems[index].name).at(-1);
    if (!last || !ctx.files) return;
    try {
      const loaded = [];
      for (const run of last.runs) loaded.push({ from: run.from, to: run.to, samples: wavSamples(await ctx.files.load(run.file)) });
      if (!ctx.current || stem !== index || lastRuns.length) return;  // another stem, or a newer take recorded meanwhile
      lastRuns = loaded;
      takeLatency = Number.isFinite(last.latency) ? last.latency : settings.lastLatency || 0;  // before version 26: the last one
      haveTake(true);
    } catch (error) {
      ctx.log("take_load_failed", { message: String((error && error.message) || error) }, "warning");  // a file removed by hand
    }
  }

  // The take's peaks, once the song's length is known (the stems' metadata).
  const takePeaks = () => {
    if (!take && stem >= 0 && ctx.duration > 0) take = new Float32Array(Math.ceil(ctx.duration / PEAK) + 1).fill(-1);
    return take;
  };
  const latency = () => (capture ? Number(latencyOf(settings, capture.key)) || 0 : 0);

  function onBlock({ data: block }) {
    const peaks = takePeaks();
    if (!peaks || !ctx.playing) return;
    const times = blockTimes(block.frame, block.samples.length, ctx.audioContext.sampleRate, latency(), ctx.songTimeAt);
    if (!times) return;
    const { from, to } = times;
    if (!runs.length) {  // a new take (each play while recording is on): the lane shows only what is recorded now
      peaks.fill(-1);
      takeMax = 0.05;
    }
    addPeaks(peaks, PEAK, from, to, block.samples);
    addToRuns(runs, from, to, block.samples);
    for (const value of block.samples) if (Math.abs(value) > takeMax) takeMax = Math.abs(value);
  }

  // The audio input in blocks of {frame, samples} (capture.js) on the AudioContext the song plays on, without the
  // browser's echo cancellation, noise suppression and gain control (they delay and change the sound).
  // Each step is logged (logs/app.jsonl, events practice_input_*): the permission before and after the browser
  // was asked, how long it took, the device asked for and the one opened, the engine's state.
  async function openInput(onBlock, why) {
    const audio = ctx.audioContext, started = performance.now();
    const asked = { why, input: settings.input ? settings.input.slice(0, 8) : "default", permission: await micPermission(), engine: engineState(audio) };
    ctx.log("input_requested", asked);
    let stream, step = "worklet";
    const constraints = { deviceId: settings.input ? { exact: settings.input } : undefined,
      echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 };
    try {
      if (!captureModules.has(audio)) captureModules.set(audio, audio.audioWorklet.addModule("plugins/practice/capture.js"));
      await captureModules.get(audio);
      step = "getUserMedia";
      stream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
    } catch (error) {
      if (step === "worklet") captureModules.delete(audio);  // a failed module load is tried again next time
      const name = String((error && error.name) || "Error");
      ctx.log("input_failed", { why, step, error: name, message: String((error && error.message) || error),
        constraint: (error && error.constraint) || null, ms: Math.round(performance.now() - started),
        asked: { ...constraints, deviceId: settings.input ? settings.input.slice(0, 8) : "default" }, open_streams: openStreams,
        permission: await micPermission(), devices: await inputDevices(), engine: engineState(audio),
        probes: step === "getUserMedia" && name !== "NotAllowedError" && name !== "SecurityError" ? await probeInputs(settings.input) : null }, "error");
      throw error;
    }
    openStreams++;
    const key = await deviceKey(settings.input);
    const track = stream.getAudioTracks()[0], got = (track && track.getSettings && track.getSettings()) || {};
    ctx.log("input_opened", { why, ms: Math.round(performance.now() - started), permission: await micPermission(),
      label: track ? track.label : null, device: (got.deviceId || "").slice(0, 8), rate: got.sampleRate || null,
      channels: got.channelCount || null, latency_ms: got.latency ? Math.round(got.latency * 10000) / 10 : null,
      echo: got.echoCancellation ?? null, noise: got.noiseSuppression ?? null, gain: got.autoGainControl ?? null,
      open_streams: openStreams, muted: track ? track.muted : null, state: track ? track.readyState : null, engine: engineState(audio), devices: await inputDevices() });
    if (track) {
      track.addEventListener("ended", () => ctx.log("input_ended", { why, label: track.label }, "warning"));
      track.addEventListener("mute", () => ctx.log("input_muted", { why, label: track.label }, "warning"));
      track.addEventListener("unmute", () => ctx.log("input_unmuted", { why, label: track.label }));
    }
    const source = audio.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(audio, "practice-capture");
    const sink = audio.createGain();  // silent: Chrome runs a worklet only while it leads to the output
    sink.gain.value = 0;
    source.connect(node).connect(sink).connect(audio.destination);
    node.port.onmessage = onBlock;
    measuredKey = key;
    showLatency();
    let closed = false;
    return {
      key,
      close() {
        if (closed) return;
        closed = true;
        openStreams--;
        ctx.log("input_closed", { why, seconds: Math.round(performance.now() - started) / 1000, open_streams: openStreams, engine: engineState(audio) });
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
      ctx.log("recording_requested", { stem: stem >= 0 ? ctx.stems[stem].name : null, playing: ctx.playing, engine: engineState(ctx.audioContext) });
      const precise = await ctx.precise();
      if (!precise || !ctx.current) {
        ctx.log("recording_not_started", { precise: Boolean(precise), current: Boolean(ctx.current) }, "warning");
        return;
      }
      const opened = await openInput(onBlock, "recording");
      if (!ctx.current || record.getAttribute("aria-pressed") !== "true") {
        opened.close();
        return;
      }
      capture = opened;
      recordedLatency = latency();
      if (take) take.fill(-1);  // a new take starts on an empty lane; the last one is silent and hidden meanwhile
      takeMax = 0.05;
      shownRuns = lastRuns;
      showStatus();
    } catch (error) {
      ctx.log("recording_failed", { error: String((error && error.name) || "Error"), message: String((error && error.message) || error) }, "error");
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
    if (take) take.fill(-1);  // the last take shows again (the next frame adds its peaks)
    takeMax = 0.05;
    shownRuns = null;
  }

  // A take ends when the song stops or recording is turned off: kept to hear again and saved. The score of a take
  // (practice-score branch) is left out until its note detection is reliable.
  function finishTake() {
    const recorded = runs, chosen = stem;
    runs = [];
    if (chosen >= 0) ctx.log("take_ended", { stem: ctx.stems[chosen].name, runs: recorded.length,
      seconds: Math.round(recorded.reduce((sum, run) => sum + run.to - run.from, 0) * 100) / 100, peak: Math.round(takeMax * 1000) / 1000 });
    if (!recorded.length || chosen < 0) return;
    lastRuns = recorded.map((run) => ({ from: run.from, to: run.to, samples: samplesOf(run) }));
    takeLatency = recordedLatency;
    shownRuns = lastRuns;
    haveTake(true);
    saveTake(lastRuns, ctx.stems[chosen].name);
  }
  let wasPlaying = false;

  // The take's runs as WAV files; older takes go when the plugin's space (100 MB a project) would overflow.
  async function saveTake(saved, stemName) {
    if (!ctx.files) return;
    const stamp = Date.now();
    const files = saved.map((run, k) => ({ name: `take-${stamp}-${k}.wav`, bytes: wavBytes(run.samples, Math.round(ctx.audioContext.sampleRate)) }));
    const size = files.reduce((sum, file) => sum + file.bytes.byteLength, 0);
    try {
      const { files: stored, limit_bytes: limit } = await ctx.files.list();
      if (size > limit) throw new Error("too large");
      let used = stored.reduce((sum, file) => sum + file.bytes, 0);
      const sizes = Object.fromEntries(stored.map((file) => [file.name, file.bytes]));
      while (takes.length && used + size > limit) {
        for (const run of takes.shift().runs) {
          used -= sizes[run.file] || 0;
          await ctx.files.remove(run.file);
        }
      }
      for (const file of files) await ctx.files.save(file.name, file.bytes);
      takes.push({ stem: stemName, at: new Date(stamp).toISOString(), latency: recordedLatency, runs: saved.map((run, k) => ({ file: files[k].name, from: run.from, to: run.to })) });
      data.takes = takes;
      await ctx.saveData(data);
      ctx.log("take_saved", { files: files.length, bytes: size, kept: takes.length });
    } catch (error) {
      ctx.log("take_save_failed", { bytes: size, message: String((error && error.message) || error) }, "error");
      if (ctx.current) status.textContent = ctx.t("The take could not be saved");
    }
  }

  // The last take along with the song like a stem (the row's M silences it, recording too), on the song's clock (the
  // exact engine): started again after a seek, a
  // loop's jump back or a speed change. shortcut: another speed changes the take's pitch too; fine for listening.
  function stopPlayer() {
    if (player) player.sources.forEach((source) => { try { source.stop(); } catch (_error) { /* not started */ } });
    player = null;
  }
  function follow() {
    if (record.getAttribute("aria-pressed") === "true" || !ctx.playing || !lastRuns.length) return stopPlayer();
    const audio = ctx.audioContext, now = audio.currentTime + 0.06;
    const song = ctx.songTimeAt(now), later = ctx.songTimeAt(now + 0.1);
    if (song === null || later === null || later <= song) return stopPlayer();
    const speed = Math.round(((later - song) / 0.1) * 100) / 100;
    if (player && Math.abs(player.song + (now - player.at) * player.speed - song) < 0.02 && player.speed === speed) return undefined;
    stopPlayer();
    player = { at: now, song, speed, sources: [] };
    for (const run of lastRuns) {
      if (run.to <= song) continue;
      if (!run.buffer) {  // its rate is the run's samples per song second, so buffer time is song time
        run.buffer = audio.createBuffer(1, run.samples.length, Math.round(run.samples.length / (run.to - run.from)));
        run.buffer.copyToChannel(run.samples, 0);
      }
      const source = audio.createBufferSource();
      source.buffer = run.buffer;
      source.playbackRate.value = speed;
      source.connect(track.input);  // the row's volume, S, M and EQ, then the master
      source.start(now + Math.max(0, run.from - song) / speed, Math.max(0, song - run.from));
      player.sources.push(source);
    }
    return undefined;
  }

  // Another latency moves the take shown: what was placed `lag` earlier goes where the new value puts it, also on disk.
  // shortcut: exact for a take recorded at 100 % speed (slower, it moves by the change in song seconds at that speed).
  function moveTake(value) {
    const delta = takeLatency - value;
    takeLatency = value;
    if (!delta || !lastRuns.length || stem < 0) return;
    lastRuns = lastRuns.map((run) => ({ ...run, from: run.from + delta, to: run.to + delta }));
    if (take) take.fill(-1);  // the next frame adds the moved runs' peaks
    stopPlayer();
    const saved = takes.filter((item) => item.stem === ctx.stems[stem].name).at(-1);
    if (!saved) return;
    saved.runs.forEach((run) => { run.from += delta; run.to += delta; });
    saved.latency = value;
    ctx.saveData(data);
  }

  function showStatus() {
    if (!capture) return;
    status.textContent = latencyOf(settings, capture.key) === null ? ctx.t("Latency not measured") : "";
  }

  // The latency of the input and output in use (known after the first recording or measurement).
  function showLatency() {
    const value = measuredKey && latencyOf(settings, measuredKey);
    measure.innerHTML = ICONS.latency;
    if (value) measure.append(`${ctx.formatDecimal(Math.round(value * 10000) / 10)} ms`);
    measure.title = ctx.t("Measure the delay of your input and output");
    measure.setAttribute("aria-label", value
      ? ctx.t("Latency {latency}", { latency: `${ctx.formatDecimal(Math.round(value * 10000) / 10)} ms` })
      : ctx.t("Measure latency"));
  }
  showLatency();
  deviceKey(settings.input).then((key) => {  // the key before any recording: the chosen input and the system's output now
    if (measuredKey === null && ctx.current) measuredKey = key;
    showLatency();
  });
  ctx.on(measure, "click", () => {
    stopRecording();
    openTuner(ctx, {
      openInput: (onBlock) => openInput(onBlock, "tuner"),
      engineState,
      known: measuredKey ? latencyOf(settings, measuredKey) : null,
      key: measuredKey || latencyKey("", ""),
      save: (key, seconds) => {
        const value = Math.round(seconds * 100000) / 100000;
        settings.latency = { ...settings.latency, [key]: value };
        settings.lastLatency = value;
        ctx.saveSettings({ ...settings });
        moveTake(value);
        showLatency();
      },
    });
  });

  // The mix as heard, then the take as recorded under the mix's number, rendered by the app and kept as FLAC; the
  // folder opens once, at the take; what happened shows over the lane.
  async function exportTake() {
    if (!lastRuns.length || keep.disabled) return;
    keep.disabled = true;
    const say = (text) => {
      status.textContent = text;
      window.setTimeout(() => { if (status.textContent === text) status.textContent = ""; }, 8000);
    };
    status.textContent = ctx.t("Saving…");
    try {
      const mix = await ctx.exportMix({ kind: "mix", track: track.index, parts: lastRuns, show: false });
      const raw = await ctx.exportMix({ kind: "raw", track: track.index, parts: lastRuns, number: mix.number });
      ctx.log("exported", { runs: lastRuns.length, number: mix.number });
      if (ctx.current) say(ctx.t("Saved in Music/Exports: {name}", { name: `${mix.name}, ${raw.name}` }));
    } catch (error) {
      ctx.log("export_failed", { message: String((error && error.message) || error) }, "error");
      if (ctx.current) say(ctx.t("Not saved: {error}", { error: (error && error.message) || error }));
    } finally {
      keep.disabled = false;
    }
  }
  ctx.on(keep, "click", exportTake);

  ctx.on(record, "click", () => (record.getAttribute("aria-pressed") === "true" ? stopRecording() : startRecording()));

  // Drawing: one frame per screen refresh while the row shows, on the stems' time scale (zoom, range, playhead and
  // beat lines come from the host and the other plugins): an outline through the loudest millisecond under each COLUMN pixels.
  const context2d = canvas.getContext("2d");
  let frame = 0;
  const color = (name) => getComputedStyle(lane).getPropertyValue(name).trim();
  function draw() {
    frame = requestAnimationFrame(draw);
    if (wasPlaying && !ctx.playing) finishTake();  // a pause ends the take
    wasPlaying = ctx.playing;
    follow();
    const peaks = takePeaks();
    if (track.row.hidden || !peaks) return;
    if (lastRuns.length && shownRuns !== lastRuns) {  // a take loaded from disk: its peaks, once the length is known
      shownRuns = lastRuns;
      for (const run of lastRuns) {
        addPeaks(peaks, PEAK, run.from, run.to, run.samples);
        for (const value of run.samples) if (Math.abs(value) > takeMax) takeMax = Math.abs(value);
      }
    }
    const dpr = window.devicePixelRatio || 1;
    const width = Math.round(canvas.clientWidth * dpr), height = Math.round(canvas.clientHeight * dpr);
    const view = ctx.view;
    if (!width || !height || !(view.end > view.start)) return;
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    context2d.clearRect(0, 0, width, height);
    context2d.fillStyle = color("--warn");
    const count = Math.ceil(width / COLUMN), shown = { start: view.start, end: view.start + (view.end - view.start) * (count * COLUMN) / width };
    const mid = height / 2, room = mid * 0.9, columns = columnPeaks(peaks, PEAK, shown, count);
    outline(context2d, columns, COLUMN, mid, (peak) => Math.max(0.5, Math.min(1, peak / takeMax) * room));
  }
  frame = requestAnimationFrame(draw);

  if (stem >= 0) choose(stem);
  return () => {
    cancelAnimationFrame(frame);
    stopRecording();
    stopPlayer();
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

  panel.append(inputLabel);
}
