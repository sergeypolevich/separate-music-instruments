// The practice plugin's score of a take: note starts (onsets) in the original stem and in the recording, each
// original note matched to the nearest recorded one within REACH; on time within the tolerance, else early or
// late; missed when nothing is near. The same detection runs on both, so its own bias cancels out.

export const REACH = 0.08;  // seconds: farther than this is another note, not a late one
const WINDOW = 0.023, FLOOR_DB = -70, AROUND = 0.1, GAP = 0.05;

// An in-place radix-2 FFT of re/im (length a power of two), with its bit-reversal and twiddle tables made once.
const tables = new Map();
function fft(re, im) {
  const n = re.length;
  if (!tables.has(n)) {
    const swap = new Uint32Array(n), cos = new Float64Array(n / 2), sin = new Float64Array(n / 2);
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      swap[i] = j ^= bit;
    }
    for (let k = 0; k < n / 2; k++) { cos[k] = Math.cos((-2 * Math.PI * k) / n); sin[k] = Math.sin((-2 * Math.PI * k) / n); }
    tables.set(n, { swap, cos, sin });
  }
  const { swap, cos, sin } = tables.get(n);
  for (let i = 1; i < n; i++) {
    const j = swap[i];
    if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
  }
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1, stride = n / size;
    for (let i = 0; i < n; i += size) {
      for (let k = 0; k < half; k++) {
        const wr = cos[k * stride], wi = sin[k * stride], a = i + k, b = a + half;
        const tr = wr * re[b] - wi * im[b], ti = wr * im[b] + wi * re[b];
        re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
      }
    }
  }
}

const pause = () => new Promise((resolve) => setTimeout(resolve, 0));

// Note starts in seconds from the first sample: peaks of the spectral flux (how much the log spectrum rose since the
// last frame, ~23 ms frames 1/8 apart). In the log, a filter on the way (a pickup, an earcup) only adds a constant
// per frequency, which the difference removes, so a recording and its original give the same starts (a take that is
// the stem through a pickup: 95 % within 15 ms, ±3 ms; a 6 dB energy rise gave 43 %, ±22 ms). A peak counts over
// the flux's mean around it (±0.1 s) plus half its spread; of two within 50 ms the stronger stays. Async: it gives
// the page a turn every few thousand frames.
export async function onsets(samples, rate) {
  const size = 2 ** Math.round(Math.log2(WINDOW * rate)), hop = size / 8, count = Math.floor((samples.length - size) / hop);
  if (count < 3) return [];
  const bins = size / 2 + 1, window = new Float64Array(size).map((_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (size - 1)));
  const re = new Float64Array(size), im = new Float64Array(size);
  // Two frames in one FFT (one in the real part, one in the imaginary), split by symmetry: half the work.
  // Out: their squared magnitudes (no square roots); frame `second` may be past the end (then left out).
  const spectra = (first, second, outFirst, outSecond) => {
    const a = first * hop, b = second < count ? second * hop : -1;
    for (let i = 0; i < size; i++) { re[i] = samples[a + i] * window[i]; im[i] = b < 0 ? 0 : samples[b + i] * window[i]; }
    fft(re, im);
    for (let k = 0; k < bins; k++) {
      const m = (size - k) % size, sr = re[k] + re[m], dr = re[k] - re[m], si = im[k] + im[m], di = im[k] - im[m];
      outFirst[k] = (sr * sr + di * di) / 4;
      outSecond[k] = (si * si + dr * dr) / 4;
    }
  };
  // The floor (FLOOR_DB under the loudest bin) from every 8th frame: close enough, a sixteenth of the work.
  let loudest = 0;
  const one = new Float64Array(bins), two = new Float64Array(bins);
  for (let frame = 0; frame < count; frame += 16) {
    spectra(frame, frame + 8, one, two);
    for (let k = 0; k < bins; k++) loudest = Math.max(loudest, one[k], frame + 8 < count ? two[k] : 0);
    if (frame % 8192 === 0) await pause();
  }
  if (!(loudest > 0)) return [];
  const floor = loudest * 10 ** (FLOOR_DB / 10), flux = new Float64Array(count);  // of the squared magnitudes
  let last = new Float64Array(bins), now = new Float64Array(bins);
  const step = (frame, power) => {
    let rise = 0;
    for (let k = 0; k < bins; k++) {
      now[k] = 0.5 * Math.log(Math.max(power[k], floor));
      if (frame && now[k] > last[k]) rise += now[k] - last[k];
    }
    flux[frame] = rise / bins;
    [last, now] = [now, last];
  };
  for (let frame = 0; frame < count; frame += 2) {
    spectra(frame, frame + 1, one, two);
    step(frame, one);
    if (frame + 1 < count) step(frame + 1, two);
    if (frame % 2048 === 0) await pause();
  }
  let mean = 0, square = 0;
  for (const value of flux) { mean += value; square += value * value; }
  mean /= count;
  const delta = 0.5 * Math.sqrt(Math.max(0, square / count - mean * mean)), reach = Math.round((AROUND * rate) / hop);
  const sums = new Float64Array(count + 1);
  for (let i = 0; i < count; i++) sums[i + 1] = sums[i] + flux[i];
  const found = [];
  for (let i = 1; i < count - 1; i++) {
    const [a, b, c] = [flux[i - 1], flux[i], flux[i + 1]];
    // the mean over ±reach frames, the missing ones beyond the ends counted as 0 (as numpy's 'same' convolution)
    if (!(b >= a && b > c && b > (sums[Math.min(count, i + reach + 1)] - sums[Math.max(0, i - reach)]) / (2 * reach + 1) + delta)) continue;
    const bend = a - 2 * b + c, time = (i * hop + size / 2) / rate + (bend ? (0.5 * (a - c)) / bend : 0) * (hop / rate);
    const previous = found[found.length - 1];
    if (previous && time - previous.time < GAP) {
      if (b > previous.strength) Object.assign(previous, { time, strength: b });
      continue;
    }
    found.push({ time, strength: b });
  }
  return found.map((item) => item.time);
}

// Each reference note gets the closest take note within REACH, closest pairs first; offsets are take minus reference.
export function match(reference, take) {
  const pairs = [];
  let j = 0;
  reference.forEach((time, i) => {
    while (j < take.length && take[j] < time - REACH) j++;
    for (let k = j; k < take.length && take[k] <= time + REACH; k++) pairs.push([Math.abs(take[k] - time), i, k]);
  });
  pairs.sort((a, b) => a[0] - b[0]);
  const offsets = reference.map(() => null), used = new Set();
  for (const [, i, k] of pairs) {
    if (offsets[i] !== null || used.has(k)) continue;
    offsets[i] = take[k] - reference[i];
    used.add(k);
  }
  return offsets;
}

export function summary(offsets, tolerance) {
  const hit = offsets.filter((offset) => offset !== null);
  const mean = hit.length ? hit.reduce((sum, value) => sum + value, 0) / hit.length : 0;
  const spread = hit.length > 1 ? Math.sqrt(hit.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (hit.length - 1)) : 0;
  return {
    notes: offsets.length,
    onTime: hit.filter((offset) => Math.abs(offset) <= tolerance).length,
    early: hit.filter((offset) => offset < -tolerance).length,
    late: hit.filter((offset) => offset > tolerance).length,
    missed: offsets.length - hit.length,
    mean, spread,
  };
}

// A beat's seconds at `time` from the beat analysis ({tempo: [[time, bpm], ...], bpm}); null without a tempo.
export function beatAt(analysis, time) {
  if (!analysis) return null;
  const tempo = Array.isArray(analysis.tempo) ? analysis.tempo : [];
  const bpm = (tempo.filter(([at]) => at <= time).at(-1) || tempo[0] || [0, analysis.bpm])[1];
  return bpm > 0 ? 60 / bpm : null;
}

// The note value nearest to an offset, a beat being a quarter: 31 ms at 120 BPM is 1/64. null under 1/256.
export function noteValue(seconds, beat) {
  if (!(Math.abs(seconds) > 0) || !(beat > 0)) return null;
  const denominator = 2 ** Math.round(Math.log2((4 * beat) / Math.abs(seconds)));
  return denominator > 256 ? null : `1/${Math.max(4, denominator)}`;
}

// The histogram's ticks as note values within `reach` seconds, at least `apart` seconds from each other and zero.
export function noteTicks(beat, reach = 0.07, apart = 0.012) {
  const ticks = [];
  for (let denominator = 4; denominator <= 256; denominator *= 2) {
    const seconds = (4 * beat) / denominator;
    if (seconds <= reach && seconds >= apart && (!ticks.length || ticks.at(-1).seconds - seconds >= apart)) {
      ticks.push({ seconds, label: `1/${denominator}` });
    }
  }
  return ticks;
}

const SVG = "http://www.w3.org/2000/svg";
const element = (name, attributes = {}, text = "") => {
  const node = document.createElementNS(SVG, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  if (text) node.textContent = text;
  return node;
};
const kind = (offset, tolerance) => (offset === null ? "missed" : Math.abs(offset) <= tolerance ? "on-time" : offset < 0 ? "early" : "late");

// Every note played against the nearest line of the beat grid, `parts` lines a beat (2: eighths): its offset from
// that line, so at most half a step. Notes before the first beat or after the last are left out.
export function gridOffsets(played, beats, parts = 2) {
  const lines = [];
  for (let i = 0; i + 1 < beats.length; i++) {
    for (let k = 0; k < parts; k++) lines.push(beats[i] + ((beats[i + 1] - beats[i]) * k) / parts);
  }
  if (beats.length) lines.push(beats.at(-1));
  const times = [], offsets = [];
  let j = 0;
  for (const time of played) {  // in order
    if (!lines.length || time < lines[0] || time > lines.at(-1)) continue;
    while (j + 1 < lines.length && lines[j + 1] <= time) j++;
    const near = j + 1 < lines.length && lines[j + 1] - time < time - lines[j] ? lines[j + 1] : lines[j];
    times.push(time);
    offsets.push(time - near);
  }
  return { times, offsets };
}

// The panel after a take, in two tabs: the notes of the original stem against the ones played, and (with the song's
// beats) every note played against the 1/8 grid. Each: the counts, a histogram of the offsets and a strip through
// the part played. With `beat` (a beat's seconds) the offsets are also told as note values: «20 ms» says little,
// «1/16» says much. The slider moves the tolerance (`limits` in ms) and draws again; `onTolerance(ms)` keeps it.
export function showScore(ctx, { reference, offsets, tolerance, from, to, history, beat = null, grid = null, stemName = "",
  limits = { min: 5, max: 40 }, onTolerance = () => {} }) {
  const t = ctx.t;
  const ms = (seconds) => `${seconds > 0.0005 ? "+" : ""}${Math.round(seconds * 1000)} ms`;
  const note = (seconds) => {
    const value = noteValue(seconds, beat);
    return beat ? (value ? t("≈ {note} note", { note: value }) : t("under 1/256 note")) : "";
  };
  const views = [{ key: "stem", name: stemName ? t("Against the {stem}", { stem: stemName }) : t("Against the original"),
    notes: reference, offsets, reach: 0.07 }];
  if (grid && beat && grid.offsets.length) views.push({ key: "grid", name: t("Grid 1/8"), notes: grid.times, offsets: grid.offsets, reach: beat / 4 });
  let view = views[0], within = tolerance;
  const dialog = ctx.add(document.body, document.createElement("dialog"));
  dialog.className = "practice-dialog practice-score";
  dialog.innerHTML = `<div class="practice-card">
    <div class="practice-heading"><h2></h2><button type="button" class="icon-button" data-action="close"></button></div>
    <div class="practice-tabs" role="tablist"></div>
    <div class="practice-tiles"></div>
    <div class="practice-legend"></div>
    <svg class="practice-histogram" viewBox="0 0 900 ${beat ? 186 : 170}" role="img"></svg>
    <svg class="practice-strip" viewBox="0 0 900 60" role="img"></svg>
    <label class="practice-tolerance"><span></span><input type="range" step="1" data-setting="tolerance"><output></output></label>
    <p class="practice-why"></p>
    <p class="practice-history"></p>
  </div>`;
  const $ = (selector) => dialog.querySelector(selector);
  $("h2").textContent = t("Take score");
  const close = $('[data-action="close"]');
  close.textContent = "✕";
  close.setAttribute("aria-label", t("Close"));
  close.title = t("Close");
  const left = 30, width = 860, top = 10, height = 130;
  const histogram = $(".practice-histogram"), strip = $(".practice-strip");
  strip.setAttribute("aria-label", t("Every note of the take, from its start to its end"));
  const tabs = $(".practice-tabs");
  tabs.hidden = views.length < 2;
  views.forEach((item) => {
    const tab = tabs.appendChild(document.createElement("button"));
    Object.assign(tab, { type: "button", className: "practice-tab", textContent: item.name });
    tab.dataset.view = item.key;
    tab.setAttribute("role", "tab");
    ctx.on(tab, "click", () => { view = item; draw(); });
  });

  const draw = () => {
    const tolerance = within, { offsets, notes, reach } = view, onGrid = view.key === "grid";
    tabs.querySelectorAll(".practice-tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.view === view.key)));
    const result = summary(offsets, tolerance);
    const percent = result.notes ? Math.round((result.onTime / result.notes) * 100) : 0;
    const tiles = [
      ["main", onGrid ? t("On the grid") : t("On time"), t("{hit} of {count} · {percent} %", { hit: result.onTime, count: result.notes, percent }), "on-time"],
      ["", t("Early"), String(result.early), "early"],
      ["", t("Late"), String(result.late), "late"],
      ...(onGrid ? [] : [["", t("Missed"), String(result.missed), "missed"]]),
      ["", t("Average"), ms(result.mean), "", note(result.mean), "average"],
      ["", t("Spread"), `±${Math.round(result.spread * 1000)} ms`, "", note(result.spread), "spread"],
    ];
    $(".practice-tiles").replaceChildren(...tiles.map(([size, name, value, swatch, aside = "", key = ""]) => {
      const tile = document.createElement("div");
      tile.className = `practice-tile ${size}`;
      tile.dataset.tile = swatch || key;
      tile.innerHTML = `<span class="practice-key">${swatch ? `<i class="practice-swatch" data-kind="${swatch}"></i>` : ""}</span><span class="practice-value"></span>`;
      tile.querySelector(".practice-key").append(name);
      tile.querySelector(".practice-value").textContent = value;
      if (aside) tile.append(Object.assign(document.createElement("span"), { className: "practice-note", textContent: aside }));
      return tile;
    }));
    const ms_ = Math.round(tolerance * 1000), noted = noteValue(tolerance, beat) || "1/256";
    $(".practice-legend").textContent = onGrid
      ? t("Each note you played against the nearest 1/8 of the song's beat grid ({bpm} BPM): on the grid within ±{tolerance} ms ({note}); early or late beyond it, up to 1/16, halfway to the next 1/8.",
        { tolerance: ms_, note: noted, bpm: Math.round(60 / beat) })
      : beat
        ? t("On time within ±{tolerance} ms ({note} at {bpm} BPM, a beat being 1/4); early or late beyond it; missed: no note within 80 ms.",
          { tolerance: ms_, note: noted, bpm: Math.round(60 / beat) })
        : t("On time within ±{tolerance} ms; early or late beyond it; missed: no note within 80 ms.", { tolerance: ms_ });
    $(".practice-tolerance output").textContent = beat ? `±${ms_} ms · ${noted}` : `±${ms_} ms`;

    // Histogram: 28 bins over ±reach, the tolerance band behind.
    histogram.setAttribute("aria-label", onGrid ? t("How far the notes played were from the 1/8 grid, in note values and milliseconds")
      : beat ? t("How far the notes were from the original, in note values and milliseconds") : t("How far the notes were from the original, in milliseconds"));
    const x = (seconds) => left + ((seconds + reach) / (2 * reach)) * width, bin = (2 * reach) / 28;
    histogram.replaceChildren(element("rect", { class: "practice-band", x: x(-tolerance), y: top, width: x(tolerance) - x(-tolerance), height }));
    const bins = new Array(28).fill(0);
    offsets.forEach((offset) => {
      if (offset !== null) bins[Math.min(27, Math.max(0, Math.floor((offset + reach) / bin)))]++;
    });
    const tallest = Math.max(1, ...bins);
    bins.forEach((count, i) => {
      if (!count) return;
      const centre = -reach + (i + 0.5) * bin, size = (count / tallest) * (height - 6);
      histogram.append(element("rect", { class: `practice-bar-${kind(centre, tolerance)}`, x: x(centre) - 13, y: top + height - size, width: 26, height: size, rx: 3 }));
    });
    const ticks = beat ? noteTicks(beat, reach * (1 + 1e-9)).flatMap(({ seconds, label }) => [{ seconds: -seconds, label: `−${label}` }, { seconds, label: `+${label}` }])
      : [-60, -40, -20, 20, 40, 60].map((value) => ({ seconds: value / 1000, label: `${value > 0 ? "+" : ""}${value}` }));
    [{ seconds: 0, label: "0" }, ...ticks].forEach(({ seconds, label }) => {
      const anchor = seconds >= reach * 0.99 ? "end" : seconds <= -reach * 0.99 ? "start" : "middle";  // the edges' labels stay inside
      histogram.append(element("line", { class: seconds ? "practice-tick" : "practice-zero", x1: x(seconds), x2: x(seconds), y1: top, y2: top + height }));
      histogram.append(element("text", { x: x(seconds), y: 162, "text-anchor": anchor, "data-tick": label }, label));
      if (beat && seconds) histogram.append(element("text", { class: "practice-ms", x: x(seconds), y: 180, "text-anchor": anchor }, ms(seconds)));
    });

    // The strip: every note where it is in the song, coloured by how it was played (a missed one lower).
    const along = (time) => left + ((time - from) / Math.max(0.001, to - from)) * width;
    strip.replaceChildren(element("line", { class: "practice-tick", x1: left, x2: left + width, y1: 30, y2: 30 }));
    notes.forEach((time, i) => strip.append(element("rect", { class: `practice-bar-${kind(offsets[i], tolerance)}`, x: along(time) - 1.5, y: offsets[i] === null ? 36 : 14, width: 3, height: 16, rx: 1 })));
    strip.append(element("text", { x: left, y: 58 }, formatTime(from)));
    strip.append(element("text", { x: left + width, y: 58, "text-anchor": "end" }, formatTime(to)));
    return result;
  };

  const slider = $(".practice-tolerance input");
  $(".practice-tolerance span").textContent = t("On time within");
  Object.assign(slider, { min: limits.min, max: limits.max, value: Math.round(tolerance * 1000) });
  $(".practice-why").textContent = t("Why ±15 ms by default: about 20 ms apart is the least at which the ear tells which of two notes came first, so notes within ±15 ms sound together with the original. A tighter value trains finer timing; a looser one suits a first go.");
  ctx.on(slider, "input", () => { within = Number(slider.value) / 1000; draw(); });
  ctx.on(slider, "change", () => onTolerance(Number(slider.value)));
  if (history.length) {
    $(".practice-history").textContent = t("Earlier takes: {list}", { list: history.map((item) => `${item.notes ? Math.round((item.onTime / item.notes) * 100) : 0} %`).join(" · ") });
  }
  ctx.on(close, "click", () => dialog.close());
  ctx.on(dialog, "close", () => dialog.remove());
  const result = draw();
  dialog.showModal();
  return result;
}

function formatTime(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
