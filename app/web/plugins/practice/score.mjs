// The practice plugin's score of a take: note starts (onsets) in the original stem and in the recording, each
// original note matched to the nearest recorded one within REACH; on time within the tolerance, else early or
// late; missed when nothing is near. The same detection runs on both, so its own bias cancels out.

export const REACH = 0.08;  // seconds: farther than this is another note, not a late one
const HOP = 0.003, LOOKBACK = 0.03, GAP = 0.07;

// Note starts in seconds from the first sample: where the energy (3 ms frames) rises 6 dB over its lowest point
// in the last 30 ms and is within 40 dB of the loudest frame; at least 70 ms apart.
export function onsets(samples, rate) {
  const hop = Math.max(1, Math.round(HOP * rate)), count = Math.floor(samples.length / hop) - 1;
  if (count < 2) return [];
  const energy = new Float32Array(count);
  let loudest = 0;
  for (let k = 0; k < count; k++) {
    let sum = 0;
    for (let i = k * hop, end = i + 2 * hop; i < end; i++) sum += samples[i] * samples[i];
    energy[k] = sum / (2 * hop);
    loudest = Math.max(loudest, energy[k]);
  }
  const floor = Math.max(1e-8, loudest * 1e-4), back = Math.round(LOOKBACK / HOP), found = [];
  for (let k = 1; k < count; k++) {
    if (energy[k] < floor) continue;
    let lowest = Infinity;
    for (let j = Math.max(0, k - back); j < k; j++) lowest = Math.min(lowest, energy[j]);
    if (energy[k] < 4 * Math.max(lowest, floor / 10)) continue;
    const time = (k * hop) / rate;
    if (found.length && time - found[found.length - 1] < GAP) continue;
    found.push(time);
  }
  return found;
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

const SVG = "http://www.w3.org/2000/svg";
const element = (name, attributes = {}, text = "") => {
  const node = document.createElementNS(SVG, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  if (text) node.textContent = text;
  return node;
};
const kind = (offset, tolerance) => (offset === null ? "missed" : Math.abs(offset) <= tolerance ? "on-time" : offset < 0 ? "early" : "late");

// The panel after a take: the counts, a histogram of the offsets and a strip through the part played.
export function showScore(ctx, { reference, offsets, tolerance, from, to, history }) {
  const t = ctx.t, result = summary(offsets, tolerance);
  const ms = (seconds) => `${seconds > 0.0005 ? "+" : ""}${Math.round(seconds * 1000)} ms`;
  const percent = result.notes ? Math.round((result.onTime / result.notes) * 100) : 0;
  const dialog = ctx.add(document.body, document.createElement("dialog"));
  dialog.className = "practice-dialog practice-score";
  dialog.innerHTML = `<div class="practice-card">
    <div class="practice-heading"><h2></h2><button type="button" class="icon-button" data-action="close"></button></div>
    <div class="practice-tiles"></div>
    <div class="practice-legend"></div>
    <svg class="practice-histogram" viewBox="0 0 900 170" role="img"></svg>
    <svg class="practice-strip" viewBox="0 0 900 60" role="img"></svg>
    <p class="practice-history"></p>
  </div>`;
  const $ = (selector) => dialog.querySelector(selector);
  $("h2").textContent = t("Take score");
  const close = $('[data-action="close"]');
  close.textContent = "✕";
  close.setAttribute("aria-label", t("Close"));
  close.title = t("Close");
  const tiles = [
    ["main", t("On time"), t("{hit} of {count} · {percent} %", { hit: result.onTime, count: result.notes, percent }), "on-time"],
    ["", t("Early"), String(result.early), "early"],
    ["", t("Late"), String(result.late), "late"],
    ["", t("Missed"), String(result.missed), "missed"],
    ["", t("Average"), ms(result.mean), ""],
    ["", t("Spread"), `±${Math.round(result.spread * 1000)} ms`, ""],
  ];
  $(".practice-tiles").append(...tiles.map(([size, name, value, swatch]) => {
    const tile = document.createElement("div");
    tile.className = `practice-tile ${size}`;
    tile.dataset.tile = swatch || name;
    tile.innerHTML = `<span class="practice-key">${swatch ? `<i class="practice-swatch" data-kind="${swatch}"></i>` : ""}</span><span class="practice-value"></span>`;
    tile.querySelector(".practice-key").append(name);
    tile.querySelector(".practice-value").textContent = value;
    return tile;
  }));
  $(".practice-legend").textContent = t("On time within ±{tolerance} ms; early or late beyond it; missed: no note within 80 ms.", { tolerance: Math.round(tolerance * 1000) });

  // Histogram: 5 ms bins from -70 to +70 ms, the tolerance band behind.
  const histogram = $(".practice-histogram");
  histogram.setAttribute("aria-label", t("How far the notes were from the original, in milliseconds"));
  const left = 30, width = 860, top = 10, height = 130, x = (seconds) => left + ((seconds * 1000 + 70) / 140) * width;
  histogram.append(element("rect", { class: "practice-band", x: x(-tolerance), y: top, width: x(tolerance) - x(-tolerance), height }));
  const bins = new Array(28).fill(0);
  offsets.forEach((offset) => {
    if (offset !== null) bins[Math.min(27, Math.max(0, Math.floor((offset * 1000 + 70) / 5)))]++;
  });
  const tallest = Math.max(1, ...bins);
  bins.forEach((count, bin) => {
    if (!count) return;
    const centre = (bin * 5 - 67.5) / 1000, size = (count / tallest) * (height - 6);
    histogram.append(element("rect", { class: `practice-bar-${kind(centre, tolerance)}`, x: x(centre) - 13, y: top + height - size, width: 26, height: size, rx: 3 },
      ""));
  });
  [-60, -40, -20, 0, 20, 40, 60].forEach((value) => {
    histogram.append(element("line", { class: value ? "practice-tick" : "practice-zero", x1: x(value / 1000), x2: x(value / 1000), y1: top, y2: top + height }));
    histogram.append(element("text", { x: x(value / 1000), y: 162, "text-anchor": "middle" }, `${value > 0 ? "+" : ""}${value}`));
  });

  // The strip: every original note of the take where it is in the song, coloured by how it was played.
  const strip = $(".practice-strip");
  strip.setAttribute("aria-label", t("Every note of the take, from its start to its end"));
  const along = (time) => left + ((time - from) / Math.max(0.001, to - from)) * width;
  strip.append(element("line", { class: "practice-tick", x1: left, x2: left + width, y1: 30, y2: 30 }));
  reference.forEach((time, i) => strip.append(element("rect", { class: `practice-bar-${kind(offsets[i], tolerance)}`, x: along(time) - 1.5, y: offsets[i] === null ? 36 : 14, width: 3, height: 16, rx: 1 })));
  strip.append(element("text", { x: left, y: 58 }, formatTime(from)));
  strip.append(element("text", { x: left + width, y: 58, "text-anchor": "end" }, formatTime(to)));

  if (history.length) {
    $(".practice-history").textContent = t("Earlier takes: {list}", { list: history.map((item) => `${item.notes ? Math.round((item.onTime / item.notes) * 100) : 0} %`).join(" · ") });
  }
  ctx.on(close, "click", () => dialog.close());
  ctx.on(dialog, "close", () => dialog.remove());
  dialog.showModal();
  return result;
}

function formatTime(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
