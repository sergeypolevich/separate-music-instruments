// Wheel zoom: the wheel (or a trackpad pinch, or two fingers on a phone) zooms the waveforms around the pointer;
// Shift + wheel, a sideways swipe or two fingers moving sideways move the view. While playing, the view turns a page
// when the playhead leaves it; a zoom or a move by hand stops that until playback starts again.

export const MIN_SECONDS = 3;  // the shortest view

const clampView = (start, length, bounds) => {
  const from = Math.min(Math.max(bounds.start, start), bounds.end - length);
  return { start: from, end: from + length };
};

// The view `factor` times longer (below 1: shorter), the time under the pointer kept where it is.
export function zoomAround(view, bounds, anchor, factor) {
  const whole = bounds.end - bounds.start;
  const length = Math.min(whole, Math.max(Math.min(MIN_SECONDS, whole), (view.end - view.start) * factor));
  const share = (anchor - view.start) / (view.end - view.start);
  return clampView(anchor - share * length, length, bounds);
}

export const panBy = (view, bounds, seconds) => clampView(view.start + seconds, view.end - view.start, bounds);

// While playing: the next page when the playhead has left the view (it starts at the view's left edge).
export function followPlayhead(view, bounds, position) {
  if (position >= view.start && position <= view.end) return null;
  return clampView(position, view.end - view.start, bounds);
}

export function project(ctx) {
  const timeline = ctx.timeline;
  let detailed = false;
  let following = true;
  let wasPlaying = false;
  const whole = () => {
    const view = ctx.view, bounds = ctx.bounds;
    return view.end - view.start >= bounds.end - bounds.start - 1e-6;
  };
  const show = (view) => {
    const bounds = ctx.bounds;
    ctx.setView(view.end - view.start >= bounds.end - bounds.start - 1e-6 ? null : view);
    if (!detailed && !whole()) {
      detailed = true;
      ctx.detailedWaveforms();  // 8192 peaks per stem, asked for on the first zoom only
    }
  };
  const timeAt = (clientX) => {
    const rect = timeline.getBoundingClientRect(), view = ctx.view;
    return view.start + ((clientX - rect.left) / rect.width) * (view.end - view.start);
  };

  ctx.on(timeline, "wheel", (event) => {
    if (!(ctx.duration > 0)) return;
    event.preventDefault();  // the page does not scroll over the waveforms
    following = false;
    const view = ctx.view;
    const lines = event.deltaMode === 1 ? 16 : 1;
    const sideways = event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY);
    if (sideways) {
      const delta = (Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY) * lines;
      show(panBy(view, ctx.bounds, (delta / timeline.clientWidth) * (view.end - view.start)));
      return;
    }
    // A pinch on a trackpad comes as the wheel with Ctrl and small steps.
    const factor = Math.exp(event.deltaY * lines * (event.ctrlKey ? 0.01 : 0.0015));
    show(zoomAround(view, ctx.bounds, timeAt(event.clientX), factor));
  }, { passive: false });

  // Two fingers on a touch screen: pinch to zoom, move sideways to pan.
  let pinch = null;
  const fingers = (event) => [...event.touches].slice(0, 2).map((touch) => touch.clientX);
  ctx.on(timeline, "touchstart", (event) => {
    if (event.touches.length !== 2 || !(ctx.duration > 0)) return;
    const [a, b] = fingers(event);
    pinch = { distance: Math.max(20, Math.abs(a - b)), centre: (a + b) / 2, view: ctx.view };
    following = false;
  }, { passive: true });
  ctx.on(timeline, "touchmove", (event) => {
    if (!pinch || event.touches.length !== 2) return;
    event.preventDefault();
    const [a, b] = fingers(event);
    const { view, distance, centre } = pinch;
    const rect = timeline.getBoundingClientRect();
    const anchor = view.start + ((centre - rect.left) / rect.width) * (view.end - view.start);
    const zoomed = zoomAround(view, ctx.bounds, anchor, distance / Math.max(20, Math.abs(a - b)));
    show(panBy(zoomed, ctx.bounds, -(((a + b) / 2 - centre) / rect.width) * (zoomed.end - zoomed.start)));
  }, { passive: false });
  ctx.on(timeline, "touchend", () => { pinch = null; });

  ctx.onTime((position, playing) => {
    if (playing && !wasPlaying) following = true;  // ▶ follows again
    wasPlaying = playing;
    if (!playing || !following || whole()) return;
    const next = followPlayhead(ctx.view, ctx.bounds, position);
    if (next) ctx.setView(next);
  });
}
