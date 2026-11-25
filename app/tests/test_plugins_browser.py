"""The plugins in a real browser (version 22): the store, the five plugins on a project, a broken plugin, removal."""
import io
import json
import math
import shutil
import threading
import wave

import numpy as np
import pytest

from audio_stems import plugins
from tests.test_ui_browser import ROOT, _launch_browser, _project_detail, _QuietHandler, _Server, _silent_wav

STEMS = ("vocals", "drums", "bass", "other")
BEATS = {"status": "done", "progress": 1, "error": None, "result": {
    "beats": [i * 0.5 for i in range(10)], "downbeats": [0, 2, 4], "bpm": 120, "tempo": [[0, 120]]}}
BROKEN = "export function project(ctx) { ctx.addFooter(document.createElement('span')); throw new Error('Broken on purpose'); }\n"


def _serve(web, state, wav=None):
    wav = wav or _silent_wav()
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
            files = state.setdefault("files", {})
            if "/files" in path and path.startswith("/api/v1/projects/p1/plugins/"):
                name = path.split("/files")[1].strip("/")
                if not name:
                    self._json({"files": [{"name": key, "bytes": len(value)} for key, value in files.items()], "limit_bytes": 100 * 2**20})
                    return
                body = files.get(name)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")
            elif path == "/api/v1/plugins":
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
                self._json({**_project_detail("p1", "j1", "Song", STEMS), "start_seconds": state.get("start_seconds", 0.0)})
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
            if "/files/" in self.path:
                state.setdefault("files", {})[self.path.rsplit("/", 1)[1]] = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(204)
                self.end_headers()
                return
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
            if self.path == "/api/v1/client-log":  # the page's log: kept apart from the requests
                state.setdefault("log", []).extend(body["events"])
                self.send_response(204)
                self.end_headers()
                return
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
            if "/files/" in self.path:
                state.setdefault("files", {}).pop(self.path.rsplit("/", 1)[1], None)
                self.send_response(204)
                self.end_headers()
                return
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
            # «Read more» shows the plugin's README.md over the store; Esc closes only it. Without a README: the card's text.
            page.locator('.store-card[data-plugin="eq"] [data-action="readme"]').click()
            readme = page.locator(".readme-dialog")
            readme.wait_for()
            assert readme.locator("h2").text_content() == "Equalizer" and readme.locator("h3").first.text_content() == "How to use"
            assert readme.locator("ol li").count() == 5 and readme.locator("strong").first.text_content() == "EQ"
            page.keyboard.press("Escape")
            readme.wait_for(state="detached")
            assert page.locator("#store-overlay").is_visible()
            page.locator('.store-card[data-plugin="broken"] [data-action="readme"]').click()
            assert readme.locator("h2").text_content() == "Broken" and readme.locator("p").text_content() == "Throws."
            readme.locator(".readme-close").click()
            readme.wait_for(state="detached")
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
            # A seek out of the view takes the view along (the playhead a tenth in); ⏮ brings it back to the start.
            view_start = """(() => { const s = document.querySelector('.waveform-preview').getBoundingClientRect();
              const c = document.querySelector('.mixer-waveform').getBoundingClientRect(); return (c.left - s.left) / s.width * 5; })()"""
            page.evaluate("activeTransport.seek(4.5)")
            page.wait_for_function(f"Math.abs({view_start} - 2) < 0.05")  # 4.2 s clamped to the song's end: 2-5 s
            page.locator('[data-action="reset"]').click()
            page.wait_for_function(f"Math.abs({view_start}) < 0.05")
            # The right button dragged a third of the width to the left moves the 3 s view 1 s on; no seek, no loop.
            page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + 20)
            page.mouse.down(button="right")
            page.mouse.move(box["x"] + box["width"] * 0.6 - box["width"] / 3, box["y"] + 20, steps=5)
            page.mouse.up(button="right")
            assert page.evaluate(view_start) == pytest.approx(1, abs=0.05)
            assert page.evaluate("activeTransport.currentTime") == 0 and page.evaluate("activeTransport.loop") is None
            # The page has no browser menu on the right button, but text fields do (paste).
            menu = "(selector) => { const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true }); document.querySelector(selector).dispatchEvent(event); return event.defaultPrevented; }"
            assert page.evaluate(menu, ".playhead-layer") and page.evaluate(menu, "#detail-title")
            assert not page.evaluate(menu, "#inputs")  # the song search
            page.locator('[data-action="reset"]').click()
            page.wait_for_function(f"Math.abs({view_start}) < 0.05")
            # A zoom while playing keeps the view on the playhead (version 24; it used to stop following).
            page.evaluate("document.activeElement.blur(); activeTransport.seek(1.5)")
            page.keyboard.press("Space")
            page.wait_for_function("activeTransport.playing && activeTransport.currentTime > 1.6")
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20)  # back over the waveforms from the ⏮ button
            page.mouse.wheel(0, 100)
            page.wait_for_function(f"{widen} < 1.6")  # zoomed out a little, still following
            page.wait_for_function(f"{view_start} > 1", timeout=8000)
            page.evaluate("activeTransport.pause(); activeTransport.seek(2)")
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
            # ?dev_mode in the address gives the browser's menu back (to inspect the page).
            page.goto(base + "/?dev_mode=1", wait_until="load")
            assert not page.evaluate("(() => { const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true }); document.body.dispatchEvent(event); return event.defaultPrevented; })()")
            browser.close()
    finally:
        server.shutdown()


WARN = (227, 189, 87)  # the dark theme's --warn: the practice lane's "You" bars
YOU_BARS = """(() => { const c = document.querySelector('.practice-lane canvas'); const g = c.getContext('2d');
  if (!c.width) return 0;
  const d = g.getImageData(0, 0, c.width, c.height).data; let n = 0;
  for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - %d) < 12 && Math.abs(d[i + 1] - %d) < 12 && Math.abs(d[i + 2] - %d) < 12) n++;
  return n; })()""" % WARN


@pytest.mark.browser
def test_practice_records_the_input_under_the_chosen_stem(tmp_path):
    """Version 24: a stem row's target picks the stem, a mixer row «You» under it (S, M, volume, EQ) shows the input
    Record captures (fake) while the song plays through the decoded engine; the choice and the settings are kept."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    state = {"installed": {"practice": {"enabled": True}, "eq": {"enabled": True}}, "data": {}, "requests": [],
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
            you = page.locator(".plugin-track")
            bass, other = page.locator('.mixer-row[data-stem="bass"]').bounding_box(), page.locator('.mixer-row[data-stem="other"]').bounding_box()
            assert bass["y"] < box["y"] < other["y"]  # right under the stem practised, over the next one
            assert you.locator(".mixer-label").text_content() == "You"
            assert you.locator('[data-control="eq"]').count() == 1 and you.locator('[data-control="gain"]').count() == 1
            # The row's own buttons are in its left side, on a line over the name, S, M and EQ; none over the waveform.
            left, heading = you.locator(".mixer-left").bounding_box(), you.locator(".mixer-heading").bounding_box()
            for action in ("practice-record", "practice-listen", "practice-latency"):
                button = you.locator(f'.mixer-left [data-action="{action}"]').bounding_box()
                assert left["x"] <= button["x"] and button["x"] + button["width"] <= left["x"] + left["width"] + 1, action
                assert button["y"] + button["height"] <= heading["y"] + 1, action
            # ...aligned to the right, flush with the end of the line under them
            assert abs(button["x"] + button["width"] - (heading["x"] + heading["width"])) <= 2
            assert lane.locator("button").count() == 0
            you.locator('[data-control="solo"]').click()  # S on «You»: the stems are silenced, like any solo
            assert page.locator('.mixer-row[data-stem="bass"]').get_attribute("data-silenced") == "true"
            you.locator('[data-control="solo"]').click()
            page.locator('.mixer-row[data-stem="drums"] [data-control="solo"]').click()  # a stem's solo silences «You»
            assert you.get_attribute("data-silenced") == "true"
            page.locator('.mixer-row[data-stem="drums"] [data-control="solo"]').click()
            playhead = page.locator(".playhead-layer").bounding_box()
            assert playhead["y"] + playhead["height"] >= box["y"] + box["height"] - 1  # the playhead, loop and grid cover it
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
            page.locator('[data-action="practice-record"]').click()  # a new take: the lane starts empty
            page.wait_for_function(YOU_BARS + " === 0")
            page.locator('[data-action="practice-record"]').click()

            page.reload(wait_until="load")  # the stem stays chosen for this project
            page.locator(".project-tab").first.click()
            page.locator(".practice-lane").wait_for()
            assert page.locator(".practice-target").nth(2).get_attribute("aria-pressed") == "true"
            page.locator('a[data-screen="settings"]').click()
            assert page.locator('[data-setting="input"] option').count() >= 1
            assert page.locator('[data-setting="tolerance"]').count() == 0  # the score's tolerance, left out
            assert errors == []
            browser.close()
    finally:
        server.shutdown()


# Everything sent to the speakers comes back as the "input" 37.4 ms later: the tuner's clicks reach it as from a pickup.
LOOPBACK = """
(() => {
  const connect = AudioNode.prototype.connect;
  const toSpeakers = [];  // everything connected to the output so far (the stems' master gain, from the start)
  AudioNode.prototype.connect = function (target, ...rest) {
    const result = connect.call(this, target, ...rest);
    if (target === this.context.destination) {
      toSpeakers.push(this);
      const loop = window.__loop;
      if (loop && this.context === loop.context) connect.call(this, loop.delay);
    }
    return result;
  };
  navigator.mediaDevices.getUserMedia = async () => {
    const context = activeAudioContext;
    if (!window.__loop || window.__loop.context !== context) {  // one delay for every input opened
      const delay = context.createDelay(1);
      delay.delayTime.value = 0.0374;
      toSpeakers.filter((node) => node.context === context).forEach((node) => connect.call(node, delay));
      window.__loop = { context, delay };
    }
    const stream = context.createMediaStreamDestination();
    connect.call(window.__loop.delay, stream);
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
            assert latency.get_attribute("aria-label") == "Measure latency" and latency.text_content() == ""  # an icon
            latency.click()
            dialog = page.locator(".practice-dialog")
            assert dialog.is_visible() and dialog.locator("h2").text_content() == "Latency"
            # How to set it up: a picture (the earcup on the pickup, the sound card, Start) and three numbered steps.
            assert "earcup" in dialog.locator(".practice-setup").get_attribute("aria-label")
            assert dialog.locator(".practice-setup [data-label]").all_text_contents() == ["Sound card", "Start"]
            steps = dialog.locator(".practice-steps li").all_text_contents()
            assert len(steps) == 3 and "Wireless" in steps[0] and "pickup" in steps[1] and "Stable" in steps[2]
            assert dialog.locator(".practice-setup").bounding_box()["width"] > 300
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
            finished = _logged(page, state, "practice_tuner_finished")
            assert finished["details"]["result"] == "stable" and len(finished["details"]["clicks"]) >= 6
            names = [item["event"] for item in state["log"]]
            for name in ("ui_click", "practice_tuner_opened", "practice_tuner_start_pressed", "practice_input_requested",
                         "practice_input_opened", "practice_tuner_started", "practice_tuner_click"):
                assert name in names, names
            opened = next(item for item in state["log"] if item["event"] == "practice_input_opened")["details"]
            assert opened["why"] == "tuner" and opened["engine"]["state"] == "running" and "permission" in opened
            assert "saved for this input and output" in dialog.locator(".practice-detail").text_content()
            dialog.locator('[data-action="close"]').click()
            page.locator(".practice-dialog").wait_for(state="detached")  # the close event comes a task later
            assert latency.get_attribute("aria-label").startswith("Latency ") and latency.text_content().endswith(" ms")
            assert errors == []
            browser.close()
    finally:
        server.shutdown()


def _logged(page, state, name):
    """The first page log event called `name`, once the page has sent it (a batch a second)."""
    for _ in range(60):
        found = [item for item in state.get("log", []) if item["event"] == name]
        if found:
            return found[0]
        page.wait_for_timeout(100)
    raise AssertionError(f"{name} not logged: {[item['event'] for item in state.get('log', [])]}")


# The device refuses the raw input (no echo cancellation, mono), as a busy or picky Windows driver does; it opens plainly.
PICKY_INPUT = """
(() => {
  const open = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    if (constraints.audio && constraints.audio.echoCancellation === false) throw new DOMException("Could not start audio source", "NotReadableError");
    return open(constraints);
  };
})();
"""


@pytest.mark.browser
def test_practice_logs_why_the_input_did_not_start(tmp_path):
    """Version 24: an input that does not start is in the log with the step, the error, the permission, the devices
    and what opens instead (the same input without the raw settings, the default input)."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    state = {"installed": {"practice": {"enabled": True}}, "data": {"practice": {"stem": "bass"}}, "requests": [],
             "chords": {"status": "none", "progress": 0, "error": None, "result": None}}
    server = _serve(ROOT / "web", state)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser, launch_error = _launch_browser(playwright, ("--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"))
            if browser is None:
                pytest.skip(f"No browser: {launch_error}")
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            page.add_init_script(PICKY_INPUT)
            page.goto(base, wait_until="load")
            page.locator(".project-tab").first.click()
            page.locator(".practice-lane").wait_for()
            page.locator('[data-action="practice-record"]').click()
            page.wait_for_function("document.querySelector('.practice-status').textContent.includes('Could not start audio source')", timeout=15000)
            failed = _logged(page, state, "practice_input_failed")
            details = failed["details"]
            assert failed["level"] == "error" and details["why"] == "recording" and details["step"] == "getUserMedia"
            assert details["error"] == "NotReadableError" and details["message"] == "Could not start audio source"
            assert details["asked"]["echoCancellation"] is False and details["open_streams"] == 0
            assert details["permission"] in ("granted", "prompt", "unknown") and isinstance(details["devices"], list)
            assert details["probes"]["same_input_plain"]["ok"] is True and details["probes"]["default_plain"]["ok"] is True
            assert _logged(page, state, "practice_recording_failed")["details"]["error"] == "NotReadableError"
            clicks = [item for item in state["log"] if item["event"] == "ui_click"]
            assert [item["details"]["control"] for item in clicks][-1] == "practice-record" and clicks[-1]["session"]
            assert clicks[0]["details"]["text"] == "Song. Done"  # the project tab, by what it says
            assert clicks[-1]["screen"] == "project" and clicks[-1]["device"] == "desktop" and clicks[-1]["project"]
            assert _logged(page, state, "practice_recording_failed")["plugin"] == "practice"
            browser.close()
    finally:
        server.shutdown()


def _plucks_wav() -> bytes:
    """5 s at 16 kHz: a plucked 110 Hz note every half second from 0.25 s."""
    rate, frames = 16_000, bytearray()
    for i in range(rate * 5):
        since = (i / rate - 0.25) % 0.5
        value = 0.5 * math.exp(-since / 0.08) * math.sin(2 * math.pi * 110 * since) if i / rate >= 0.25 else 0.0
        sample = int(value * 32767).to_bytes(2, "little", signed=True)
        frames += sample + sample
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(bytes(frames))
    return buffer.getvalue()


@pytest.mark.browser
def test_practice_keeps_a_take_and_plays_it_after_reload(tmp_path):
    """Version 24: the song itself comes back through the loop as the "playing"; after the tuner has measured the
    loop, a recorded take is kept as WAV files, comes back with the project and plays along. No score panel: the
    take's score is left out of this release (its note detection is not reliable yet)."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    state = {"installed": {"practice": {"enabled": True}}, "data": {"practice": {"stem": "bass"}}, "requests": [],
             "chords": {"status": "none", "progress": 0, "error": None, "result": None}}
    server = _serve(ROOT / "web", state, _plucks_wav())
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
            page.locator('[data-action="practice-latency"]').click()
            page.locator('.practice-dialog [data-action="measure"]').click()
            page.wait_for_function("!document.querySelector('.practice-pill').hidden", timeout=30000)
            assert page.locator(".practice-pill").text_content() == "Stable"
            page.locator('.practice-dialog [data-action="close"]').click()
            page.locator('[data-action="practice-record"]').click()
            page.wait_for_function("activeTransport.stretching")
            page.evaluate("document.activeElement.blur()")
            page.keyboard.press("Space")
            page.wait_for_function("activeTransport.currentTime > 4.4", timeout=15000)
            page.keyboard.press("Space")
            page.wait_for_timeout(1500)  # the score, when it was there, opened within a second of the stop
            assert page.locator(".practice-score").count() == 0 and "scores" not in state["data"]["practice"]
            # The take is kept as WAV files and comes back with the project: drawn again, and it can be played along.
            for _ in range(50):
                if state["data"]["practice"].get("takes"):
                    break
                page.wait_for_timeout(100)
            (take,) = state["data"]["practice"]["takes"]
            assert take["stem"] == "bass" and [run["file"] for run in take["runs"]] == sorted(state["files"])
            assert all(state["files"][run["file"]][:4] == b"RIFF" for run in take["runs"])
            page.reload(wait_until="load")
            page.locator(".project-tab").first.click()
            listen = page.locator('[data-action="practice-listen"]')
            page.wait_for_function("!document.querySelector('[data-action=\"practice-listen\"]').disabled")
            page.evaluate("activeTransport.seek(1)")
            page.wait_for_function(YOU_BARS + " > 50")
            listen.click()
            assert listen.get_attribute("aria-pressed") == "true"
            page.wait_for_function("activeTransport.stretching")  # on the song's clock to follow it
            page.evaluate("document.activeElement.blur()")
            page.keyboard.press("Space")
            page.wait_for_function("activeTransport.currentTime > 2")
            page.keyboard.press("Space")
            assert errors == []
            browser.close()
    finally:
        server.shutdown()


# The output as it is rendered, block by block, with the song second of each block (only while the decoded engine plays).
OUTPUT_TAP = """
(() => {
  const connect = AudioNode.prototype.connect;
  window.__speakers = [];
  AudioNode.prototype.connect = function (target, ...rest) {
    const result = connect.call(this, target, ...rest);
    if (target === this.context.destination) window.__speakers.push(this);
    return result;
  };
  window.__tap = async (seconds) => {
    const context = activeAudioContext;
    if (!window.__tapNode) {
      const code = `registerProcessor("tap", class extends AudioWorkletProcessor { process(inputs) { const c = inputs[0][0];
        if (c) this.port.postMessage({ frame: currentFrame, samples: c.slice() }); return true; } });`;
      await context.audioWorklet.addModule(URL.createObjectURL(new Blob([code], { type: "text/javascript" })));
      window.__tapNode = new AudioWorkletNode(context, "tap");
      const sink = context.createGain(); sink.gain.value = 0;
      window.__tapNode.connect(sink); connect.call(sink, context.destination);
      window.__speakers.filter((node) => node.context === context).forEach((node) => connect.call(node, window.__tapNode));
    }
    const blocks = [];
    window.__tapNode.port.onmessage = ({ data }) => {
      const song = activeTransport.songTimeAt(data.frame / context.sampleRate);
      if (song !== null) blocks.push([song, Array.from(data.samples)]);
    };
    await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
    window.__tapNode.port.onmessage = null;
    return { rate: context.sampleRate, blocks };
  };
})();
"""


def _stem_as_take(stem_wav, start, song_from, song_to):
    """The stem's left channel from song second `song_from` to `song_to` as a mono WAV take, and its length."""
    with wave.open(io.BytesIO(stem_wav)) as source:
        rate = source.getframerate()
        data = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0]
    part = data[int(round((start + song_from) * rate)):int(round((start + song_to) * rate))]
    out = io.BytesIO()
    with wave.open(out, "wb") as take:
        take.setnchannels(1)
        take.setsampwidth(2)
        take.setframerate(rate)
        take.writeframes(part.tobytes())
    return out.getvalue(), len(part) / rate


def _song_grid(result, start, end):
    """The recorded output blocks placed at their song seconds, from `start` to `end`."""
    rate = result["rate"]
    out = np.zeros(int((end - start) * rate))
    for song, samples in result["blocks"]:
        i = int(round((song - start) * rate))
        if 0 <= i and i + len(samples) <= len(out):
            out[i:i + len(samples)] = samples
    return out, rate


@pytest.mark.browser
@pytest.mark.parametrize("start", [0.0, 0.3])
def test_practice_plays_the_last_take_in_time(start):
    """Version 24: a take that is the stem itself, heard back through «You», lines up with the stem to the sample
    (the output recorded once with only the stem, once with only the take, both mapped to song time), also when the
    music starts later in the files (start_seconds)."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    stem = _plucks_wav()
    take, length = _stem_as_take(stem, start, 0.5, 4.5)
    state = {"installed": {"practice": {"enabled": True}}, "start_seconds": start, "requests": [],
             "data": {"practice": {"stem": "bass", "takes": [{"stem": "bass", "at": "2026-11-18T00:00:00Z", "runs": [{"file": "take-1-0.wav", "from": 0.5, "to": 0.5 + length}]}]}},
             "files": {"take-1-0.wav": take}, "chords": {"status": "none", "progress": 0, "error": None, "result": None}}
    server = _serve(ROOT / "web", state, stem)
    try:
        with sync_playwright() as playwright:
            browser, launch_error = _launch_browser(playwright, ("--autoplay-policy=no-user-gesture-required",))
            if browser is None:
                pytest.skip(f"No browser: {launch_error}")
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            page.add_init_script(OUTPUT_TAP)
            page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="load")
            page.locator(".project-tab").first.click()
            page.wait_for_function("document.querySelector('[data-action=\"practice-listen\"]') && !document.querySelector('[data-action=\"practice-listen\"]').disabled")
            page.locator('[data-action="practice-listen"]').click()
            page.wait_for_function("activeTransport.stretching")
            mute_you = page.locator('[data-track="practice"] [data-control="mute"]')
            mute_bass = page.locator('.mixer-row[data-stem="bass"] [data-control="mute"]')
            others = page.locator('.mixer-row[data-stem]:not([data-stem="bass"]) [data-control="mute"]')
            for i in range(others.count()): others.nth(i).click()
            results = []
            for muted in (mute_you, mute_bass):
                muted.click()
                page.evaluate("activeTransport.seek(1)")
                page.evaluate("activeTransport.play()")
                results.append(page.evaluate("window.__tap(2.5)"))
                page.evaluate("activeTransport.pause()")
                muted.click()
            browser.close()
    finally:
        server.shutdown()
    (a, rate), (b, _) = _song_grid(results[0], 1.2, 3.2), _song_grid(results[1], 1.2, 3.2)
    lags = range(-int(0.05 * rate), int(0.05 * rate))
    corr = [np.dot(a[max(0, -l):len(a) - max(0, l)], b[max(0, l):len(b) - max(0, -l)]) for l in lags]
    lag = list(lags)[int(np.argmax(corr))] / rate * 1000
    assert np.dot(a, a) > 1 and abs(np.dot(b, b) / np.dot(a, a) - 1) < 0.1  # both were heard, as loud
    assert abs(lag) < 1, lag  # milliseconds
