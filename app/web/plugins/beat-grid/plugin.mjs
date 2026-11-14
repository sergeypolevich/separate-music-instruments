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
  let found = null;
  const draw = () => {
    if (!found || !ctx.current) return;
    layer.replaceChildren(...gridLines(found.beats || [], found.downbeats || [], ctx.view, layer.clientWidth).map(({ x, strong }) => {
      const line = document.createElement("span");
      line.className = strong ? "beat-line strong" : "beat-line";
      line.style.left = `${(x * 100).toFixed(3)}%`;
      return line;
    }));
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
