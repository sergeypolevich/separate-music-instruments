// The practice plugin's latency tuner: clicks one at a time on the AudioContext the song plays on, each found in
// the input (an earcup held to the pickup); the estimate is the median so far, and the recorded lane slides to it.
// Only the drawing moves; the clicks sound as scheduled. Stored per input and output (latencyKey).

export const BEFORE = 0.05;  // seconds of input before a click: its noise floor
export const WINDOW = 0.5;  // the longest latency looked for
export const MIN_CLICKS = 6, MAX_CLICKS = 16, AGREE = 0.0008;  // stop when the last 5 agree within 0.8 ms

// The first sample of a click in `samples` (input from BEFORE seconds before it was played): where the level rises
// to 30 % of the way from the noise floor to the peak. -1 when no click stands out of the noise.
export function findClick(samples, before) {
  let noise = 0, peak = 0;
  for (let i = 0; i < before; i++) noise = Math.max(noise, Math.abs(samples[i]));
  for (let i = before; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
  if (peak < Math.max(0.01, noise * 3)) return -1;
  const threshold = noise + (peak - noise) * 0.3;
  for (let i = before; i < samples.length; i++) if (Math.abs(samples[i]) >= threshold) return i;
  return -1;
}

export function median(values) {
  const sorted = [...values].sort((a, b) => a - b), middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// The state after each click: `found` are the measured latencies (seconds) and `tried` the clicks played.
export function settle(found, tried) {
  const last = found.slice(-5);
  const spread = last.length > 1 ? Math.max(...last) - Math.min(...last) : null;
  const estimate = found.length ? median(found) : null;
  const stable = found.length >= MIN_CLICKS && spread !== null && spread < AGREE;
  const silent = tried >= 3 && found.length === 0;  // nothing heard: the earcup is not at the pickup
  return { estimate, spread, stable, done: stable || silent || tried >= MAX_CLICKS, silent };
}

const ms = (seconds, formatDecimal) => `${formatDecimal(Math.round(seconds * 10000) / 10)} ms`;
const clickShape = (t) => (t < 0 || t > 0.03 ? 0 : Math.exp(-t / 0.004) * Math.sin(2 * Math.PI * 2000 * t));

// The dialog. openInput(onBlock) starts the input (blocks of {frame, samples}); save(key, seconds) stores a result.
export function openTuner(ctx, { openInput, save, known }) {
  const t = ctx.t;
  const audio = ctx.audioContext;
  const dialog = ctx.add(document.body, document.createElement("dialog"));
  dialog.className = "practice-dialog";
  dialog.innerHTML = `<div class="practice-card">
    <div class="practice-heading"><h2></h2><button type="button" class="icon-button" data-action="close"></button></div>
    <p class="practice-hint"></p>
    <div class="practice-row"><button type="button" class="primary-button" data-action="measure"></button><div class="practice-dots"></div></div>
    <div class="practice-result" aria-live="polite"><span class="practice-big"></span><span class="practice-pill" hidden></span><span class="practice-detail"></span></div>
    <canvas class="practice-tune-lanes" aria-hidden="true"></canvas>
    <h3></h3>
    <canvas class="practice-close-up" aria-hidden="true"></canvas>
  </div>`;
  const $ = (selector) => dialog.querySelector(selector);
  $("h2").textContent = t("Latency");
  $("h3").textContent = t("Last click, close up");
  $(".practice-hint").textContent = t("Hold one earcup against the pickup of your instrument (or the microphone), then press Start. Clicks play one at a time; the recorded ones move into line with the played ones.");
  const close = $('[data-action="close"]');
  close.textContent = "✕";
  close.setAttribute("aria-label", t("Close"));
  close.title = t("Close");
  const button = $('[data-action="measure"]');
  const dots = $(".practice-dots"), big = $(".practice-big"), pill = $(".practice-pill"), detail = $(".practice-detail");

  let run = null;  // {capture, clicks: [{at, latency|null|undefined}], blocks: [{frame, samples}], key}
  let shift = known || 0, frame = 0;
  big.textContent = known ? ms(known, ctx.formatDecimal) : "—";
  detail.textContent = known ? t("Measured before for this input and output") : t("Not measured yet");
  button.textContent = t("Start");

  const found = () => run.clicks.filter((click) => click.latency).map((click) => click.latency);
  function click(at) {
    const oscillator = audio.createOscillator(), envelope = audio.createGain();
    oscillator.frequency.value = 2000;
    envelope.gain.setValueAtTime(0, at);
    envelope.gain.linearRampToValueAtTime(0.5, at + 0.001);
    envelope.gain.exponentialRampToValueAtTime(0.001, at + 0.025);
    oscillator.connect(envelope).connect(audio.destination);
    oscillator.start(at);
    oscillator.stop(at + 0.03);
    return oscillator;
  }
  // Input from `from` to `to` (context seconds) out of the kept blocks; null until all of it has arrived.
  function input(from, to) {
    const rate = audio.sampleRate, first = Math.round(from * rate), length = Math.round((to - from) * rate);
    const last = run.blocks.at(-1);
    if (!last || last.frame + last.samples.length < first + length) return null;
    const out = new Float32Array(length);
    for (const block of run.blocks) {
      const start = Math.max(first, block.frame), end = Math.min(first + length, block.frame + block.samples.length);
      for (let i = start; i < end; i++) out[i - first] = block.samples[i - block.frame];
    }
    return out;
  }

  function finish(state) {
    const clicks = found();
    button.textContent = t("Start again");
    pill.hidden = false;
    run.capture.close();
    if (state.stable) {
      save(run.key, state.estimate);
      pill.textContent = t("Stable");
      pill.dataset.state = "ok";
      detail.textContent = t("{count} clicks · spread {spread} · saved for this input and output", { count: clicks.length, spread: ms(state.spread, ctx.formatDecimal) });
    } else {
      pill.textContent = state.silent ? t("No click heard") : t("Not stable");
      pill.dataset.state = "bad";
      detail.textContent = state.silent
        ? t("Hold the earcup closer to the pickup or turn the volume up.")
        : t("The clicks came back at different times; wireless headphones cannot be measured. Not saved.");
    }
    run.done = true;
    run.clicks.filter((item) => item.latency === undefined).forEach((item) => {  // still to come: not played
      try { item.scheduled.stop(); } catch (_error) { /* already ended */ }
      item.dropped = true;
    });
    run.end = audio.currentTime + 0.3;  // the lanes roll on a little, then stand still with the last clicks in view
  }

  function step() {
    frame = requestAnimationFrame(step);
    if (run && !run.done) {
      const now = audio.currentTime, last = run.clicks.at(-1);
      if (run.clicks.length < MAX_CLICKS && (!last || now >= last.at)) run.clicks.push({ at: (last ? last.at : now + 0.3) + (last ? 0.45 + Math.random() * 0.3 : 0) });
      for (const item of run.clicks) {
        if (run.done) break;
        if (item.latency !== undefined || item.scheduled === undefined) continue;
        const samples = input(item.at - BEFORE, item.at + WINDOW);
        if (!samples) continue;
        const index = findClick(samples, Math.round(BEFORE * audio.sampleRate));
        item.latency = index < 0 ? null : index / audio.sampleRate - BEFORE;
        const dot = dots.appendChild(document.createElement("span"));
        dot.dataset.found = String(index >= 0);
        dot.textContent = String(run.clicks.indexOf(item) + 1);
        const state = settle(found(), run.clicks.filter((c) => c.latency !== undefined).length);
        if (state.estimate !== null) {
          big.textContent = ms(state.estimate, ctx.formatDecimal);
          detail.textContent = state.spread === null ? t("Click 1") : t("Click {count} · spread {spread}", { count: found().length, spread: ms(state.spread, ctx.formatDecimal) });
        }
        if (state.done) finish(state);
      }
      run.clicks.forEach((item) => {
        if (item.scheduled === undefined && !run.done) item.scheduled = click(item.at);
      });
      const rate = audio.sampleRate, keep = (now - 4) * rate;  // the last 4 s of input are enough to draw and search
      while (run.blocks.length && run.blocks[0].frame + run.blocks[0].samples.length < keep) run.blocks.shift();
    }
    const estimate = run && found().length ? median(found()) : known;
    if (estimate) shift += (estimate - shift) * 0.15;
    draw();
  }

  async function start() {
    if (run && !run.done) {  // Stop
      finish({ stable: false, silent: false });
      pill.textContent = t("Stopped");
      detail.textContent = t("Not saved");
      return;
    }
    dots.replaceChildren();
    pill.hidden = true;
    big.textContent = "—";
    detail.textContent = t("Waiting for the first click");
    shift = 0;
    const blocks = [];
    try {
      const capture = await openInput(({ data }) => blocks.push(data));
      run = { capture, clicks: [], blocks, key: capture.key, done: false };
      button.textContent = t("Stop");
    } catch (error) {
      detail.textContent = t("The audio input did not start: {error}", { error: (error && error.message) || error });
    }
  }

  const color = (name) => getComputedStyle(dialog).getPropertyValue(name).trim();
  function canvasOf(selector) {
    const canvas = $(selector), dpr = window.devicePixelRatio || 1;
    const width = Math.round(canvas.clientWidth * dpr), height = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const g = canvas.getContext("2d");
    g.clearRect(0, 0, width, height);
    return { g, width, height, dpr };
  }
  function labels(g, dpr, half, below, right = false) {
    g.font = `${11 * dpr}px 'DM Sans', sans-serif`;
    g.fillStyle = color("--muted");
    g.textAlign = right ? "right" : "left";
    const x = right ? g.canvas.width - 6 * dpr : 6 * dpr;
    g.fillText(t("Played"), x, 13 * dpr);
    g.fillText(below, x, half + 13 * dpr);
    g.textAlign = "left";
  }
  function draw() {
    if (!dialog.open) return;
    const rate = audio.sampleRate;
    // The lanes: 1.5 s of context time, now at 70 %; the input drawn `shift` earlier.
    {
      const { g, width, height, dpr } = canvasOf(".practice-tune-lanes");
      if (!width) return;
      const half = height / 2, span = 1.5, now = run && run.end ? Math.min(run.end, audio.currentTime) : audio.currentTime;
      const start = now - 0.7 * span;
      g.fillStyle = color("--line");
      g.fillRect(0, Math.round(half), width, Math.max(1, Math.round(dpr)));
      if (run) {
        g.fillStyle = color("--accent");
        for (const item of run.clicks) {
          if (item.dropped || item.at > now || item.at < start - 0.05) continue;
          const x = ((item.at - start) / span) * width;
          g.fillRect(x, half * 0.15, Math.max(2, 2 * dpr), half * 0.7);
          g.fillStyle = color("--line-strong");
          g.fillRect(x, half, Math.max(1, dpr), half);
          g.fillStyle = color("--accent");
        }
        const shown = partial(start + shift, now + shift);
        if (shown) {
          let peak = 0.02;
          for (const value of shown) peak = Math.max(peak, Math.abs(value));
          g.fillStyle = color("--warn");
          const per = shown.length / (((now - start) / span) * width);
          for (let x = 0; x < width * ((now - start) / span); x++) {
            let top = 0;
            for (let i = Math.floor(x * per); i < Math.floor((x + 1) * per) && i < shown.length; i++) top = Math.max(top, Math.abs(shown[i]));
            const size = (top / peak) * half * 0.42;
            if (size > dpr / 2) g.fillRect(x, half * 1.5 - size, 1, size * 2);
          }
        }
      }
      g.fillStyle = color("--text");
      g.fillRect(Math.round(0.7 * width), 0, Math.max(1, Math.round(dpr)), height);
      labels(g, dpr, half, t("Recorded, moved back {latency}", { latency: ms(shift, ctx.formatDecimal) }));
    }
    // The last click found, -5 to 55 ms around where it was played.
    {
      const { g, width, height, dpr } = canvasOf(".practice-close-up");
      const half = (height - 16 * dpr) / 2, from = -0.005, span = 0.06, x = (time) => ((time - from) / span) * width;
      g.font = `${11 * dpr}px 'DM Sans', sans-serif`;
      g.textAlign = "center";
      for (let m = 0; m <= 50; m += 10) {
        g.fillStyle = color("--line");
        g.fillRect(x(m / 1000), 0, Math.max(1, dpr), height - 16 * dpr);
        g.fillStyle = color("--muted");
        g.fillText(`${m} ms`, x(m / 1000), height - 3 * dpr);
      }
      g.textAlign = "left";
      const wave = (top, value, stroke) => {
        g.strokeStyle = stroke;
        g.lineWidth = 1.5 * dpr;
        g.beginPath();
        for (let px = 0; px < width; px++) {
          const y = top + half / 2 - value(from + (px / width) * span) * half * 0.42;
          if (px) g.lineTo(px, y); else g.moveTo(px, y);
        }
        g.stroke();
      };
      wave(0, clickShape, color("--accent"));
      const last = run && [...run.clicks].reverse().find((item) => item.latency);
      const samples = last && input(last.at + shift + from, last.at + shift + from + span);
      if (samples) {
        let peak = 0.02;
        for (const value of samples) peak = Math.max(peak, Math.abs(value));
        wave(half, (time) => (samples[Math.floor((time - from) * rate)] || 0) / peak, color("--warn"));
      }
      g.fillStyle = color("--text");
      g.fillRect(x(0), 0, Math.max(1, dpr), height - 16 * dpr);
      labels(g, dpr, half, last ? t("Recorded · off by {latency}", { latency: ms(Math.abs(last.latency - shift), ctx.formatDecimal) }) : t("Recorded"), true);
    }
  }
  // While the newest input has not arrived yet: what there is, from `from` on.
  function partial(from, to) {
    const lastBlock = run.blocks.at(-1);
    if (!lastBlock) return null;
    const end = Math.min(to, (lastBlock.frame + lastBlock.samples.length) / audio.sampleRate);
    if (end <= from) return null;
    const part = input(from, end);
    if (!part) return null;
    const full = new Float32Array(Math.round((to - from) * audio.sampleRate));
    full.set(part.subarray(0, full.length));
    return full;
  }

  ctx.on(button, "click", start);
  ctx.on(close, "click", () => dialog.close());
  ctx.on(dialog, "close", () => {
    cancelAnimationFrame(frame);
    if (run && !run.done) run.capture.close();
    dialog.remove();
  });
  dialog.showModal();
  frame = requestAnimationFrame(step);
  return dialog;
}
