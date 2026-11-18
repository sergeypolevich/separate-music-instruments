# Writing a plugin for Audio Stems

A plugin adds a feature to the project page: a layer over the waveforms, a button in the footer or in a stem row,
an effect on a stem's sound, its own window. Plugins come only from the app's own store: the folders in
`app/web/plugins/`. There is no install from a file or a link yet; a new plugin is added to this repository.

## Files

```text
app/web/plugins/<id>/
  manifest.json   name, description, author, version, permissions, order in the store
  plugin.mjs      the code: an ES module that exports project(ctx)
  image.svg       the store card's picture, 16:9 (640 x 360)
  README.md       for users, in English: what it does, how to use it, its permissions; the store's «Read more» shows it
                  (# and ## headings, paragraphs, - and 1. lists, **bold**, `code`; the first line is "# <name>")
  plugin.css      optional; the plugin adds it to the page itself
```

`<id>`: lowercase letters, digits and dashes, the same as `"id"` in the manifest.

```json
{
  "id": "beat-grid",
  "order": 2,
  "name": "Beat grid",
  "description": "Lines on the song's beats over the waveforms…",
  "author": "Audio Stems",
  "version": "1.0",
  "permissions": ["analysis"]
}
```

## Permissions

The store card lists them, and `ctx` has the matching functions only when the manifest names the permission:

| Permission | Card text | Gives |
|---|---|---|
| `sound` | Changes the sound while playing | `ctx.audioContext`, `ctx.setEffect(index, chain)` |
| `playback` | Changes what is played | `ctx.range()`, `ctx.setRange(range)`, `ctx.dropLoop()` |
| `analysis` | Runs an analysis on this computer | `ctx.beats()`, `ctx.analyze(kind, onProgress)`, `ctx.analysisState(kind)` |
| `tone` | Plays its own sound | `ctx.audioContext`, to make a sound of its own (the tuner's tone) and play it straight to the speakers, not into the stems' mix |
| `input` | Records from your audio input | `ctx.audioContext`, `ctx.precise()`, `ctx.songTimeAt(contextTime)`, `ctx.stemAudio(index)` (version 24, the practice plugin) |

No plugin code runs on the computer itself. Heavy work is a service of the app that a plugin asks for. Today that
means the beats and chord detection. A new kind of analysis is added to the app (`audio_stems/analysis.py` and its
API route), not to a plugin.

## Life cycle

`project(ctx)` is called for every finished project that is opened, also when the plugin is turned on while a
project is open. It may return a cleanup function, or a promise of one. When the project closes or the plugin is
turned off, the app calls the cleanup and then undoes what the plugin added through `ctx`:

- elements added with `ctx.add`, `ctx.addFooter`, `ctx.addHeader`, `ctx.addLane`, `ctx.addTrack` and `ctx.addStemControl`;
- listeners registered with `ctx.on`, `ctx.onTime` and `ctx.onView`;
- the view, the play range and every stem effect it set.

The app catches the plugin's errors: anything `project`, a listener or a promise of it throws. The plugin is then
turned off, the error is stored with it, ⚠ appears in the header, and its card says "Turned off because of an
error". Turning it on again clears the error. An error that is not the plugin's fault (an analysis that failed)
should be caught by the plugin and shown on its own button.

## `ctx`

| Member | What it is |
|---|---|
| `id`, `projectId` | The plugin's id, the open project's id |
| `stems` | `[{name, label}]` in row order (`vocals`… `other`; four or six) |
| `duration`, `position`, `playing` | Song time in seconds (0 = where the music starts) |
| `seek(seconds)` | Moves the playhead |
| `view` | `{start, end}` the waveforms show (song time) |
| `bounds` | `{start, end}` a view may cover: the play range, else the whole song |
| `setView(view or null)` | Shows `view`; `null` shows the whole song |
| `followLead`, `setFollowLead(share or null)` | Version 24: where a followed playhead stays in a zoomed view (0-1; `null`: the zoom's own 10 %). Practice sets 0.6 |
| `onView(callback(view))` | After every change of the view, also when the length becomes known |
| `onTime(callback(position, playing))` | On every frame while playing, and after every seek; the position is the drawn playhead's (smooth between the player's readings) |
| `timeline` | The element over the waveforms that takes the clicks (seek, loop): for wheel or touch gestures; the left button is the host's, the right one the zoom's (a drag pans; the page shows no browser menu unless the address has `?dev_mode`) |
| `overlay` | A layer over all waveforms, under the loop and the playhead, `pointer-events: none` (set `auto` on your own elements) |
| `waves` | The grid of stem rows |
| `add(parent, element)` | Appends a plugin element, removed when the plugin stops |
| `addFooter(element)` | Into the plugins' row under the footer (the metronome, quality and speed row stays the app's) |
| `addHeader(element)` | Into the header, before the shortcuts' "?" (an icon button, like the tuner's) |
| `addLane(element)` | A row over the stems, exactly as wide as the waveforms (give it a height); hidden while all its elements are `hidden` |
| `addStemControl(index, element)` | Before the S and M buttons of a stem row |
| `addTrack({name, label, after})` | Version 24: a mixer row of its own right under stem `after`, with the stems' volume, S and M (one more mixer channel: its solo silences the stems, theirs silence it). Returns `{index, row, left, wave, input, place(stem), remove()}`: `left` is the row's left side (name, S, M, volume; a plugin may add its own line of buttons there), draw into `wave` (the waveform cell, on the stems' time scale `ctx.view`; the playhead layer lies over it, so give clickable elements `z-index: 3`), connect sound to `input` (an AudioNode: then the row's volume, a plugin effect and the master), `place(-1)` hides the row. Removed when the plugin stops |
| `tracks`, `onTrack(callback)` | The plugins' tracks so far (`[{index, name, label}]`) and each one added later; `index` works with `addStemControl` and `setEffect` (the equalizer puts its button there) |
| `on(target, type, callback, options)` | `addEventListener`, guarded and removed when the plugin stops |
| `detailedWaveforms()` | Redraws the waveforms from 8192 peaks (for zooming in) |
| `levels(index)` | A promise of the stem's 8192 stored peaks (0-1) over the song (version 24) |
| `log(event, details, level)` | A step of the plugin into the app's log, `logs/app.jsonl` (version 24): the event is named `<plugin>_<event>`, `details` is a small JSON object (at most 8000 characters), `level` is `"info"` (default), `"warning"` or `"error"`. Log what was asked, what the browser answered and how it ended; never what the user typed |
| `loadData()`, `saveData(object)` | The plugin's own data for this project (JSON, at most 64 KB); removing the plugin deletes it |
| `settings`, `onSettings(callback(settings))`, `saveSettings(object)` | The plugin's settings, the same in every project (version 23); a call after each change made in Settings; storing a change made in the project (the tuner's volume) |
| `current` | `false` once the project has closed (check it after an `await`) |
| `t(text, values)`, `tn(count, one, other, values)`, `formatDecimal(x)` | Translation, as in `app.js` |

With `sound`: `setEffect(index, {input, output})` puts a chain of Web Audio nodes between the stem's volume and the
master volume, and `setEffect(index, null)` takes it out. Use `ctx.audioContext` to make the nodes.

With `input`: the plugin opens the input itself (`getUserMedia`, the browser asks the user) on `ctx.audioContext`.
`precise()` makes the stems play from their decoded audio on that context's clock (as they are at 100 %, through the
stretch engine at other speeds) until the plugin stops, and resolves `true` when ready; then `songTimeAt(time)` gives the
song second heard at an AudioContext time (`null` while paused, before the last seek or while the stems stream).
`stemAudio(index)` resolves `{samples, sampleRate}`: the stem as one channel from song time 0.

A manifest's `"storage_mb"` (version 24, at most 1000) gives the plugin files of its own per project: `ctx.files.list()`
resolves `{files: [{name, bytes}], limit_bytes}`, `load(name)` an ArrayBuffer, `save(name, body)` stores (refused over the
limit in all), `remove(name)` deletes. Names are lowercase letters, digits, `-` and `_` with one extension (`take-1.wav`).
They live in `Plugins/<id>/<project>/` in the media folder and go with the project or the plugin.

With `playback`: `setRange({start, end})` keeps playback between the two times and shows only them; `null` gives
the whole song back.

With `analysis`: `beats()` gives `{beats, downbeats, bpm, tempo}` or `null`. It is the metronome's analysis, run
once per song. `analyze("chords", onProgress)` starts the chord detection and resolves with
`{chords: [{start, end, chord}]}`. `analysisState("chords")` returns the stored state without starting anything.

## Settings

A plugin with settings also exports `settings(panel, api)`. Settings → Plugins lists the installed plugins, each
with its on/off switch, and calls it for a plugin that is on, with an empty `panel` to fill (the classes
`plugin-setting` for a labelled slider and `plugin-switch` for a switch are styled by the app). `api.values` are
the stored settings; `api.save(values)` stores them (at most 64 KB, for every project) and calls the open project's
`ctx.onSettings` at once, so a slider shows its effect while it moves. `api.t`, `api.tn` and `api.formatDecimal`
are as in `ctx`. Removing the plugin deletes its settings. The chords plugin is the example: a size slider, a bold
switch and a sample row.

## Texts and languages

Write every text in English through `ctx.t("…")` or `ctx.tn(count, "…", "…")`, with literal strings, so that
`python -m audio_stems.i18n` finds them (in every `.mjs` of the plugin's folder). The manifest's name and description are collected too. Each plugin is
its own block for the translation service. Stem names (Vocal, Drums…) stay in English.

## Tests

- Pure logic exported from `plugin.mjs` gets a test in `app/tests/test_plugins.mjs` (node, no browser).
- The plugin on a project goes into `app/tests/test_plugins_browser.py` (a fake API, a real browser).
- `tests/test_plugins.py` checks every manifest.
