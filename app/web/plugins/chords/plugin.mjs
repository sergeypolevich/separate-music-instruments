// Chords: "Find chords" runs a background analysis of the stems without vocals and drums (the app's service; it
// takes seconds); the chord names then show over the waveforms where each chord starts. A click on a name edits it
// like the project title: Enter keeps it, Esc cancels, an empty name removes the chord. Edits are kept per project.

export const LABEL_GAP = 34;  // pixels between two names shown; closer ones wait for a zoom

const key = (start) => start.toFixed(3);

// The found chords with the user's edits ({start: name}, "" removes one).
export function applyEdits(chords, edits = {}) {
  return chords
    .map((chord) => (key(chord.start) in edits ? { ...chord, chord: edits[key(chord.start)] } : chord))
    .filter((chord) => chord.chord);
}

// The chords whose name fits: in the view, at least LABEL_GAP pixels after the last one shown.
export function visibleLabels(chords, view, width) {
  const span = view.end - view.start;
  if (!(span > 0) || !(width > 0)) return [];
  const shown = [];
  let last = -Infinity;
  for (const chord of chords) {
    if (chord.end <= view.start || chord.start >= view.end) continue;
    const x = Math.max(0, (chord.start - view.start) / span);
    if (x * width - last < LABEL_GAP) continue;
    shown.push({ ...chord, x });
    last = x * width;
  }
  return shown;
}

const ICON = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';

export function project(ctx) {
  ctx.add(document.head, Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/chords/plugin.css" }));
  const layer = ctx.add(ctx.overlay, document.createElement("div"));
  layer.className = "chord-layer";
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
  const draw = () => {
    if (!ctx.current || editing) return;
    layer.hidden = !found || !shown;
    if (layer.hidden) return;
    layer.replaceChildren(...visibleLabels(applyEdits(found, edits), ctx.view, layer.clientWidth).map((chord) => {
      const item = document.createElement("span");
      item.className = "chord-label";
      item.dataset.start = key(chord.start);
      item.style.left = `${(chord.x * 100).toFixed(3)}%`;
      item.textContent = chord.chord;
      item.title = ctx.t("Click to correct the chord");
      return item;
    }));
  };
  const save = () => ctx.saveData({ edits });

  // A click on a name edits it in place; the click does not seek.
  ctx.on(layer, "pointerdown", (event) => { if (event.target.closest(".chord-label")) event.stopPropagation(); });
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
      draw();
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
      draw();
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
    draw();
  });
  ctx.onView(draw);
  const resize = new ResizeObserver(() => draw());
  resize.observe(layer);
  name();

  return Promise.all([ctx.loadData(), ctx.analysisState("chords")]).then(([saved, state]) => {
    edits = saved.edits && typeof saved.edits === "object" ? saved.edits : {};
    if (state.status === "done" && state.result) found = state.result.chords;
    name();
    draw();
    return () => resize.disconnect();
  });
}
