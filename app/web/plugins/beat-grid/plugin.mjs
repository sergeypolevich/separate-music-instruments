// Beat grid: lines on the song's beats over the waveforms. Bar starts are strong lines, the other beats pale ones;
// the pale ones show only when they are at least WEAK_GAP pixels apart, the strong ones STRONG_GAP (zoom in for more).
// The beats are the metronome's: found once per song, in the background, when a project opens.

export const WEAK_GAP = 12;
export const STRONG_GAP = 6;

// [{x: share of the view 0..1, strong}] for the beats in view; without known bar starts every fourth beat is one.
export function gridLines(beats, downbeats, view, width) {
  const span = view.end - view.start;
  if (!(span > 0) || !(width > 0) || beats.length < 2) return [];
  const bars = downbeats.length ? new Set(downbeats.map((time) => time.toFixed(3))) : null;
  const isStrong = (time, index) => (bars ? bars.has(time.toFixed(3)) : index % 4 === 0);
  const beatGap = ((beats[beats.length - 1] - beats[0]) / (beats.length - 1)) * (width / span);
  const barGap = beatGap * (bars ? Math.max(1, beats.length / Math.max(1, bars.size)) : 4);
  const lines = [];
  beats.forEach((time, index) => {
    if (time < view.start || time > view.end) return;
    const strong = isStrong(time, index);
    if (strong ? barGap >= STRONG_GAP : beatGap >= WEAK_GAP) lines.push({ x: (time - view.start) / span, strong });
  });
  return lines;
}

export function project(ctx) {
  const layer = ctx.add(ctx.overlay, document.createElement("div"));
  layer.className = "beat-grid";
  ctx.add(document.head, Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/beat-grid/plugin.css" }));
  // The lines are drawn once per zoom over the whole song and only moved as the view moves (it follows the playhead).
  // Each is one screen pixel wide on whole screen pixels, like the playhead: a 1px line at a fractional position was
  // drawn one or two pixels wide by turns and flickered as it moved.
  const track = layer.appendChild(document.createElement("div"));
  track.className = "beat-track";
  let found = null;
  let drawn = "";  // the zoom, width and screen density the lines are drawn for
  let origin = 0;  // the layer's left edge on the page
  const draw = () => {
    if (!found || !ctx.current) return;
    const view = ctx.view, whole = ctx.duration, width = layer.clientWidth;
    const span = view.end - view.start;
    if (!(whole > 0 && span > 0)) return;
    const scale = window.devicePixelRatio || 1;
    const full = (width * whole) / span;  // the whole song's width at this zoom
    const zoom = `${span.toFixed(6)} ${width} ${scale}`;
    if (zoom !== drawn) {
      drawn = zoom;
      origin = layer.getBoundingClientRect().left + window.scrollX;
      track.style.width = `${full}px`;
      track.style.setProperty("--line", `${1 / scale}px`);
      track.replaceChildren(...gridLines(found.beats || [], found.downbeats || [], { start: 0, end: whole }, full).map(({ x, strong }) => {
        const line = document.createElement("span");
        line.className = strong ? "beat-line strong" : "beat-line";
        line.style.left = `${Math.round(x * full * scale) / scale}px`;
        return line;
      }));
    }
    const shift = Math.round((origin - (view.start / whole) * full) * scale) / scale - origin;
    track.style.transform = `translateX(${shift}px)`;
  };
  ctx.onView(draw);
  const resize = new ResizeObserver(() => draw());
  resize.observe(layer);
  ctx.beats().then((result) => {
    found = result;  // null: no steady beat in this song, no grid
    draw();
  });
  return () => resize.disconnect();
}
