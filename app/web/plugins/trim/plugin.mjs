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

const ICONS = {
  start: '<path d="M4 4v16"/><path d="M20 12H9"/><path d="m13 8-4 4 4 4"/>',
  end: '<path d="M20 4v16"/><path d="M4 12h11"/><path d="m11 8 4 4-4 4"/>',
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
