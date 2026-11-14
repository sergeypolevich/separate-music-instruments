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

No plugin code runs on the computer itself. Heavy work is a service of the app that a plugin asks for. Today that
means the beats and chord detection. A new kind of analysis is added to the app (`audio_stems/analysis.py` and its
API route), not to a plugin.

## Life cycle

`project(ctx)` is called for every finished project that is opened, also when the plugin is turned on while a
project is open. It may return a cleanup function, or a promise of one. When the project closes or the plugin is
turned off, the app calls the cleanup and then undoes what the plugin added through `ctx`:

- elements added with `ctx.add`, `ctx.addFooter` and `ctx.addStemControl`;
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
| `onView(callback(view))` | After every change of the view, also when the length becomes known |
| `onTime(callback(position, playing))` | On every frame while playing, and after every seek |
| `timeline` | The element over the waveforms that takes the clicks (seek, loop): for wheel or touch gestures |
| `overlay` | A layer over all waveforms, under the loop and the playhead, `pointer-events: none` (set `auto` on your own elements) |
| `waves` | The grid of stem rows |
| `add(parent, element)` | Appends a plugin element, removed when the plugin stops |
| `addFooter(element)` | Into the footer under the stems (metronome, speed…) |
| `addStemControl(index, element)` | Before the S and M buttons of a stem row |
| `on(target, type, callback, options)` | `addEventListener`, guarded and removed when the plugin stops |
| `detailedWaveforms()` | Redraws the waveforms from 8192 peaks (for zooming in) |
| `loadData()`, `saveData(object)` | The plugin's own data for this project (JSON, at most 64 KB); removing the plugin deletes it |
| `current` | `false` once the project has closed (check it after an `await`) |
| `t(text, values)`, `tn(count, one, other, values)`, `formatDecimal(x)` | Translation, as in `app.js` |

With `sound`: `setEffect(index, {input, output})` puts a chain of Web Audio nodes between the stem's volume and the
master volume, and `setEffect(index, null)` takes it out. Use `ctx.audioContext` to make the nodes.

With `playback`: `setRange({start, end})` keeps playback between the two times and shows only them; `null` gives
the whole song back.

With `analysis`: `beats()` gives `{beats, downbeats, bpm, tempo}` or `null`. It is the metronome's analysis, run
once per song. `analyze("chords", onProgress)` starts the chord detection and resolves with
`{chords: [{start, end, chord}]}`. `analysisState("chords")` returns the stored state without starting anything.

## Texts and languages

Write every text in English through `ctx.t("…")` or `ctx.tn(count, "…", "…")`, with literal strings, so that
`python -m audio_stems.i18n` finds them. The manifest's name and description are collected too. Each plugin is
its own block for the translation service. Stem names (Vocal, Drums…) stay in English.

## Tests

- Pure logic exported from `plugin.mjs` gets a test in `app/tests/test_plugins.mjs` (node, no browser).
- The plugin on a project goes into `app/tests/test_plugins_browser.py` (a fake API, a real browser).
- `tests/test_plugins.py` checks every manifest.
