// Tuner: a button in the footer opens a row of circles, one per string of the chosen tuning. A click on a circle
// plays a steady tone of that note (organ-like, with overtones, so a bass's low B is heard on laptop speakers too)
// until a click elsewhere, Esc or Space; then Space plays the song again. Its own volume, kept with the tuning.

export const TUNINGS = [
  { id: "guitar", name: "Guitar: standard", notes: ["E2", "A2", "D3", "G3", "B3", "E4"] },
  { id: "guitar-drop-d", name: "Guitar: drop D", notes: ["D2", "A2", "D3", "G3", "B3", "E4"] },
  { id: "guitar-7", name: "Guitar: 7-string", notes: ["B1", "E2", "A2", "D3", "G3", "B3", "E4"] },
  { id: "bass", name: "Bass: standard", notes: ["E1", "A1", "D2", "G2"] },
  { id: "bass-drop-d", name: "Bass: drop D", notes: ["D1", "A1", "D2", "G2"] },
  { id: "bass-5", name: "Bass: 5-string", notes: ["B0", "E1", "A1", "D2", "G2"] },
];
const SEMITONES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
export const OVERTONES = [1, 0.7, 0.5, 0.35, 0.25, 0.15, 0.1, 0.06];  // the fundamental and its harmonics

// Equal temperament, A4 = 440 Hz.
export function frequency(note) {
  const [, letter, sharp, octave] = /^([A-G])(#?)(-?\d)$/.exec(note);
  const midi = 12 * (Number(octave) + 1) + SEMITONES[letter] + (sharp ? 1 : 0);
  return 440 * 2 ** ((midi - 69) / 12);
}

export function choice(settings = {}) {
  const volume = Number(settings.volume);
  return {
    tuning: TUNINGS.find((tuning) => tuning.id === settings.tuning) || TUNINGS[0],
    volume: Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 0.5,
  };
}

const ICON = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 2v8a3 3 0 0 0 6 0V2"/><path d="M12 13v9"/></svg>';

export function project(ctx) {
  ctx.add(document.head, Object.assign(document.createElement("link"), { rel: "stylesheet", href: "plugins/tuner/plugin.css" }));
  const holder = ctx.addFooter(document.createElement("div"));
  holder.className = "tuner";
  const button = holder.appendChild(document.createElement("button"));
  button.type = "button";
  button.className = "footer-toggle tuner-toggle";
  button.dataset.action = "tuner";
  button.setAttribute("aria-expanded", "false");
  button.innerHTML = `${ICON}<span>${ctx.t("Tuner")}</span>`;
  const panel = holder.appendChild(document.createElement("div"));
  panel.className = "tuner-panel";
  panel.hidden = true;
  panel.setAttribute("role", "group");
  panel.setAttribute("aria-label", ctx.t("Tuner"));
  const select = panel.appendChild(document.createElement("select"));
  select.className = "tuner-tuning";
  select.setAttribute("aria-label", ctx.t("Tuning"));
  // Literal strings for the translation catalog: the tunings' names.
  const names = { "Guitar: standard": ctx.t("Guitar: standard"), "Guitar: drop D": ctx.t("Guitar: drop D"),
    "Guitar: 7-string": ctx.t("Guitar: 7-string"), "Bass: standard": ctx.t("Bass: standard"),
    "Bass: drop D": ctx.t("Bass: drop D"), "Bass: 5-string": ctx.t("Bass: 5-string") };
  select.append(...TUNINGS.map((tuning) => Object.assign(document.createElement("option"), { value: tuning.id, textContent: names[tuning.name] })));
  const notes = panel.appendChild(document.createElement("div"));
  notes.className = "tuner-notes";
  const volumeLabel = panel.appendChild(document.createElement("label"));
  volumeLabel.className = "tuner-volume";
  volumeLabel.title = ctx.t("Tuner volume");
  volumeLabel.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M11 4.7a.7.7 0 0 0-1.2-.5L6.4 7.6A1.4 1.4 0 0 1 5.4 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.4a1.4 1.4 0 0 1 1 .4l3.4 3.4a.7.7 0 0 0 1.2-.5z"/><path d="M16 9a5 5 0 0 1 0 6"/></svg>';
  const volume = volumeLabel.appendChild(Object.assign(document.createElement("input"), { type: "range", min: 0, max: 1, step: 0.01 }));
  volume.setAttribute("aria-label", ctx.t("Tuner volume"));

  const state = choice(ctx.settings);
  const audio = ctx.audioContext;
  const output = audio.createGain();
  output.gain.value = state.volume;
  output.connect(audio.destination);
  const wave = audio.createPeriodicWave(new Float32Array(OVERTONES.length + 1), Float32Array.from([0, ...OVERTONES]));
  let sounding = null;  // {note, oscillator, envelope, circle}

  const stop = () => {
    if (!sounding) return;
    const { oscillator, envelope, circle } = sounding;
    sounding = null;
    circle.setAttribute("aria-pressed", "false");
    const now = audio.currentTime;
    envelope.gain.cancelScheduledValues(now);
    envelope.gain.setTargetAtTime(0, now, 0.02);  // no click at the end
    oscillator.stop(now + 0.15);
    window.setTimeout(() => envelope.disconnect(), 300);
  };
  const play = (note, circle) => {
    stop();
    audio.resume();
    const oscillator = audio.createOscillator();
    oscillator.setPeriodicWave(wave);
    oscillator.frequency.value = frequency(note);
    const envelope = audio.createGain();
    envelope.gain.value = 0;
    envelope.gain.setTargetAtTime(0.25, audio.currentTime, 0.02);  // the overtones add up: room for them
    oscillator.connect(envelope).connect(output);
    oscillator.start();
    circle.setAttribute("aria-pressed", "true");
    sounding = { note, oscillator, envelope, circle };
  };
  const save = () => ctx.saveSettings({ tuning: state.tuning.id, volume: state.volume });
  const showTuning = () => {
    stop();
    select.value = state.tuning.id;
    notes.replaceChildren(...state.tuning.notes.map((note) => {
      const circle = document.createElement("button");
      circle.type = "button";
      circle.className = "tuner-note";
      circle.dataset.note = note;
      circle.setAttribute("aria-pressed", "false");
      circle.innerHTML = `${note[0]}${note.length > 2 ? note[1] : ""}<sub>${note.slice(-1)}</sub>`;
      return circle;
    }));
  };

  ctx.on(notes, "click", (event) => {
    const circle = event.target.closest(".tuner-note");
    if (!circle) return;
    if (sounding && sounding.circle === circle) stop();
    else play(circle.dataset.note, circle);
  });
  ctx.on(select, "change", () => {
    state.tuning = choice({ tuning: select.value }).tuning;
    showTuning();
    save();
  });
  ctx.on(volume, "input", () => {
    state.volume = Number(volume.value);
    output.gain.setTargetAtTime(state.volume, audio.currentTime, 0.02);
    save();
  });
  ctx.on(button, "click", () => {
    panel.hidden = !panel.hidden;
    button.setAttribute("aria-expanded", String(!panel.hidden));
    button.setAttribute("aria-pressed", String(!panel.hidden));
    if (panel.hidden) stop();
    else {  // over its button, moved left as far as it must to stay 16 px inside the window (a phone)
      panel.style.left = "0px";
      const right = panel.getBoundingClientRect().right;
      panel.style.left = `${Math.min(0, document.documentElement.clientWidth - 16 - right)}px`;
    }
  });
  // A click anywhere but a circle stops the tone; Esc and Space too (before the player's hotkeys, which they
  // would otherwise reach: Space starts the song only when no tone sounds). Esc with no tone closes the panel.
  ctx.on(document, "pointerdown", (event) => {
    if (!event.target.closest(".tuner-note")) stop();
  }, true);
  ctx.on(window, "keydown", (event) => {
    if (event.code !== "Space" && event.key !== "Escape") return;
    if (sounding) {
      event.preventDefault();
      event.stopPropagation();
      stop();
    } else if (event.key === "Escape" && !panel.hidden && !document.querySelector("dialog[open]")) {
      event.stopPropagation();
      button.click();
    }
  }, true);
  volume.value = String(state.volume);
  showTuning();
  return () => {
    stop();
    window.setTimeout(() => output.disconnect(), 300);
  };
}
