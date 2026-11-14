// Equalizer: a parametric EQ for every stem, like ReaEQ: four bands (a low shelf, two bells, a high shelf) whose
// points are dragged on the curve (frequency and gain); the wheel over a point sets a bell's width, a double click
// puts the band back to 0 dB. It sits after the stem's volume and changes only playback, never the files; a stem
// whose bands are all at 0 dB plays without it. The bands are kept per project.

export const MIN_HZ = 20;
export const MAX_HZ = 20000;
export const MAX_DB = 18;
export const defaultBands = () => [
  { type: "lowshelf", frequency: 100, gain: 0, q: 0.7 },
  { type: "peaking", frequency: 400, gain: 0, q: 1 },
  { type: "peaking", frequency: 2500, gain: 0, q: 1 },
  { type: "highshelf", frequency: 8000, gain: 0, q: 0.7 },
];

export const flat = (bands) => bands.every((band) => Math.abs(band.gain) < 0.05);
// Positions on the graph as shares 0..1: frequency on a log scale, gain from +MAX_DB at the top to -MAX_DB.
export const xOfFrequency = (hz) => Math.log(hz / MIN_HZ) / Math.log(MAX_HZ / MIN_HZ);
export const frequencyOfX = (x) => MIN_HZ * (MAX_HZ / MIN_HZ) ** Math.min(1, Math.max(0, x));
export const yOfGain = (db) => 0.5 - db / (2 * MAX_DB);
export const gainOfY = (y) => Math.max(-MAX_DB, Math.min(MAX_DB, (0.5 - y) * 2 * MAX_DB));

// Saved bands back as four valid bands (anything else in the data falls back to the default).
export function readBands(saved) {
  const bands = defaultBands();
  if (!Array.isArray(saved) || saved.length !== bands.length) return bands;
  return bands.map((band, index) => {
    const value = saved[index] || {};
    const number = (x, low, high, fallback) => (Number.isFinite(x) ? Math.min(high, Math.max(low, x)) : fallback);
    return { type: band.type, frequency: number(value.frequency, MIN_HZ, MAX_HZ, band.frequency),
      gain: number(value.gain, -MAX_DB, MAX_DB, 0), q: number(value.q, 0.2, 12, band.q) };
  });
}

const formatHz = (ctx, hz) => (hz >= 1000 ? ctx.t("{n} kHz", { n: ctx.formatDecimal(hz / 1000) }) : ctx.t("{n} Hz", { n: Math.round(hz) }));

export function project(ctx) {
  ctx.add(document.head, Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/eq/plugin.css" }));
  const bandsByStem = ctx.stems.map(() => defaultBands());
  const filters = ctx.stems.map(() => null);  // BiquadFilterNodes of a stem, made the first time it is not flat
  const buttons = [];
  let saveTimer = null;

  const apply = (index) => {
    const bands = bandsByStem[index];
    if (flat(bands)) {
      ctx.setEffect(index, null);
    } else {
      if (!filters[index]) {
        filters[index] = bands.map(() => ctx.audioContext.createBiquadFilter());
        filters[index].reduce((previous, next) => (previous.connect(next), next));
      }
      filters[index].forEach((filter, band) => {
        filter.type = bands[band].type;
        filter.frequency.value = bands[band].frequency;
        filter.gain.value = bands[band].gain;
        filter.Q.value = bands[band].q;
      });
      ctx.setEffect(index, { input: filters[index][0], output: filters[index][filters[index].length - 1] });
    }
    buttons[index].setAttribute("aria-pressed", String(!flat(bands)));
  };
  const save = () => {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      const stems = {};
      ctx.stems.forEach((stem, index) => { if (!flat(bandsByStem[index])) stems[stem.name] = bandsByStem[index]; });
      ctx.saveData({ stems });
    }, 400);
  };

  ctx.stems.forEach((stem, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mixer-toggle eq-toggle";
    button.textContent = "EQ";
    button.dataset.control = "eq";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", ctx.t("Equalizer: {stem}", { stem: stem.label }));
    button.title = ctx.t("Equalizer");
    buttons.push(ctx.addStemControl(index, button));
    ctx.on(button, "click", () => openEditor(index));
  });

  // The editor: one dialog for the stem whose EQ button was pressed.
  const dialog = ctx.add(document.body, document.createElement("dialog"));
  dialog.className = "eq-dialog";
  dialog.innerHTML = `<form method="dialog" class="eq-card">
    <div class="eq-heading"><h2></h2><button class="icon-button" value="close" aria-label="Close" title="Close">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button></div>
    <canvas class="eq-graph" width="640" height="260"></canvas>
    <div class="eq-footer"><span class="eq-readout"></span><button type="button" class="quiet-button" data-action="eq-reset"></button></div>
  </form>`;
  dialog.querySelector('[value="close"]').setAttribute("aria-label", ctx.t("Close"));
  dialog.querySelector('[value="close"]').title = ctx.t("Close");
  dialog.querySelector('[data-action="eq-reset"]').textContent = ctx.t("Reset");
  const title = dialog.querySelector("h2");
  const canvas = dialog.querySelector("canvas");
  const readout = dialog.querySelector(".eq-readout");
  let current = 0;
  let selected = 1;
  let dragging = null;
  const typeName = (type) => ({ lowshelf: ctx.t("Low shelf"), highshelf: ctx.t("High shelf") }[type] || ctx.t("Bell"));

  const draw = () => {
    const bands = bandsByStem[current];
    const scale = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 640, height = canvas.clientHeight || 260;
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const g = canvas.getContext("2d");
    g.setTransform(scale, 0, 0, scale, 0, 0);
    const style = getComputedStyle(document.documentElement);
    const color = (name) => style.getPropertyValue(name).trim();
    g.clearRect(0, 0, width, height);
    g.strokeStyle = color("--line");
    g.lineWidth = 1;
    for (const hz of [50, 100, 200, 500, 1000, 2000, 5000, 10000]) {
      const x = Math.round(xOfFrequency(hz) * width) + 0.5;
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x, height); g.stroke();
    }
    for (const db of [-12, -6, 0, 6, 12]) {
      const y = Math.round(yOfGain(db) * height) + 0.5;
      g.beginPath(); g.moveTo(0, y); g.lineTo(width, y); g.stroke();
    }
    g.fillStyle = color("--muted");
    g.font = "11px system-ui, sans-serif";
    for (const hz of [100, 1000, 10000]) g.fillText(hz >= 1000 ? `${hz / 1000}k` : String(hz), xOfFrequency(hz) * width + 4, height - 6);
    // The curve: the four filters' response, multiplied (dB added).
    const points = 256;
    const frequencies = new Float32Array(points).map((_value, i) => frequencyOfX(i / (points - 1)));
    const total = new Float32Array(points);
    const probe = ctx.audioContext.createBiquadFilter();
    const magnitude = new Float32Array(points), phase = new Float32Array(points);
    bands.forEach((band) => {
      probe.type = band.type;
      probe.frequency.value = band.frequency;
      probe.gain.value = band.gain;
      probe.Q.value = band.q;
      probe.getFrequencyResponse(frequencies, magnitude, phase);
      magnitude.forEach((value, i) => { total[i] += 20 * Math.log10(Math.max(value, 1e-6)); });
    });
    g.strokeStyle = color("--accent");
    g.lineWidth = 2;
    g.beginPath();
    total.forEach((db, i) => {
      const x = (i / (points - 1)) * width, y = yOfGain(Math.max(-MAX_DB, Math.min(MAX_DB, db))) * height;
      if (i) g.lineTo(x, y); else g.moveTo(x, y);
    });
    g.stroke();
    bands.forEach((band, index) => {
      g.beginPath();
      g.arc(xOfFrequency(band.frequency) * width, yOfGain(band.gain) * height, index === selected ? 7 : 5.5, 0, 2 * Math.PI);
      g.fillStyle = index === selected ? color("--accent") : color("--panel");
      g.fill();
      g.strokeStyle = color("--accent");
      g.lineWidth = 2;
      g.stroke();
    });
    const band = bands[selected];
    const sign = band.gain > 0 ? "+" : "";
    readout.textContent = `${typeName(band.type)} · ${formatHz(ctx, band.frequency)} · ${ctx.t("{n} dB", { n: `${sign}${ctx.formatDecimal(band.gain)}` })}`
      + (band.type === "peaking" ? ` · Q ${ctx.formatDecimal(band.q)}` : "");
  };
  const changed = () => {
    apply(current);
    draw();
    save();
  };
  const nearest = (event) => {
    const rect = canvas.getBoundingClientRect();
    let best = null, distance = 18;
    bandsByStem[current].forEach((band, index) => {
      const d = Math.hypot(xOfFrequency(band.frequency) * rect.width - (event.clientX - rect.left), yOfGain(band.gain) * rect.height - (event.clientY - rect.top));
      if (d < distance) { best = index; distance = d; }
    });
    return best;
  };
  ctx.on(canvas, "pointerdown", (event) => {
    const index = nearest(event);
    if (index === null) return;
    selected = index;
    dragging = event.pointerId;
    canvas.setPointerCapture(event.pointerId);
    draw();
  });
  ctx.on(canvas, "pointermove", (event) => {
    if (dragging !== event.pointerId) return;
    const rect = canvas.getBoundingClientRect();
    const band = bandsByStem[current][selected];
    band.frequency = Math.round(frequencyOfX((event.clientX - rect.left) / rect.width));
    band.gain = Math.round(gainOfY((event.clientY - rect.top) / rect.height) * 10) / 10;
    changed();
  });
  ctx.on(canvas, "pointerup", () => { dragging = null; });
  ctx.on(canvas, "wheel", (event) => {
    event.preventDefault();
    const band = bandsByStem[current][nearest(event) ?? selected];
    band.q = Math.round(Math.min(12, Math.max(0.2, band.q * Math.exp(-event.deltaY * 0.002))) * 100) / 100;
    changed();
  }, { passive: false });
  ctx.on(canvas, "dblclick", (event) => {
    const index = nearest(event);
    if (index === null) return;
    bandsByStem[current][index] = { ...defaultBands()[index] };
    selected = index;
    changed();
  });
  ctx.on(dialog.querySelector('[data-action="eq-reset"]'), "click", () => {
    bandsByStem[current] = defaultBands();
    changed();
  });
  const openEditor = (index) => {
    current = index;
    title.textContent = ctx.t("Equalizer · {stem}", { stem: ctx.stems[index].label });
    dialog.showModal();
    draw();
  };

  return ctx.loadData().then((saved) => {
    ctx.stems.forEach((stem, index) => {
      if (saved.stems && saved.stems[stem.name]) bandsByStem[index] = readBands(saved.stems[stem.name]);
      apply(index);
    });
    return () => {
      window.clearTimeout(saveTimer);
      if (dialog.open) dialog.close();
    };
  });
}
