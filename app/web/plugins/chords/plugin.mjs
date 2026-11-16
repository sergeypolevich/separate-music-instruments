// Chords: "Find chords" runs a background analysis of the stems without vocals and drums (the app's service; it
// takes seconds); the chord names then show in a row over the stems where each chord starts (they come from all
// the stems but vocals and drums, so they belong to none of them). A click on a name edits it
// like the project title: Enter keeps it, Esc cancels, an empty name removes the chord. Edits are kept per project.

export const LABEL_SPACE = 0;  // pixels a chord's line keeps from the name before it; closer names wait for a zoom

const key = (start) => start.toFixed(3);

// The names' size in pixels (Settings → Plugins, version 23): half to three times version 22's 11 px, 20 % over it
// by default; not bold unless asked.
export const SIZE = { min: 5.5, max: 33, normal: 13.2 };

export function look(settings = {}) {
  const size = Number(settings.size);
  return { size: Number.isFinite(size) ? Math.min(SIZE.max, Math.max(SIZE.min, size)) : SIZE.normal, bold: settings.bold === true };
}

const dress = (element, { size, bold }) => {
  element.style.setProperty("--chord-size", `${size}px`);
  element.style.setProperty("--chord-weight", bold ? "650" : "400");
};

const stylesheet = () => {
  if (!document.querySelector('link[href="plugins/chords/plugin.css"]')) {
    document.head.append(Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/chords/plugin.css" }));
  }
};

// The settings: a size slider and a bold switch over a sample row that shows them at once (so does an open project).
export function settings(panel, api) {
  stylesheet();
  const value = look(api.values);
  const size = document.createElement("label");
  size.className = "plugin-setting";
  const sizeName = document.createElement("span");  // a small and a big A instead of a word (version 23)
  sizeName.className = "setting-glyph";
  sizeName.setAttribute("aria-hidden", "true");
  sizeName.innerHTML = '<span class="glyph-small">A</span><span class="glyph-big">A</span>';
  const sizeValue = document.createElement("output");
  const slider = Object.assign(document.createElement("input"), { type: "range", min: SIZE.min, max: SIZE.max, step: 0.1, value: value.size });
  slider.dataset.setting = "size";
  slider.setAttribute("aria-label", api.t("Text size"));
  size.title = api.t("Text size");
  size.append(sizeName, sizeValue, slider);
  const bold = document.createElement("label");
  bold.className = "plugin-switch";
  const boldName = document.createElement("span");  // a plain and a bold B instead of a word
  boldName.className = "setting-glyph";
  boldName.setAttribute("aria-hidden", "true");
  boldName.innerHTML = '<span>B</span><b>B</b>';
  const boldInput = Object.assign(document.createElement("input"), { type: "checkbox", checked: value.bold });
  boldInput.dataset.setting = "bold";
  boldInput.setAttribute("aria-label", api.t("Bold"));
  bold.title = api.t("Bold");
  bold.append(boldName, boldInput);
  const sample = document.createElement("div");
  sample.className = "chord-sample";
  sample.setAttribute("aria-hidden", "true");
  sample.append(...["C", "Am", "F", "G7", "D/F#"].map((name) => Object.assign(document.createElement("span"), { className: "chord-label", textContent: name })));
  const show = () => {
    sizeValue.textContent = `${api.formatDecimal(Math.round(value.size * 10) / 10)} px`;
    dress(sample, value);
  };
  slider.addEventListener("input", () => {
    value.size = Number(slider.value);
    show();
    api.save(value);
  });
  boldInput.addEventListener("change", () => {
    value.bold = boldInput.checked;
    show();
    api.save(value);
  });
  show();
  panel.append(size, bold, sample);
}

// The found chords with the user's edits ({start: name}, "" removes one).
export function applyEdits(chords, edits = {}) {
  return chords
    .map((chord) => (key(chord.start) in edits ? { ...chord, chord: edits[key(chord.start)] } : chord))
    .filter((chord) => chord.chord);
}

// The chords whose name fits: in the view, starting no sooner than LABEL_SPACE after the end of the last name shown
// (widthOf: a name's width in pixels).
export function visibleLabels(chords, view, width, widthOf) {
  const span = view.end - view.start;
  if (!(span > 0) || !(width > 0)) return [];
  const shown = [];
  let last = -Infinity;
  for (const chord of chords) {
    if (chord.end <= view.start || chord.start >= view.end) continue;
    const x = Math.max(0, (chord.start - view.start) / span);
    if (x * width < last + LABEL_SPACE) continue;
    shown.push({ ...chord, x });
    last = x * width + widthOf(chord.chord);
  }
  return shown;
}

const ICON = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';

export function project(ctx) {
  stylesheet();
  const layer = ctx.addLane(document.createElement("div"));
  layer.className = "chord-layer";
  layer.hidden = true;  // no row until chords are found
  dress(layer, look(ctx.settings));
  const widths = new Map();  // a name's width on screen, measured once per size
  const widthOf = (name) => {
    if (!widths.has(name)) {
      const probe = layer.appendChild(Object.assign(document.createElement("span"), { className: "chord-label", textContent: name }));
      widths.set(name, probe.offsetWidth);
      probe.remove();
    }
    return widths.get(name);
  };
  const button = ctx.addFooter(document.createElement("button"));
  button.type = "button";
  button.className = "footer-toggle chords-toggle";
  button.dataset.action = "chords";
  button.innerHTML = `${ICON}<span></span>`;
  const label = button.querySelector("span");
  let found = null;  // the analysis' chords, song time
  let edits = {};
  let shown = true;
  let editing = null;

  const name = () => {
    if (found) label.textContent = shown ? ctx.t("Chords") : ctx.t("Chords hidden");
    else label.textContent = ctx.t("Find chords");
    button.setAttribute("aria-pressed", String(Boolean(found) && shown));
  };
  // The names are drawn once per zoom over the whole song and only moved as the view moves (it follows the
  // playhead); the chord already playing at the view's left edge keeps its name there until the next one comes.
  const track = layer.appendChild(document.createElement("div"));
  track.className = "chord-track";
  const current = Object.assign(document.createElement("span"), { className: "chord-label chord-current" });
  let drawn = "";  // the zoom and width the names are drawn for
  let chords = [];
  let labels = [];
  let covered = null;  // the name under the left edge's copy
  const draw = () => {
    if (!ctx.current || editing) return;
    layer.hidden = !found || !shown;
    const view = ctx.view, whole = ctx.duration, width = layer.clientWidth;
    const span = view.end - view.start;
    if (layer.hidden || !(whole > 0 && span > 0 && width > 0)) {
      drawn = "";
      return;
    }
    const zoom = `${span.toFixed(6)} ${width}`;
    if (zoom !== drawn) {
      drawn = zoom;
      covered = null;
      chords = applyEdits(found, edits);
      labels = visibleLabels(chords, { start: 0, end: whole }, (width * whole) / span, widthOf);
      track.style.width = `${(whole / span) * 100}%`;
      track.replaceChildren(...labels.map((chord) => {
        const item = document.createElement("span");
        item.className = "chord-label";
        item.dataset.start = key(chord.start);
        item.style.left = `${(chord.x * 100).toFixed(4)}%`;
        item.textContent = chord.chord;
        item.title = ctx.t("Click to correct the chord");
        chord.element = item;
        return item;
      }));
    }
    track.style.transform = `translateX(${(-view.start / whole) * 100}%)`;
    const now = chords.find((chord) => chord.start < view.start && chord.end > view.start);
    const next = labels.find((chord) => chord.start > view.start);
    const keep = now && (!next || ((next.start - view.start) / span) * width >= widthOf(now.chord) + LABEL_SPACE);
    if (keep) {
      if (current.dataset.start !== key(now.start) || current.textContent !== now.chord) {
        current.dataset.start = key(now.start);
        current.textContent = now.chord;
        current.title = ctx.t("Click to correct the chord");
      }
      if (!current.isConnected) layer.append(current);
    } else current.remove();
    const under = keep ? labels.find((chord) => chord.start === now.start)?.element || null : null;
    if (under !== covered) {
      covered?.style.removeProperty("visibility");
      under?.style.setProperty("visibility", "hidden");
      covered = under;
    }
  };
  const redraw = () => { drawn = ""; draw(); };
  const save = () => ctx.saveData({ edits });

  // A click on a name edits it in place.
  ctx.on(layer, "click", (event) => {
    const item = event.target.closest(".chord-label");
    if (!item || editing) return;
    editing = item;
    const before = item.textContent;
    item.contentEditable = "plaintext-only";
    item.spellcheck = false;
    item.focus();
    document.getSelection().selectAllChildren(item);
    const finish = (keep) => {
      item.removeEventListener("keydown", keys);
      item.removeEventListener("blur", blur);
      item.contentEditable = "false";
      editing = null;
      const text = item.textContent.trim().slice(0, 12);
      if (keep && text !== before) {
        edits[item.dataset.start] = text;
        save();
      }
      redraw();
    };
    const keys = (key) => {
      key.stopPropagation();  // the player's hotkeys wait
      if (key.key === "Enter") { key.preventDefault(); finish(true); }
      if (key.key === "Escape") { key.preventDefault(); finish(false); }
    };
    const blur = () => finish(true);
    item.addEventListener("keydown", keys);
    item.addEventListener("blur", blur);
  });

  ctx.on(button, "click", async () => {
    if (found) {
      shown = !shown;
      name();
      redraw();
      return;
    }
    button.disabled = true;
    button.dataset.analyzing = "true";
    try {
      const result = await ctx.analyze("chords", (progress) => {
        button.style.setProperty("--job-progress", `${Math.round(progress * 100)}%`);
        label.textContent = ctx.t("Finding chords · {percent}%", { percent: Math.round(progress * 100) });
      });
      if (result) found = result.chords;
    } catch (error) {
      label.textContent = error.message;  // the analysis failed: said on the button, the plugin stays on
      return;
    } finally {
      button.disabled = false;
      delete button.dataset.analyzing;
      button.style.removeProperty("--job-progress");
    }
    name();
    redraw();
  });
  ctx.onView(draw);
  ctx.onSettings((values) => {
    dress(layer, look(values));
    widths.clear();
    redraw();
  });
  const resize = new ResizeObserver(() => draw());
  resize.observe(layer);
  name();

  return Promise.all([ctx.loadData(), ctx.analysisState("chords")]).then(([saved, state]) => {
    edits = saved.edits && typeof saved.edits === "object" ? saved.edits : {};
    if (state.status === "done" && state.result) found = state.result.chords;
    name();
    redraw();
    return () => resize.disconnect();
  });
}
