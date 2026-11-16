"""The plugins in a real browser (version 22): the store, the five plugins on a project, a broken plugin, removal."""
import json
import shutil
import threading

import pytest

from audio_stems import plugins
from tests.test_ui_browser import ROOT, _launch_browser, _project_detail, _QuietHandler, _Server, _silent_wav

STEMS = ("vocals", "drums", "bass", "other")
BEATS = {"status": "done", "progress": 1, "error": None, "result": {
    "beats": [i * 0.5 for i in range(10)], "downbeats": [0, 2, 4], "bpm": 120, "tempo": [[0, 120]]}}
BROKEN = "export function project(ctx) { ctx.addFooter(document.createElement('span')); throw new Error('Broken on purpose'); }\n"


def _serve(web, state):
    wav = _silent_wav()
    catalog = plugins.catalog(web)

    class Handler(_QuietHandler):
        def _json(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

        def _plugin(self, plugin_id):
            installed = state["installed"].get(plugin_id)
            return {**catalog[plugin_id], "installed": installed is not None, "enabled": bool(installed and installed["enabled"]),
                    "error": installed and installed.get("error"), "settings": (installed or {}).get("settings", {})}

        def do_GET(self):
            path, _, query = self.path.partition("?")
            state["requests"].append(self.path)
            if path == "/api/v1/plugins":
                self._json([self._plugin(plugin_id) for plugin_id in catalog])
            elif path == "/api/v1/projects":
                self._json([{"project_id": "p1", "input_type": "search_query", "input_value": "Song", "source_id": "s",
                             "title": "Song", "created_at": "2026-11-14T10:00:00+00:00", "job_id": "j1", "job_status": "completed", "progress": 1}])
            elif path.startswith("/api/v1/projects/p1/plugins/"):
                self._json({"data": state["data"].get(path.rsplit("/", 1)[1], {})})
            elif path.endswith("/beats"):
                self._json(BEATS)
            elif path.endswith("/chords"):
                self._json(state["chords"])
            elif path == "/api/v1/projects/p1":
                self._json(_project_detail("p1", "j1", "Song", STEMS))
            elif path in ("/api/v1/queue", "/api/v1/models"):
                self._json([])
            elif path == "/api/v1/storage":
                self._json({"projects_bytes": 0, "unused_bytes": 0, "temp_bytes": 0, "models_bytes": 0, "busy": False})
            elif path.endswith("/waveform"):
                self._json({"asset_id": path.split("/")[-2], "levels": [0.5] * (8192 if "points=8192" in query else 1024)})
            elif path.startswith("/api/v1/assets/"):
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(wav)
            else:
                super().do_GET()

        def do_PUT(self):
            body = self._body()
            state["requests"].append(f"PUT {self.path}")
            if self.path.startswith("/api/v1/projects/p1/plugins/"):
                state["data"][self.path.rsplit("/", 1)[1]] = body["data"]
                self.send_response(204)
                self.end_headers()
                return
            if self.path.endswith("/settings"):
                plugin_id = self.path.split("/")[-2]
                state["installed"][plugin_id]["settings"] = body["settings"]
                self._json(self._plugin(plugin_id))
                return
            plugin_id = self.path.rsplit("/", 1)[1]
            state["installed"][plugin_id] = {**state["installed"].get(plugin_id, {}), "enabled": body["enabled"]}
            self._json(self._plugin(plugin_id))

        def do_POST(self):
            body = self._body()
            state["requests"].append(f"POST {self.path}")
            if self.path.endswith("/error"):
                plugin_id = self.path.split("/")[-2]
                state["installed"][plugin_id] = {"enabled": False, "error": body["message"]}
                self._json(self._plugin(plugin_id))
            elif self.path.endswith("/chords"):
                state["chords"] = {"status": "done", "progress": 1, "error": None, "result": {"chords": [
                    {"start": 0.0, "end": 2.0, "chord": "C"}, {"start": 2.0, "end": 4.0, "chord": "Am"}], "algorithm": 1}}
                self._json({"status": "running", "progress": 0.5, "error": None, "result": None}, 202)
            else:  # beats
                self._json(BEATS)

        def do_DELETE(self):
            state["requests"].append(f"DELETE {self.path}")
            state["installed"].pop(self.path.rsplit("/", 1)[1], None)
            self.send_response(204)
            self.end_headers()

    server = _Server(("127.0.0.1", 0), lambda *args, **kwargs: Handler(*args, directory=str(web), **kwargs))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.browser
def test_plugins_from_the_store_on_a_project(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    web = tmp_path / "web"
    shutil.copytree(ROOT / "web", web)
    broken = web / "plugins" / "broken"  # a plugin that throws: turned off, ⚠ shown
    broken.mkdir()
    (broken / "plugin.mjs").write_text(BROKEN, encoding="utf-8")
    (broken / "image.svg").write_text((web / "plugins" / "zoom" / "image.svg").read_text(encoding="utf-8"), encoding="utf-8")
    (broken / "manifest.json").write_text(json.dumps({"id": "broken", "order": 9, "name": "Broken", "description": "Throws.",
                                                      "author": "Tests", "version": "0.1", "permissions": []}), encoding="utf-8")
    state = {"installed": {}, "data": {}, "requests": [], "chords": {"status": "none", "progress": 0, "error": None, "result": None}}
    server = _serve(web, state)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser, launch_error = _launch_browser(playwright)
            if browser is None:
                pytest.skip(f"No browser: {launch_error}")
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base, wait_until="load")
            page.locator('a[data-screen="settings"]').click()
            assert page.locator("#plugins-summary").text_content() == "0 of 8 installed"
            page.locator("#open-store").click()
            cards = page.locator(".store-card")
            assert cards.count() == 8
            assert cards.first.locator("h2").text_content() == "Wheel zoom"
            assert page.locator('.store-card[data-plugin="eq"] .store-permissions').text_content() == "Changes the sound while playing"
            for plugin_id in ("zoom", "beat-grid", "trim", "eq", "chords", "tuner"):
                page.locator(f'.store-card[data-plugin="{plugin_id}"] [data-action="install"]').click()
                page.locator(f'.store-card[data-plugin="{plugin_id}"] [data-action="disable"]').wait_for()
            page.keyboard.press("Escape")  # the store closes first, then Settings
            assert page.locator("#store-overlay").is_hidden() and page.locator("#settings-overlay").is_visible()
            page.keyboard.press("Escape")

            page.locator(".project-tab").first.click()
            page.wait_for_function("document.querySelector('#playhead-time, .playhead-layer') && activeTransport && activeTransport.duration > 0")
            # Beat grid: strong lines on the bar starts, pale ones between (the wide view has room for every beat).
            page.wait_for_function("document.querySelectorAll('.beat-line').length === 10")
            assert page.locator(".beat-line.strong").count() == 3
            # The playhead is as tall as the grid's lines; the title is only as wide as the name (a click beside it
            # does not rename); the plugins' buttons are a row of their own under the footer.
            line, head = page.locator(".beat-line").first.bounding_box(), page.locator(".playhead").bounding_box()
            assert abs(line["y"] - head["y"]) < 1 and abs(line["height"] - head["height"]) < 1
            title, clock = page.locator("#detail-title").bounding_box(), page.locator("#detail-time").bounding_box()
            assert title["x"] + title["width"] < clock["x"] - 100
            assert page.locator('.plugin-footer [data-action="trim-start"]').count() == 1
            assert page.evaluate("document.querySelector('.plugin-footer').getBoundingClientRect().top >= document.querySelector('.project-footer').getBoundingClientRect().bottom")
            # Wheel zoom: the waveforms show less, the page does not scroll, detailed peaks are asked for.
            svg = page.locator(".waveform-preview").first
            box = page.locator(".playhead-layer").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20)
            page.mouse.wheel(0, -600)
            # The waveform is the whole song at the zoom's width, moved into place; it is not drawn again per view.
            widen = "document.querySelector('.waveform-preview').getBoundingClientRect().width / document.querySelector('.mixer-waveform').clientWidth"
            page.wait_for_function(f"{widen} > 1.1")
            assert page.evaluate(widen) == pytest.approx(5 / 3, rel=0.02)  # 3 s of 5
            assert svg.get_attribute("viewBox") == "0 0 1024 100"
            shifted = page.evaluate("""(() => { const s = document.querySelector('.waveform-preview').getBoundingClientRect();
              const c = document.querySelector('.mixer-waveform').getBoundingClientRect(); return (c.left - s.left) / s.width; })()""")
            assert shifted == pytest.approx(1 / 5, abs=0.01)  # the view starts at 1 s: zoomed around the middle (2.5 s)
            # The grid moves with the waveforms: the bar line at 2 s stands a third into the 1-4 s view.
            page.wait_for_function("document.querySelectorAll('.beat-line.strong').length === 3")
            bar = page.evaluate("""(() => { const c = document.querySelector('.mixer-waveform').getBoundingClientRect();
              const line = document.querySelectorAll('.beat-line.strong')[1].getBoundingClientRect(); return (line.left - c.left) / c.width; })()""")
            assert bar == pytest.approx(1 / 3, abs=0.01)
            assert page.evaluate("window.scrollY") == 0
            assert any("points=8192" in request for request in state["requests"])
            page.mouse.wheel(0, 2000)  # back out: the whole song
            page.wait_for_function("document.querySelector('.waveform-preview').style.width === '' && document.querySelector('.waveform-preview').style.transform === ''")

            # Trim: cut the start at the playhead (2 s); the bounds are saved and playback stays inside them.
            page.evaluate("activeTransport.seek(2)")
            page.locator('[data-action="trim-start"]').click()
            assert state["data"]["trim"] == {"start": 2, "end": pytest.approx(5, abs=0.05)}
            page.wait_for_function("activeTransport.currentTime >= 2")
            page.evaluate("activeTransport.seek(0.5)")
            page.wait_for_function("activeTransport.currentTime >= 1.99")  # moved back into the bounds
            assert page.locator('[data-action="trim-restore"]').is_visible()
            page.locator('[data-action="trim-restore"]').click()
            assert state["data"]["trim"] == {}

            # EQ: a band dragged up makes the stem's EQ active (its button lit) and is saved for the project.
            page.locator('.mixer-row[data-stem="bass"] [data-control="eq"]').click()
            canvas = page.locator(".eq-graph")
            canvas.wait_for()
            area = canvas.bounding_box()
            x = area["x"] + area["width"] * (__import__("math").log(800 / 20) / __import__("math").log(1000))
            y = area["y"] + area["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x, y - area["height"] / 4, steps=4)
            tip = page.locator(".eq-tip")
            assert tip.is_visible() and "800 Hz" in tip.text_content() and "dB" in tip.text_content()  # over the point
            assert tip.bounding_box()["y"] + tip.bounding_box()["height"] < y - area["height"] / 4
            page.mouse.up()
            assert tip.is_hidden()
            # The switch turns the stem's EQ off (heard without it, the button dark) and on again.
            switch = page.locator(".eq-switch input")
            switch.click()
            assert page.locator('.mixer-row[data-stem="bass"] [data-control="eq"]').get_attribute("aria-pressed") == "false"
            page.wait_for_timeout(600)
            assert state["data"]["eq"]["off"] == ["bass"]
            switch.click()
            page.keyboard.press("Escape")
            assert page.locator('.mixer-row[data-stem="bass"] [data-control="eq"]').get_attribute("aria-pressed") == "true"
            page.wait_for_function("document.querySelector('.eq-dialog') && !document.querySelector('.eq-dialog').open")
            page.wait_for_timeout(600)  # the save waits 400 ms after the last change
            saved = state["data"]["eq"]["stems"]["bass"]
            assert saved[2]["gain"] > 5 and list(state["data"]["eq"]["stems"]) == ["bass"] and state["data"]["eq"]["off"] == []

            # Chords: found in the background, shown over the waveforms, corrected by hand.
            page.locator('[data-action="chords"]').click()
            page.wait_for_function("document.querySelectorAll('.chord-layer .chord-label').length === 2")
            assert page.locator(".chord-layer .chord-label").all_text_contents() == ["C", "Am"]
            # In a row of their own over the stems, over the waveforms' column.
            lane, waves = page.locator(".chord-layer").bounding_box(), page.locator(".playhead-layer").bounding_box()
            assert lane["y"] + lane["height"] <= waves["y"] and abs(lane["x"] - waves["x"]) < 1 and abs(lane["width"] - waves["width"]) < 1
            page.locator(".chord-layer .chord-label").nth(1).click()
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.type("Dm")
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelectorAll('.chord-layer .chord-label')[1].textContent === 'Dm'")
            for _ in range(40):  # the save is a request of its own, after the name changes
                if "chords" in state["data"]:
                    break
                page.wait_for_timeout(50)
            assert state["data"]["chords"] == {"edits": {"2.000": "Dm"}}
            # Zoomed in to 1-4 s the names move with the waveforms; C, already playing, keeps its name at the left edge.
            page.mouse.move(waves["x"] + waves["width"] / 2, waves["y"] + 20)
            page.mouse.wheel(0, -600)
            page.wait_for_function("document.querySelector('.chord-current')?.textContent === 'C'")
            edge = page.evaluate("""(() => { const lane = document.querySelector('.chord-layer').getBoundingClientRect();
              const at = (el) => (el.getBoundingClientRect().left - lane.left) / lane.width;
              return [at(document.querySelector('.chord-current')), at(document.querySelector('.chord-track [data-start="2.000"]')),
                getComputedStyle(document.querySelector('.chord-track [data-start="0.000"]')).visibility]; })()""")
            assert edge[0] == pytest.approx(0, abs=0.005) and edge[1] == pytest.approx(1 / 3, abs=0.01) and edge[2] == "hidden"
            page.mouse.wheel(0, 2000)
            page.wait_for_function("!document.querySelector('.chord-current')")
            assert page.locator(".chord-layer .chord-label").all_text_contents() == ["C", "Dm"]

            # Settings → Plugins shows the settings of the plugins that are on; the chords' size and weight change the open
            # project at once and are saved for every project.
            font = "[...document.querySelectorAll('.chord-track .chord-label')].map((el) => getComputedStyle(el).fontSize + ' ' + getComputedStyle(el).fontWeight)"
            assert page.evaluate(font) == ["13.2px 400", "13.2px 400"]  # 20 % over version 22's 11 px, not bold
            page.locator('a[data-screen="settings"]').click()
            assert page.locator("#plugins-summary").is_hidden()
            # Only the plugins that are on and have settings; the others are only in the store.
            assert page.locator(".installed-plugin h3").all_text_contents() == ["Chords"]
            chords_settings = page.locator('.installed-plugin[data-plugin="chords"] .plugin-settings')
            slider = chords_settings.locator('[data-setting="size"]')
            assert (slider.get_attribute("min"), slider.get_attribute("max")) == ("5.5", "33")
            # Glyphs instead of words (small and big A, plain and bold B); the names stay for screen readers.
            assert chords_settings.locator(".setting-glyph").all_text_contents() == ["AA", "BB"]
            assert (slider.get_attribute("aria-label"), chords_settings.locator('[data-setting="bold"]').get_attribute("aria-label")) == ("Text size", "Bold")
            slider.evaluate("(el) => { el.value = '22'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            chords_settings.locator('[data-setting="bold"]').click()
            assert page.evaluate(font) == ["22px 650", "22px 650"]
            assert chords_settings.locator("output").text_content() == "22.0 px"
            assert page.evaluate("getComputedStyle(document.querySelector('.chord-sample .chord-label')).fontSize") == "22px"
            page.wait_for_function("document.querySelector('.chord-layer').getBoundingClientRect().height > 30")  # the row grows
            for _ in range(40):
                if state["installed"]["chords"].get("settings"):
                    break
                page.wait_for_timeout(50)
            assert state["installed"]["chords"]["settings"] == {"size": 22, "bold": True}
            page.keyboard.press("Escape")

            # Tuner: a circle per string; a click plays its tone until a click elsewhere, Esc or Space (which then
            # does not start the song); the tuning and the volume are kept.
            tuner = page.locator('[data-action="tuner"]')
            assert tuner.inner_text().strip() == "" and tuner.get_attribute("aria-label") == "Tuner"  # a tuning fork only
            assert page.evaluate("document.querySelector('[data-action=\"tuner\"]').closest('.tuner').nextElementSibling.id") == "help-trigger"  # in the header
            tuner.click()
            notes = page.locator(".tuner-note")
            assert [note.get_attribute("data-note") for note in notes.all()] == ["E2", "A2", "D3", "G3", "B3", "E4"]
            pressed = "[...document.querySelectorAll('.tuner-note')].filter((el) => el.getAttribute('aria-pressed') === 'true').map((el) => el.dataset.note)"
            notes.nth(0).click()
            assert page.evaluate(pressed) == ["E2"]
            notes.nth(1).click()
            assert page.evaluate(pressed) == ["A2"]  # one tone at a time
            page.mouse.click(5, 450)  # outside the circles
            assert page.evaluate(pressed) == []
            notes.nth(2).click()
            page.keyboard.press("Space")
            assert page.evaluate(pressed) == [] and page.evaluate("activeTransport.playing") is False
            notes.nth(3).click()
            page.keyboard.press("Escape")
            assert page.evaluate(pressed) == [] and page.locator(".tuner-panel").is_visible()
            page.locator(".tuner-tuning").select_option("bass-5")
            assert [note.get_attribute("data-note") for note in notes.all()] == ["B0", "E1", "A1", "D2", "G2"]
            page.locator(".tuner-volume input").evaluate("(el) => { el.value = '0.8'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            for _ in range(40):
                if state["installed"]["tuner"].get("settings", {}).get("volume") == 0.8:
                    break
                page.wait_for_timeout(50)
            assert state["installed"]["tuner"]["settings"] == {"tuning": "bass-5", "volume": 0.8}
            page.keyboard.press("Escape")  # no tone: closes the panel
            assert page.locator(".tuner-panel").is_hidden()

            # A broken plugin is turned off at once, its error under ⚠ and on its card; the others keep working.
            page.locator('a[data-screen="settings"]').click()
            page.locator("#open-store").click()
            page.locator('.store-card[data-plugin="broken"] [data-action="install"]').click()
            page.locator("#plugin-alert").wait_for()
            assert state["installed"]["broken"] == {"enabled": False, "error": "Broken on purpose"}
            assert "Broken on purpose" in page.locator('.store-card[data-plugin="broken"] .store-error').text_content()
            page.keyboard.press("Escape")
            page.keyboard.press("Escape")
            page.locator("#plugin-alert").click()
            assert "Broken on purpose" in page.locator("#plugin-alert-list").text_content()
            page.locator('#plugin-alert-list [data-action="open-plugin"]').click()
            assert page.locator("#store-overlay").is_visible()

            # Removing the EQ asks first, then its buttons go and the stem plays without it.
            page.locator('.store-card[data-plugin="eq"] [data-action="remove"]').click()
            page.locator("#confirm-action").click()
            page.wait_for_function("!document.querySelector('[data-control=\"eq\"]')")
            assert "DELETE /api/v1/plugins/eq" in state["requests"]
            assert page.locator(".chord-layer .chord-label").count() == 2 and page.locator(".beat-line").count() == 10
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()


WARN = (227, 189, 87)  # the dark theme's --warn: the practice lane's "You" bars
YOU_BARS = """(() => { const c = document.querySelector('.practice-lane canvas'); const g = c.getContext('2d');
  const d = g.getImageData(0, c.height / 2, c.width, c.height / 2).data; let n = 0;
  for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - %d) < 12 && Math.abs(d[i + 1] - %d) < 12 && Math.abs(d[i + 2] - %d) < 12) n++;
  return n; })()""" % WARN


@pytest.mark.browser
def test_practice_records_the_input_under_the_chosen_stem(tmp_path):
    """Version 24: a stem row's target picks the stem, the lane shows it, Record captures the (fake) input while the
    song plays through the decoded engine, and the choice and the settings are kept."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    state = {"installed": {"practice": {"enabled": True}}, "data": {}, "requests": [],
             "chords": {"status": "none", "progress": 0, "error": None, "result": None}}
    server = _serve(ROOT / "web", state)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser, launch_error = _launch_browser(playwright, ("--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"))
            if browser is None:
                pytest.skip(f"No browser: {launch_error}")
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base, wait_until="load")
            page.locator(".project-tab").first.click()
            page.wait_for_function("activeTransport && activeTransport.duration > 0")
            targets = page.locator(".practice-target")
            assert targets.count() == 4 and page.locator(".practice-lane").is_hidden()
            assert targets.nth(2).get_attribute("aria-label") == "Practice with Bass"
            targets.nth(2).click()
            assert targets.nth(2).get_attribute("aria-pressed") == "true"
            lane = page.locator(".practice-lane")
            lane.wait_for()
            wave = page.locator(".mixer-waveform").first.bounding_box()
            box = lane.bounding_box()
            assert abs(box["x"] - wave["x"]) < 1 and abs(box["width"] - wave["width"]) < 1  # as wide as the waveforms
            assert state["data"]["practice"] == {"stem": "bass"}
            assert page.evaluate(YOU_BARS) == 0
            page.locator('[data-action="practice-record"]').click()
            page.wait_for_function("activeTransport.stretching")  # the decoded engine: song time on the AudioContext clock
            page.wait_for_function("document.querySelector('.practice-status').textContent.includes('Latency not measured')")
            page.evaluate("document.activeElement.blur()")
            page.keyboard.press("Space")
            page.wait_for_function(YOU_BARS + " > 50", timeout=15000)  # the fake input's beeps, drawn under the stem
            page.keyboard.press("Space")
            page.locator('[data-action="practice-record"]').click()
            assert page.locator('[data-action="practice-record"]').get_attribute("aria-pressed") == "false"

            page.reload(wait_until="load")  # the stem stays chosen for this project
            page.locator(".project-tab").first.click()
            page.locator(".practice-lane").wait_for()
            assert page.locator(".practice-target").nth(2).get_attribute("aria-pressed") == "true"
            page.locator('a[data-screen="settings"]').click()
            page.locator('[data-setting="tolerance"]').evaluate("(el) => { el.value = '20'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            page.wait_for_function("document.querySelector('[data-setting=\"tolerance\"]').closest('label').querySelector('output').textContent === '±20 ms'")
            for _ in range(50):
                if state["installed"]["practice"].get("settings", {}).get("tolerance") == 20:
                    break
                page.wait_for_timeout(100)
            assert state["installed"]["practice"]["settings"] == {"input": "", "tolerance": 20, "latency": {}}
            assert page.locator('[data-setting="input"] option').count() >= 1
            assert errors == []
            browser.close()
    finally:
        server.shutdown()


# Everything sent to the speakers comes back as the "input" 37.4 ms later: the tuner's clicks reach it as from a pickup.
LOOPBACK = """
(() => {
  const connect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (target, ...rest) {
    const result = connect.call(this, target, ...rest);
    const loop = window.__loop;
    if (loop && target === this.context.destination && this.context === loop.context) connect.call(this, loop.delay);
    return result;
  };
  navigator.mediaDevices.getUserMedia = async () => {
    const context = activeAudioContext;
    const delay = context.createDelay(1);
    delay.delayTime.value = 0.0374;
    const stream = context.createMediaStreamDestination();
    connect.call(delay, stream);
    window.__loop = { context, delay };
    return stream.stream;
  };
})();
"""


@pytest.mark.browser
def test_practice_latency_tuner_measures_a_loopback(tmp_path):
    """Version 24: the tuner's clicks, looped back 37.4 ms late, are found one by one until 5 agree; the result is
    stored for the input and output and shown on the lane's button."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    state = {"installed": {"practice": {"enabled": True}}, "data": {"practice": {"stem": "bass"}}, "requests": [],
             "chords": {"status": "none", "progress": 0, "error": None, "result": None}}
    server = _serve(ROOT / "web", state)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser, launch_error = _launch_browser(playwright, ("--autoplay-policy=no-user-gesture-required",))
            if browser is None:
                pytest.skip(f"No browser: {launch_error}")
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            page.add_init_script(LOOPBACK)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base, wait_until="load")
            page.locator(".project-tab").first.click()
            page.locator(".practice-lane").wait_for()
            latency = page.locator('[data-action="practice-latency"]')
            assert latency.text_content() == "Measure latency"
            latency.click()
            dialog = page.locator(".practice-dialog")
            assert dialog.is_visible() and dialog.locator("h2").text_content() == "Latency"
            dialog.locator('[data-action="measure"]').click()
            page.wait_for_function("document.querySelector('.practice-pill') && !document.querySelector('.practice-pill').hidden", timeout=30000)
            assert dialog.locator(".practice-pill").text_content() == "Stable", dialog.locator(".practice-detail").text_content()
            assert dialog.locator('.practice-dots span[data-found="true"]').count() >= 6
            for _ in range(50):
                saved = state["installed"]["practice"].get("settings", {}).get("latency", {})
                if saved:
                    break
                page.wait_for_timeout(100)
            (key, seconds), = saved.items()
            assert key.startswith("default>") and 0.0374 <= seconds < 0.08  # the loop's delay plus the stream's own
            assert "saved for this input and output" in dialog.locator(".practice-detail").text_content()
            dialog.locator('[data-action="close"]').click()
            assert page.locator(".practice-dialog").count() == 0
            assert latency.text_content().startswith("Latency ") and latency.text_content().endswith(" ms")
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
