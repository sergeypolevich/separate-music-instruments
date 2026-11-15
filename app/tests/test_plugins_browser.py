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
                    "error": installed and installed.get("error")}

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
            plugin_id = self.path.rsplit("/", 1)[1]
            state["installed"][plugin_id] = {"enabled": body["enabled"]}
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
            assert page.locator("#plugins-summary").text_content() == "0 of 6 installed"
            page.locator("#open-store").click()
            cards = page.locator(".store-card")
            assert cards.count() == 6
            assert cards.first.locator("h2").text_content() == "Wheel zoom"
            assert page.locator('.store-card[data-plugin="eq"] .store-permissions').text_content() == "Changes the sound while playing"
            for plugin_id in ("zoom", "beat-grid", "trim", "eq", "chords"):
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
            page.wait_for_function("document.querySelectorAll('.chord-label').length === 2")
            assert page.locator(".chord-label").all_text_contents() == ["C", "Am"]
            # In a row of their own over the stems, over the waveforms' column.
            lane, waves = page.locator(".chord-layer").bounding_box(), page.locator(".playhead-layer").bounding_box()
            assert lane["y"] + lane["height"] <= waves["y"] and abs(lane["x"] - waves["x"]) < 1 and abs(lane["width"] - waves["width"]) < 1
            page.locator(".chord-label").nth(1).click()
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.type("Dm")
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelectorAll('.chord-label')[1].textContent === 'Dm'")
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
            assert page.locator(".chord-label").all_text_contents() == ["C", "Dm"]

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
            assert page.locator(".chord-label").count() == 2 and page.locator(".beat-line").count() == 10
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
