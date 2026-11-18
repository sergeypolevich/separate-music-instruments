// Practice (version 24): play your part along with the song. A target button in a stem row picks the stem you
// play (you mute it yourself with M); a mixer row «You» under that stem (ctx.addTrack) shows your audio input on
// the stems' time scale, drawn as it arrives, and plays your last take through its own volume, S, M and EQ.
// The stems play through the decoded engine while practising, so a captured frame's AudioContext time maps to the
// song second heard then (ctx.songTimeAt); the input is placed earlier by the measured latency (the latency tuner).

export const PEAK = 0.001;  // the take's peaks: one a millisecond of song, drawn one column a screen pixel
const TOLERANCE = { min: 5, max: 40, fallback: 15 };  // ms

export function choice(settings = {}) {
  const tolerance = Number(settings.tolerance);
  return {
    input: typeof settings.input === "string" ? settings.input : "",
    tolerance: Number.isFinite(tolerance) ? Math.min(TOLERANCE.max, Math.max(TOLERANCE.min, tolerance)) : TOLERANCE.fallback,
    latency: settings.latency && typeof settings.latency === "object" ? settings.latency : {},
  };
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

// The note starts of the take (song seconds) and of the original over the same stretch, each run on its own (the
// detection's thresholds follow the part heard), the original's 0.1 s in from the edges, where a note may be cut.
// `reference` is the stem from song time 0: {samples, sampleRate}.
export async function takeNotes(runs, reference) {
  const played = [], covered = [], rate = reference.sampleRate;
  for (const run of runs) {
    const samples = samplesOf(run);
    played.push(...(await onsets(samples, samples.length / (run.to - run.from))).map((time) => run.from + time));
    const first = Math.max(0, Math.round(run.from * rate)), part = reference.samples.subarray(first, Math.round(run.to * rate));
    covered.push(...(await onsets(part, rate)).map((time) => first / rate + time).filter((time) => time >= run.from + 0.1 && time <= run.to - 0.1));
  }
  return { played: played.sort((a, b) => a - b), covered };
}

export function latencyKey(input, output) {
  return `${input || "default"}>${output || "default"}`;
}

import { openTuner } from "./tuner.mjs";
import { beatAt, match, onsets, showScore } from "./score.mjs";

const TARGET = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>';
const ICONS = {
  record: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="6" fill="currentColor"/></svg>',
  listen: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/></svg>',
  latency: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 2h4M12 14l3-3"/><circle cx="12" cy="14" r="8"/></svg>',
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
  const listen = bar.appendChild(document.createElement("button"));
  listen.type = "button";
  listen.className = "practice-record practice-listen";
  listen.dataset.action = "practice-listen";
  listen.setAttribute("aria-pressed", "false");
  listen.innerHTML = ICONS.listen;
  listen.title = ctx.t("Play your last take along with the song");
  listen.setAttribute("aria-label", ctx.t("Play your last take"));
  listen.disabled = true;
  const measure = bar.appendChild(document.createElement("button"));
  measure.type = "button";
  measure.className = "practice-record practice-latency";
  measure.dataset.action = "practice-latency";
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
  let reference = null;  // the chosen stem's audio from song time 0 (a promise), decoded once
  let analysis = null;  // the song's beats and tempo (a promise), asked for once a stem is chosen
  const scores = Array.isArray(data.scores) ? data.scores : [];
  const takes = Array.isArray(data.takes) ? data.takes : [];  // saved: [{stem, at, runs: [{file, from, to}]}]
  let lastRuns = [];  // the last take of the chosen stem, to hear again: [{from, to, samples}]
  let shownRuns = null;  // the runs whose peaks the lane shows
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
    reference = null;
    runs = [];
    lastRuns = [];
    stopPlayer();
    listen.disabled = true;
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
    analysis = analysis || Promise.resolve(ctx.beats ? ctx.beats() : null).catch(() => null);  // for the score's note values
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
      listen.disabled = false;
    } catch (error) {
      ctx.log("take_load_failed", { message: String((error && error.message) || error) }, "warning");  // a file removed by hand
    }
  }

  // The take's peaks, once the song's length is known (the stems' metadata).
  const takePeaks = () => {
    if (!take && stem >= 0 && ctx.duration > 0) take = new Float32Array(Math.ceil(ctx.duration / PEAK) + 1).fill(-1);
    return take;
  };
  const latency = () => (capture ? Number(settings.latency[capture.key]) || 0 : 0);

  function onBlock({ data: block }) {
    const peaks = takePeaks();
    if (!peaks || !ctx.playing) return;
    const times = blockTimes(block.frame, block.samples.length, ctx.audioContext.sampleRate, latency(), ctx.songTimeAt);
    if (!times) return;
    const { from, to } = times;
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
    const output = await outputGroup();
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
    const key = latencyKey(settings.input, output);
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
      const index = stem;
      reference = reference || ctx.stemAudio(index);
      reference.catch(() => { if (stem === index) reference = null; });
      const opened = await openInput(onBlock, "recording");
      if (!ctx.current || record.getAttribute("aria-pressed") !== "true") {
        opened.close();
        return;
      }
      capture = opened;
      if (take) take.fill(-1);  // a new take starts on an empty lane; the last one stays saved, to hear again
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
  }

  // A take ends when the song stops or recording is turned off: its score, kept with the last 20.
  async function finishTake() {
    const recorded = runs, chosen = stem, tolerance = settings.tolerance / 1000;
    runs = [];
    if (chosen >= 0) ctx.log("take_ended", { stem: ctx.stems[chosen].name, runs: recorded.length,
      seconds: Math.round(recorded.reduce((sum, run) => sum + run.to - run.from, 0) * 100) / 100, peak: Math.round(takeMax * 1000) / 1000 });
    if (!recorded.length || chosen < 0) return;
    lastRuns = recorded.map((run) => ({ from: run.from, to: run.to, samples: samplesOf(run) }));
    shownRuns = lastRuns;
    listen.disabled = false;
    saveTake(lastRuns, ctx.stems[chosen].name);
    if (!reference) return;
    let notes;
    const scoring = ctx.t("Scoring…"), started = performance.now();
    if (!status.textContent) status.textContent = scoring;  // about 1 s for 20 s of take
    try {
      notes = await takeNotes(recorded, await reference);
    } catch (error) {
      ctx.log("score_failed", { message: String((error && error.message) || error) }, "warning");
      return;  // the stem could not be decoded: no score, the recording still showed
    } finally {
      if (status.textContent === scoring) status.textContent = "";
    }
    ctx.log("score_notes", { ms: Math.round(performance.now() - started), original_notes: notes.covered.length, played_notes: notes.played.length });
    if (!ctx.current || notes.covered.length < 4) {  // too short to say anything
      ctx.log("score_skipped", { original_notes: notes.covered.length, played_notes: notes.played.length });
      return;
    }
    const offsets = match(notes.covered, notes.played);
    const history = scores.slice(-5), from = recorded[0].from, to = recorded[recorded.length - 1].to;
    const beats = await Promise.race([analysis, new Promise((resolve) => setTimeout(resolve, 3000))]);  // no wait on a slow analysis
    const result = showScore(ctx, { reference: notes.covered, offsets, tolerance, history, from, to, beat: beatAt(beats, (from + to) / 2),
      limits: TOLERANCE, onTolerance: (value) => {  // the same setting as in Settings → Plugins → Practice
        settings.tolerance = value;
        ctx.saveSettings({ ...settings });
      } });
    scores.push({ at: new Date().toISOString(), stem: ctx.stems[chosen].name, tolerance: settings.tolerance,
      ...Object.fromEntries(Object.entries(result).map(([key, value]) => [key, Math.round(value * 10000) / 10000])) });
    data.scores = scores.slice(-20);
    ctx.log("score", scores.at(-1));
    ctx.saveData(data);
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
      takes.push({ stem: stemName, at: new Date(stamp).toISOString(), runs: saved.map((run, k) => ({ file: files[k].name, from: run.from, to: run.to })) });
      data.takes = takes;
      await ctx.saveData(data);
      ctx.log("take_saved", { files: files.length, bytes: size, kept: takes.length });
    } catch (error) {
      ctx.log("take_save_failed", { bytes: size, message: String((error && error.message) || error) }, "error");
      if (ctx.current) status.textContent = ctx.t("The take could not be saved");
    }
  }

  // The last take along with the song, on the song's clock (the exact engine): started again after a seek, a
  // loop's jump back or a speed change. shortcut: another speed changes the take's pitch too; fine for listening.
  function stopPlayer() {
    if (player) player.sources.forEach((source) => { try { source.stop(); } catch (_error) { /* not started */ } });
    player = null;
  }
  function follow() {
    if (listen.getAttribute("aria-pressed") !== "true" || !ctx.playing || !lastRuns.length) return stopPlayer();
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
  ctx.on(listen, "click", async () => {
    const on = listen.getAttribute("aria-pressed") !== "true";
    listen.setAttribute("aria-pressed", String(on));
    if (on) await ctx.precise();
  });

  function showStatus() {
    if (!capture) return;
    status.textContent = settings.latency[capture.key] === undefined ? ctx.t("Latency not measured") : "";
  }

  // The latency of the input and output in use (known after the first recording or measurement).
  function showLatency() {
    const value = measuredKey && settings.latency[measuredKey];
    measure.innerHTML = ICONS.latency;
    if (value) measure.append(`${ctx.formatDecimal(Math.round(value * 10000) / 10)} ms`);
    measure.title = ctx.t("Measure the delay of your input and output");
    measure.setAttribute("aria-label", value
      ? ctx.t("Latency {latency}", { latency: `${ctx.formatDecimal(Math.round(value * 10000) / 10)} ms` })
      : ctx.t("Measure latency"));
  }
  showLatency();
  outputGroup().then((output) => {  // the key before any recording: the chosen input and the system's output now
    if (measuredKey === null && ctx.current) measuredKey = latencyKey(settings.input, output);
    showLatency();
  });
  ctx.on(measure, "click", () => {
    stopRecording();
    openTuner(ctx, {
      openInput: (onBlock) => openInput(onBlock, "tuner"),
      engineState,
      known: measuredKey ? settings.latency[measuredKey] : null,
      save: (key, seconds) => {
        settings.latency = { ...settings.latency, [key]: Math.round(seconds * 100000) / 100000 };
        ctx.saveSettings({ ...settings });
        showLatency();
      },
    });
  });

  ctx.on(record, "click", () => (record.getAttribute("aria-pressed") === "true" ? stopRecording() : startRecording()));

  // Drawing: one frame per screen refresh while the row shows, on the stems' time scale (zoom, range, playhead and
  // beat lines come from the host and the other plugins): the loudest millisecond under each screen pixel.
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
    const mid = height / 2, room = mid * 0.9, columns = columnPeaks(peaks, PEAK, view, width);
    for (let x = 0; x < width; x++) {
      if (columns[x] < 0) continue;
      const size = Math.max(0.5, Math.min(1, columns[x] / takeMax) * room);
      context2d.fillRect(x, mid - size, 1, size * 2);
    }
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
