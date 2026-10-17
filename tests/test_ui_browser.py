"""Local Playwright checks for the desktop and mobile UI layout."""
from __future__ import annotations

import io
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import wave

import pytest


ROOT = Path(__file__).parents[1]
STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")


def _silent_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0\0\0" * 16_000 * 5)  # 5 s, so playback never ends mid-test
    return buffer.getvalue()


def _projects():
    return [
        {
            "project_id": f"project-{index}",
            "input_type": "search_query",
            "input_value": f"Track {index}",
            "source_id": f"source-{index}",
            "title": f"Track {index}",
            "created_at": "2026-10-14T10:00:00+00:00",
            "job_id": f"job-{index}",
            "job_status": "completed" if index == 0 else "failed" if index == 1 else "queued",
        }
        for index in range(14)
    ]


def _project_detail(project_id: str, job_id: str, title: str):
    assets = [
        {
            "asset_id": f"asset-{index}",
            "asset_type": "stem",
            "stem_name": stem,
            "media_format": "wav",
            "byte_size": 64_000,
            "available": True,
        }
        for index, stem in enumerate(STEMS)
    ]
    return {
        "project_id": project_id,
        "input_type": "search_query",
        "input_value": title,
        "source_id": "source-0",
        "title": title,
        "created_at": "2026-10-14T10:00:00+00:00",
        "job": {
            "job_id": job_id,
            "status": "completed",
            "error_summary": None,
            "separation_options": {"model_name": "htdemucs_6s", "device": "cpu", "parameters": {}},
        },
        "assets": assets,
    }


class _Server(ThreadingHTTPServer):
    # The default backlog of 5 drops connections when six stems are aborted and re-requested at once.
    request_queue_size = 64
    daemon_threads = True


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


# Distance between the centre of an icon (SVG box, or the text itself for letter buttons) and its host.
_ICON_OFFSET = """([iconSel, hostSel]) => {
  const icon = document.querySelector(iconSel), host = document.querySelector(hostSel);
  let r;
  if (icon instanceof SVGElement) r = icon.getBoundingClientRect();
  else { const range = document.createRange(); range.selectNodeContents(icon); r = range.getBoundingClientRect(); }
  const h = host.getBoundingClientRect();
  return [r.left + r.width / 2 - (h.left + h.width / 2), r.top + r.height / 2 - (h.top + h.height / 2)];
}"""


def _assert_icon_centred(page, icon_sel, host_sel, *, horizontal=True, tolerance=0.75):
    dx, dy = page.evaluate(_ICON_OFFSET, [icon_sel, host_sel])
    assert abs(dy) <= tolerance, f"{icon_sel} is {dy:.2f}px off vertically"
    if horizontal:
        assert abs(dx) <= tolerance, f"{icon_sel} is {dx:.2f}px off horizontally"


@pytest.mark.browser
@pytest.mark.parametrize("viewport", [(1365, 768), (390, 844)], ids=["desktop", "mobile"])
def test_v3_layout_search_navigation_and_mixer_in_browser(viewport):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    projects = _projects()
    batch_id = "batch-created"
    new_job_id = "job-created"
    wav_bytes = _silent_wav()

    class ApiHandler(_QuietHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/v1/projects":
                self._json(projects)
            elif path.startswith("/api/v1/projects/"):
                project_id = path.rsplit("/", 1)[-1]
                item = next((p for p in projects if p["project_id"] == project_id), projects[0])
                self._json(_project_detail(item["project_id"], item["job_id"], item["title"]))
            elif path == "/api/v1/models":
                self._json([{"name": "htdemucs_6s", "stems": list(STEMS), "parameters": {}, "default": True}])
            elif path.startswith("/api/v1/assets/") and path.endswith("/waveform"):
                asset_id = path.split("/")[-2]
                self._json({"asset_id": asset_id, "levels": [0.0, 0.2, 0.5, 0.1] * 64})
            elif path.startswith("/api/v1/assets/"):
                # Like the real API, honour single byte ranges so the browser can seek.
                start, end = 0, len(wav_bytes) - 1
                requested = self.headers.get("Range", "")
                if requested.startswith("bytes="):
                    first, _, last = requested[6:].partition("-")
                    start = int(first or 0)
                    end = min(int(last), end) if last else end
                body = wav_bytes[start:end + 1]
                self.send_response(206 if requested else 200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                if requested:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(wav_bytes)}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path.startswith("/api/v1/batches/"):
                self._json({"batch_id": batch_id, "status": "completed", "jobs": []})
            else:
                super().do_GET()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/v1/batches":
                title = body["inputs"][0]
                projects.append({
                    "project_id": "project-created", "input_type": "search_query",
                    "input_value": title, "source_id": "source-created", "title": title,
                    "created_at": "2026-10-14T10:00:00+00:00", "job_id": new_job_id,
                    "job_status": "queued",
                })
                self._json({"batch_id": batch_id, "status": "queued", "job_ids": [new_job_id], "jobs": []}, 202)
            else:
                self._json({"error": {"code": "not_found", "message": "Not found"}}, 404)

        def do_DELETE(self):
            self.send_response(204)
            self.end_headers()

        def _json(self, body, status=200):
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    handler = lambda *args, **kwargs: ApiHandler(*args, directory=str(ROOT / "web"), **kwargs)
    server = _Server(("127.0.0.1", 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = None
    try:
        with sync_playwright() as playwright:
            launch_error = None
            for channel in ("msedge", "chrome"):
                try:
                    browser = playwright.chromium.launch(channel=channel, headless=True)
                    break
                except Exception as error:
                    launch_error = error
            if browser is None:
                pytest.skip(f"No installed Edge or Chrome for Playwright: {launch_error}")
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
            page.locator(".mixer-row").first.wait_for()
            # An open project shows only its card: no empty-state hint, subtitle, model line or file list.
            assert not page.locator("#project-empty").is_visible()
            for removed in ("#refresh-projects", "#asset-list", "#detail-input", "#detail-model", "#screen-help"):
                assert page.locator(removed).count() == 0
            # Re-opening the selected project (tab click, job-finished refresh) keeps the same players.
            page.evaluate("window.firstStem = document.querySelector('#playback-panel audio')")
            page.locator('.project-tab[data-project-id="project-0"]').click()
            page.wait_for_load_state("networkidle")
            assert page.evaluate("document.querySelector('#playback-panel audio') === window.firstStem")
            # The project is unframed; tabs start where the project controls start.
            assert page.locator("#project-detail").evaluate("el => getComputedStyle(el).borderTopWidth") == "0px"
            tabs_box = page.locator("#project-tabs").bounding_box()
            assert abs(tabs_box["x"] - page.locator("#detail-controls").bounding_box()["x"]) <= 1
            assert page.locator(".project-tab").count() == 14
            assert page.locator(".project-tab").first.get_attribute("data-status") == "completed"
            assert "Ошибка" not in page.locator("#project-tabs").inner_text()
            assert page.locator("#search-trigger").bounding_box()["width"] <= 760
            assert page.locator("#project-tabs").evaluate("el => el.compareDocumentPosition(document.querySelector('#project-workspace')) & Node.DOCUMENT_POSITION_FOLLOWING")
            assert page.locator(".mixer-row").count() == 6
            assert page.locator(".mixer-row").first.locator(".mixer-left > *").count() == 2
            assert page.locator(".gain-value").count() == 0 and page.locator(".timeline").count() == 0
            assert page.locator(".playback-buttons button").count() == 2
            assert page.locator('[data-action="toggle"]').get_attribute("aria-label") == "Воспроизвести"
            assert page.locator('[data-action="reset"]').get_attribute("aria-label") == "В начало и пауза"
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            tabs = page.locator("#project-tabs")
            tabs.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
            # Tabs that do not fit scroll horizontally; on wide screens they may all fit.
            assert tabs.evaluate("el => el.scrollWidth <= el.clientWidth || el.scrollLeft > 0")
            page.wait_for_function(
                "Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                "&& Array.from(document.querySelectorAll('#playback-panel audio')).every(" 
                "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                timeout=15_000,
            )

            for icon_sel, host_sel in (
                (".settings-link .icon", ".settings-link"),
                ('[data-action="toggle"] .icon', '[data-action="toggle"]'),
                ('[data-action="reset"] .icon', '[data-action="reset"]'),
                ("#delete-project .icon", "#delete-project"),
                ('[data-control="solo"]', '[data-control="solo"]'),
                ('[data-control="mute"]', '[data-control="mute"]'),
            ):
                _assert_icon_centred(page, icon_sel, host_sel)
            _assert_icon_centred(page, "#search-trigger .icon", "#search-trigger", horizontal=False)
            _assert_icon_centred(page, "#search-trigger .icon", "#search-preview", horizontal=False, tolerance=1)

            # Each waveform is one scalable SVG shape that fills its cell.
            page.wait_for_function("document.querySelectorAll('.mixer-waveform svg path.waveform-shape').length === 6")
            for cell in page.locator(".mixer-waveform").all():
                svg_box = cell.locator("svg").bounding_box()
                cell_box = cell.bounding_box()
                assert cell.locator("svg path").count() == 1
                assert abs(svg_box["width"] - cell_box["width"]) <= 0.5

            # The title sits over the waveform column; the transport buttons are 1.5x larger.
            waves = [w.bounding_box() for w in page.locator(".mixer-waveform").all()]
            assert abs(page.locator("#detail-title").bounding_box()["x"] - waves[0]["x"]) <= 0.5
            assert page.locator('[data-action="toggle"]').bounding_box()["height"] >= 46
            # S and M share the stem-name line and are pushed to the right edge of the controls column.
            name_box = page.locator(".mixer-name").first.bounding_box()
            mute_box = page.locator('[data-control="mute"]').first.bounding_box()
            fader_box = page.locator('[data-control="gain"]').first.bounding_box()
            assert abs((name_box["y"] + name_box["height"] / 2) - (mute_box["y"] + mute_box["height"] / 2)) <= 1
            assert abs((mute_box["x"] + mute_box["width"]) - (fader_box["x"] + fader_box["width"])) <= 1
            assert mute_box["height"] == 22
            assert page.locator(".mixer-name").first.evaluate("el => [getComputedStyle(el).fontSize, getComputedStyle(el).fontWeight]") == ["15px", "400"]
            # No bottom gap under the workspace, and the page gradient never repeats as a band.
            assert page.locator("#project-workspace").evaluate("el => getComputedStyle(el).paddingBottom") == "0px"
            assert page.evaluate("getComputedStyle(document.body).backgroundRepeat").startswith("no-repeat")

            # The position layer covers exactly the waveform column; one 1px line spans all stems.
            seek_box = page.locator('[data-control="seek"]').bounding_box()
            for wave_box in waves:
                assert abs(seek_box["x"] - wave_box["x"]) <= 0.5
                assert abs(seek_box["x"] + seek_box["width"] - wave_box["x"] - wave_box["width"]) <= 0.5

            def position():
                return page.evaluate("""() => {
                    const line = document.querySelector('.playhead').getBoundingClientRect();
                    const audio = document.querySelector('#playback-panel audio');
                    return {x: line.left, time: audio.currentTime, duration: audio.duration};
                }""")

            assert page.locator(".playhead").count() == 1
            line = page.locator(".playhead").bounding_box()
            assert line["width"] == 1
            assert abs(line["y"] - waves[0]["y"]) <= 1
            assert abs(line["y"] + line["height"] - (waves[-1]["y"] + waves[-1]["height"])) <= 1
            assert abs(position()["x"] - seek_box["x"]) <= 1

            page.mouse.click(waves[0]["x"] + waves[0]["width"] * 0.25, waves[0]["y"] + waves[0]["height"] / 2)
            quarter = position()
            assert abs(quarter["time"] / quarter["duration"] - 0.25) < 0.02
            assert abs(quarter["x"] - (seek_box["x"] + seek_box["width"] * quarter["time"] / quarter["duration"])) <= 1
            assert page.evaluate("[...document.querySelectorAll('#playback-panel audio')].every(a => Math.abs(a.currentTime - document.querySelector('#playback-panel audio').currentTime) < 0.01)")

            # A click anywhere over the waveform column, gaps between stems included, seeks there.
            page.mouse.click(seek_box["x"] + seek_box["width"] / 2, seek_box["y"] + seek_box["height"] / 2)
            half = position()
            assert abs(half["time"] / half["duration"] - 0.5) < 0.02
            page.locator('[data-control="seek"]').press("Home")
            assert position()["time"] == 0
            page.locator('[data-control="seek"]').press("End")
            assert abs(position()["time"] - half["duration"]) < 0.02
            page.locator('[data-control="seek"]').press("Home")

            # Global hotkeys: they work without focusing anything in particular.
            audio_state = "() => { const a = document.querySelector('#playback-panel audio'); return {t: a.currentTime, d: a.duration, paused: a.paused}; }"
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.keyboard.press("End")
            assert abs(page.evaluate(audio_state)["t"] - page.evaluate(audio_state)["d"]) < 0.02
            page.keyboard.press("ArrowLeft")
            assert page.evaluate(audio_state)["t"] == 0
            page.keyboard.press("ArrowRight")
            assert page.evaluate(audio_state)["t"] > 0.9
            page.keyboard.press("Home")
            assert page.evaluate(audio_state)["t"] == 0
            page.keyboard.press("Space")
            page.wait_for_function("!document.querySelector('#playback-panel audio').paused")
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelector('#playback-panel audio').paused")
            page.keyboard.press("Home")
            selected = "() => [...document.querySelectorAll('.mixer-row')].map(r => r.dataset.selected)"
            assert page.evaluate(selected) == ["true"] + ["false"] * 5
            page.keyboard.press("ArrowUp")
            assert page.evaluate(selected)[0] == "true"
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            assert page.evaluate(selected) == ["false", "false", "true", "false", "false", "false"]
            bar = page.evaluate("""() => { const row = document.querySelector('.mixer-row[data-selected="true"]');
                const s = getComputedStyle(row, '::before'); return [s.width, s.height, row.getBoundingClientRect().height]; }""")
            assert bar[0] == "3px" and abs(float(bar[1][:-2]) - bar[2]) <= 0.5
            page.keyboard.press("KeyS")
            assert page.locator('[data-control="solo"]').nth(2).get_attribute("aria-pressed") == "true"
            page.keyboard.press("KeyS")
            page.keyboard.press("KeyM")
            assert page.locator('[data-control="mute"]').nth(2).get_attribute("aria-pressed") == "true"
            page.keyboard.press("KeyM")
            for _ in range(8):
                page.keyboard.press("ArrowDown")
            assert page.evaluate(selected)[-1] == "true"
            # On a volume slider the arrows still move the position, never the volume.
            gain0 = page.locator('[data-control="gain"]').first
            before_gain = gain0.input_value()
            gain0.focus()
            page.keyboard.press("ArrowRight")
            page.keyboard.press("PageUp")
            assert gain0.input_value() == before_gain and page.evaluate(audio_state)["t"] > 0.9
            page.keyboard.press("Home")
            # Ctrl+F opens the search; typing there never reaches the player.
            page.keyboard.press("Control+KeyF")
            assert page.locator("#search-overlay").is_visible()
            assert page.evaluate("document.activeElement.id") == "inputs"
            page.keyboard.type("s m ")
            page.keyboard.press("ArrowRight")
            assert page.locator("#inputs").input_value() == "s m "
            assert page.evaluate(audio_state)["paused"] and page.evaluate(audio_state)["t"] == 0
            assert page.locator('[data-control="solo"][aria-pressed="true"]').count() == 0
            page.locator("#inputs").fill("")
            page.keyboard.press("Escape")

            # Stems come from the other loopback name so their connections never starve API calls.
            assert page.evaluate(
                "[...document.querySelectorAll('#playback-panel audio')].every(a => new URL(a.src).hostname === 'localhost')"
            )
            # Switching projects must stop the old stems and abort their downloads.
            page.evaluate("window.oldStems = [...document.querySelectorAll('#playback-panel audio')]")
            page.locator('.project-tab[data-project-id="project-2"]').click()
            page.wait_for_function("document.querySelector('#detail-title').textContent === 'Track 2'")
            assert page.evaluate("window.oldStems.every(a => a.paused && !a.hasAttribute('src'))")
            page.locator('.project-tab[data-project-id="project-0"]').click()
            page.wait_for_function(
                "document.querySelector('#detail-title').textContent === 'Track 0' "
                "&& Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                "&& Array.from(document.querySelectorAll('#playback-panel audio')).every("
                "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                timeout=15_000,
            )

            page.locator("#delete-project").click()
            assert page.locator("#delete-dialog").is_visible()
            page.locator("#cancel-delete").click()
            assert not page.locator("#delete-dialog").is_visible()

            gain = page.locator('[data-control="gain"]').first
            gain.evaluate("el => { el.value = '104'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            assert gain.input_value() == "100"
            audio = page.locator("#playback-panel audio").first
            assert float(audio.get_attribute("data-effective-gain")) == 1
            gain.evaluate("el => { el.value = '150'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            assert float(audio.get_attribute("data-effective-gain")) == 1.5
            page.locator('[data-control="mute"]').first.click()
            assert float(audio.get_attribute("data-effective-gain")) == 0
            # Pressed S/M are solid yellow/red with white letters; S/M-silenced waveforms fade to 20 %.
            mute_style = page.locator('[data-control="mute"]').first.evaluate("el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]")
            assert mute_style == ["rgb(226, 32, 32)", "rgb(255, 255, 255)"]
            opacity = "i => getComputedStyle(document.querySelectorAll('.mixer-waveform svg')[i]).opacity"
            assert page.evaluate(opacity, 0) == "0.2" and page.evaluate(opacity, 1) == "1"
            page.locator('[data-control="mute"]').first.click()
            assert float(audio.get_attribute("data-effective-gain")) == 1.5
            page.locator('[data-control="solo"]').nth(2).click()
            solo_style = page.locator('[data-control="solo"]').nth(2).evaluate("el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]")
            assert solo_style == ["rgb(220, 206, 26)", "rgb(255, 255, 255)"]
            assert [page.evaluate(opacity, i) for i in range(6)] == ["0.2", "0.2", "1", "0.2", "0.2", "0.2"]
            page.locator('[data-control="solo"]').nth(2).click()
            assert [page.evaluate(opacity, i) for i in range(6)] == ["1"] * 6
            switches_gap = page.locator(".mixer-switches").first.evaluate("el => getComputedStyle(el).columnGap")
            assert switches_gap == "11px"

            page.locator('[data-screen="settings"]').click()
            assert page.locator("#screen-settings").is_visible()
            page.locator('[data-screen="project"]').first.click()
            assert page.locator("#detail-title").inner_text() == "Track 0"

            page.locator("#search-trigger").click()
            overlay = page.locator("#search-overlay")
            assert overlay.is_visible()
            _assert_icon_centred(page, "#close-search .icon", "#close-search")
            _assert_icon_centred(page, "#submit-button .icon", "#submit-button span", horizontal=False, tolerance=1)
            bounds = overlay.bounding_box()
            assert bounds["width"] == viewport[0] and bounds["height"] == viewport[1]
            textarea = page.locator("#inputs")
            initial_height = textarea.evaluate("el => el.getBoundingClientRect().height")
            textarea.fill("one\ntwo\nthree\nfour")
            expanded_height = textarea.evaluate("el => el.getBoundingClientRect().height")
            assert expanded_height > initial_height
            page.mouse.click(2, 2)
            assert not overlay.is_visible()
            page.locator("#search-trigger").click()
            page.locator("#close-search").click()
            assert not overlay.is_visible()
            page.locator("#search-trigger").click()
            page.keyboard.press("Escape")
            assert not overlay.is_visible()
            page.locator("#search-trigger").click()
            page.locator("#inputs").fill("New track")
            page.locator("#submit-button").click()
            page.wait_for_function("document.querySelector('#search-overlay').hidden")
            page.locator('.project-tab[data-project-id="project-created"]').wait_for(state="visible")
            assert not errors, f"Browser errors: {errors}"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
