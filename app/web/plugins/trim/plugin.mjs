// Trim: cut off the start or the end of the song at the playhead. Only the bounds are kept (per project); the files
// stay whole and "Restore" brings the whole song back. Playback stays inside the bounds and the waveforms show only
// them; the metronome keeps the song's own beats; a trim drops the loop.

export const MIN_SECONDS = 1;  // the shortest song a trim may leave

// The bounds after cutting at `position` ("start" or "end"), or null when too little would be left.
export function trimmed(range, duration, side, position) {
  const start = side === "start" ? position : range ? range.start : 0;
  const end = side === "end" ? position : range ? range.end : duration;
  return end - start >= MIN_SECONDS ? { start: Math.max(0, start), end: Math.min(duration, end) } : null;
}

// A track with a cut in the middle and scissors on the side cut off; that part is hatched.
const SCISSORS = '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88"/><path d="M14.47 14.48 20 20"/><path d="M8.12 8.12 12 12"/>';
const trimIcon = (side) => {
  const cut = side === "start" ? 2 : 12;  // the hatched half
  const scissors = side === "start" ? "translate(1 0) scale(.42)" : "translate(23 0) scale(-.42 .42)";
  return `<defs><clipPath id="trim-cut-${side}"><rect x="${cut}" y="13" width="10" height="8"/></clipPath></defs>`
    + `<g clip-path="url(#trim-cut-${side})" stroke-width="1"><path d="M0 23 6 11M4 23l6-12M8 23l6-12M12 23l6-12M16 23l6-12M20 23l6-12"/></g>`
    + '<rect x="2" y="13" width="20" height="8" rx="1.5"/><path d="M12 10v14"/>'
    + `<g transform="${scissors}" stroke-width="4">${SCISSORS}</g>`;
};
const ICONS = {
  start: trimIcon("start"),
  end: trimIcon("end"),
  restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
};

export function project(ctx) {
  const group = ctx.addFooter(document.createElement("div"));
  group.className = "trim-buttons";
  ctx.add(document.head, Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/trim/plugin.css" }));
  const make = (action, label, title) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "footer-toggle trim-button";
    button.dataset.action = action;
    button.title = title;
    button.innerHTML = `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICONS[action.replace("trim-", "")]}</svg><span></span>`;
    button.querySelector("span").textContent = label;
    group.append(button);
    return button;
  };
  const start = make("trim-start", ctx.t("Trim the start"), ctx.t("Cut off everything before the playhead"));
  const end = make("trim-end", ctx.t("Trim the end"), ctx.t("Cut off everything after the playhead"));
  const restore = make("trim-restore", ctx.t("Whole song"), ctx.t("Bring back the trimmed start and end"));
  const show = () => { restore.hidden = !ctx.range(); };
  const apply = (range) => {
    ctx.dropLoop();
    ctx.setRange(range);
    show();
    return ctx.saveData(range ? { start: range.start, end: range.end } : {});
  };
  const cut = (side) => {
    const next = trimmed(ctx.range(), ctx.duration, side, ctx.position);
    if (next) return apply(next);
    return undefined;
  };
  ctx.on(start, "click", () => cut("start"));
  ctx.on(end, "click", () => cut("end"));
  ctx.on(restore, "click", () => apply(null));
  show();
  return ctx.loadData().then((saved) => {
    if (Number.isFinite(saved.start) && Number.isFinite(saved.end) && ctx.current) {
      ctx.setRange({ start: saved.start, end: saved.end });
      show();
    }
  });
}
