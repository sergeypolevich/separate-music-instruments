"""Local Playwright checks for the desktop and mobile UI layout."""
from __future__ import annotations

import io
import os
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import wave

import pytest


ROOT = Path(__file__).parents[1]
# AUDIO_STEMS_SOFT_CHECKS=<folder> (CI on macOS): the browser test goes on past a failed check and lists every
# failure at the end, with a screenshot of each, instead of stopping at the first. See _soft_checks below.
SOFT_CHECKS = os.environ.get("AUDIO_STEMS_SOFT_CHECKS")
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
            "job_status": "failed" if index == 1 else "completed",
            "progress": 1.0 if index == 0 else 0.0,
        }
        for index in range(14)
    ]


def _project_detail(project_id: str, job_id: str, title: str, stems=STEMS, missing=()):
    assets = [
        {
            "asset_id": f"asset-{index}",
            "asset_type": "stem",
            "stem_name": stem,
            "media_format": "wav",
            "byte_size": 64_000,
            "available": stem not in missing,
        }
        for index, stem in enumerate(stems)
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
            "separation_options": {"model_name": "htdemucs_6s", "device": "cpu"},
        },
        "assets": assets,
        "start_seconds": 1.0 if project_id == "project-6" else 0.0,
        "disk_bytes": 12 * 2**20,
        "shared_bytes": 3 * 2**20 if project_id == "project-1" else 0,
        # project-3 was upgraded here: its finished upgrade stays «done, 100 %»
        "upgrade": {"status": "done" if project_id == "project-3" else "none",
                    "progress": 1.0 if project_id == "project-3" else 0.0, "error": None, "best": project_id == "project-3",
                    "estimate_seconds": 1380, "eta_seconds": None},
    }


def _launch_browser(playwright):
    """Installed Edge or Chrome by default; AUDIO_STEMS_TEST_BROWSER=webkit runs Safari's engine instead
    (also on Windows), =chromium the bundled Chromium (CI without Chrome)."""
    engine = os.environ.get("AUDIO_STEMS_TEST_BROWSER", "")
    if engine in ("webkit", "chromium"):
        return getattr(playwright, engine).launch(headless=True), None
    launch_error = None
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(channel=channel, headless=True), None
        except Exception as error:
            launch_error = error
    return None, launch_error


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
    submitted = []
    uploaded = []
    deleted = []
    running_detail = {}
    disk = {"unused": 40 * 2**20, "temp": 10 * 2**20, "busy": False}
    beats_polls = []
    upgrade_polls = []
    wav_bytes = _silent_wav()

    class ApiHandler(_QuietHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/v1/projects":
                self._json(projects)
            elif path.endswith("/beats"):
                beats_polls.append(path)
                done = len(beats_polls) >= 3
                self._json({"status": "done" if done else "running", "progress": 1.0 if done else 0.3 * len(beats_polls),
                            "error": None, "result": {"beats": [i * 0.25 for i in range(20)], "downbeats": [], "bpm": 240,
                                       "tempo": [[0.0, 240.0], [0.3, 238.44]]} if done else None})
            elif path.endswith("/upgrade"):
                upgrade_polls.append(path)
                step = len(upgrade_polls)
                state = {"status": "running", "progress": 0.4, "error": None, "best": False, "estimate_seconds": 1380, "eta_seconds": 830}
                if step >= 3:  # finished: the project now has the best model's stems
                    state.update(status="done", progress=1.0, best=True, eta_seconds=None)
                self._json(state)
            elif path.startswith("/api/v1/projects/"):
                project_id = path.rsplit("/", 1)[-1]
                item = next((p for p in projects if p["project_id"] == project_id), projects[0])
                stems = ("vocals", "drums", "bass", "other") if project_id == "project-3" else STEMS  # a 4-stem model
                missing = ("vocals", "drums") if project_id == "project-4" else ()  # removed from disk by hand
                detail = _project_detail(item["project_id"], item["job_id"], item["title"], stems, missing)
                if project_id == "project-0" and len(upgrade_polls) >= 3:  # upgraded: new stem files
                    for asset in detail["assets"]:
                        asset["asset_id"] += "-sw"
                    detail["job"]["separation_options"]["model_name"] = "bs_roformer_sw"
                    detail["upgrade"].update(status="done", progress=1.0, best=True)
                if project_id in running_detail:  # still processing: no stems yet
                    detail["job"]["status"] = running_detail[project_id]
                    detail["assets"] = []
                self._json(detail)
            elif path == "/api/v1/models":
                self._json([
                    {"name": "htdemucs_6s", "label": "Demucs 6", "description": "Fast", "stems": list(STEMS), "default": True,
                     "seconds_per_audio_second": 0.9, "measured": True},
                    {"name": "htdemucs_ft", "label": "Demucs ft", "description": "Cleaner", "stems": ["vocals", "drums", "bass", "other"], "default": False,
                     "seconds_per_audio_second": 11.2, "measured": False},
                ])
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
            elif path == "/api/v1/storage":
                self._json({"projects_bytes": 3 * 2**30, "unused_bytes": disk["unused"], "temp_bytes": disk["temp"],
                            "models_bytes": 800 * 2**20, "busy": disk["busy"]})
            elif path.startswith("/api/v1/batches/"):
                self._json({"batch_id": batch_id, "status": "completed", "jobs": []})
            else:
                super().do_GET()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path.startswith("/api/v1/uploads"):  # the body is the file itself
                from urllib.parse import unquote
                uploaded.append((unquote(self.headers.get("X-Filename", "")), self.rfile.read(length), self.path))
                name = uploaded[-1][0].rsplit(".", 1)[0]
                projects.append({
                    "project_id": f"project-upload-{len(uploaded)}", "input_type": "local_file",
                    "input_value": f"0123456789abcdef.wav/{uploaded[-1][0]}", "source_id": "0123456789abcdef",
                    "title": name, "created_at": "2026-10-14T10:00:00+00:00", "job_id": f"job-upload-{len(uploaded)}",
                    "job_status": "queued", "progress": 0.0,
                })
                self._json({"batch_id": batch_id, "status": "queued", "job_ids": [f"job-upload-{len(uploaded)}"], "jobs": []}, 202)
                return
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path.endswith("/upgrade"):
                upgrade_polls.append(self.path)
                self._json({"status": "queued", "progress": 0.0, "error": None, "best": False, "estimate_seconds": 1380, "eta_seconds": 1380}, 202)
                return
            if self.path.endswith("/project-2/beats"):  # speech: no steady beat
                self._json({"status": "done", "progress": 1.0, "error": None, "result": {"beats": [0, 2.9, 5.8], "downbeats": [], "bpm": 20.7}})
                return
            if self.path.endswith("/beats"):
                beats_polls.append(self.path)
                self._json({"status": "running", "progress": 0.3, "error": None, "result": None}, 202)
                return
            if self.path == "/api/v1/storage/cleanup":
                freed = disk["unused"] + disk["temp"] if body == {"confirmed": True} else 0
                disk.update(unused=0, temp=0)
                self._json({"freed_bytes": freed})
                return
            if self.path == "/api/v1/batches":
                submitted.append(body)
                title = body["inputs"][0]
                projects.append({
                    "project_id": "project-created", "input_type": "search_query",
                    "input_value": title, "source_id": "source-created", "title": title,
                    "created_at": "2026-10-14T10:00:00+00:00", "job_id": new_job_id,
                    "job_status": "queued", "progress": 0.0,
                })
                self._json({"batch_id": batch_id, "status": "queued", "job_ids": [new_job_id], "jobs": []}, 202)
            else:
                self._json({"error": {"code": "not_found", "message": "Not found"}}, 404)

        def do_DELETE(self):
            deleted.append(self.path)
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
            browser, launch_error = _launch_browser(playwright)
            if browser is None:
                pytest.skip(f"No browser for Playwright: {launch_error}")
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            if SOFT_CHECKS:
                page.set_default_timeout(10_000)  # a failed wait costs 10 s, not 30, when the run goes on past it
                page.set_default_navigation_timeout(30_000)  # the first load really takes longer
            page.add_init_script("""(() => { const C = window.AudioContext; window.__contexts = [];
                window.AudioContext = class extends C { constructor(...a) { super(...a); window.__contexts.push(this); } }; })()""")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="load")
            page.locator(".mixer-row").first.wait_for()
            # The player runs at the stems' own 44.1 kHz: at the device's rate Chrome resamples every stem on its own.
            page.wait_for_function("window.__contexts.length > 0")
            assert page.evaluate("window.__contexts.at(-1).sampleRate") == 44100
            # Tabs: 14 px text; a ready project shows no status dot, a failed one does.
            tab_style = "id => { const t = document.querySelector(`.project-tab[data-project-id='${id}']`); return [getComputedStyle(t).fontSize, getComputedStyle(t, '::before').display]; }"
            assert page.evaluate(tab_style, "project-0") == ["14px", "none"]
            assert page.evaluate(tab_style, "project-1")[1] != "none"  # failed
            # An open project shows only its card: no empty-state hint, subtitle, model line or file list.
            assert not page.locator("#project-empty").is_visible()
            for removed in ("#refresh-projects", "#asset-list", "#detail-input", "#detail-model", "#screen-help"):
                assert page.locator(removed).count() == 0
            # An open running project shows a progress bar instead of the mixer, then becomes the mixer.
            running_detail["project-5"] = "separating"
            projects[5].update(job_status="separating", progress=0.25, eta_seconds=720)
            page.locator('.project-tab[data-project-id="project-5"]').click()
            page.wait_for_function("document.querySelector('.job-progress-label') && document.querySelector('.job-progress-label').textContent === 'Разделяем дорожки · 25% · осталось ~12 мин'")
            assert page.locator(".mixer-row").count() == 0
            projects[5].update(progress=0.6)
            page.wait_for_function("document.querySelector('.job-progress > span').style.width === '60%'", timeout=15000)
            page.wait_for_timeout(900)  # after the width transition the fill is really drawn
            fill_width = page.evaluate("document.querySelector('.job-progress > span').getBoundingClientRect().width / document.querySelector('.job-progress').getBoundingClientRect().width")
            assert abs(fill_width - 0.6) < 0.02
            running_detail.pop("project-5")
            projects[5].update(job_status="completed", progress=1.0, eta_seconds=None)
            page.wait_for_function("document.querySelectorAll('.mixer-row').length === 6", timeout=15000)
            page.locator('.project-tab[data-project-id="project-0"]').click()
            page.wait_for_function("document.querySelector('#detail-title').textContent === 'Track 0'")

            # A running job fills its tab with its progress; polling updates the same element in place.
            projects[2].update(job_status="separating", progress=0.4)
            running_detail["project-2"] = "separating"
            page.locator('.project-tab[data-project-id="project-2"]').click()  # a fresh list starts the polling
            fill = """() => { const tab = document.querySelector('.project-tab[data-project-id="project-2"]');
                return {progress: tab.style.getPropertyValue('--job-progress'), image: getComputedStyle(tab).backgroundImage}; }"""
            page.wait_for_function("() => document.querySelector('.project-tab[data-project-id=\"project-2\"]').style.getPropertyValue('--job-progress') === '40%'", timeout=15000)
            page.evaluate("window.runningTab = document.querySelector('.project-tab[data-project-id=\"project-2\"]')")
            assert "linear-gradient" in page.evaluate(fill)["image"]
            running_detail.pop("project-2")
            projects[2].update(job_status="completed", progress=1.0)
            page.wait_for_function("() => window.runningTab.dataset.status === 'completed'", timeout=15000)
            assert page.evaluate(fill)["image"] == "none"
            assert page.evaluate("document.querySelector('.project-tab[data-project-id=\"project-2\"]') === window.runningTab")
            page.wait_for_function("document.querySelectorAll('.mixer-row').length === 6", timeout=15000)  # finished: mixer
            page.locator('.project-tab[data-project-id="project-0"]').click()
            page.wait_for_function("document.querySelector('#detail-title').textContent === 'Track 0' && document.querySelectorAll('#playback-panel audio').length === 6")
            # Re-opening the selected project (tab click, job-finished refresh) keeps the same players.
            page.evaluate("window.firstStem = document.querySelector('#playback-panel audio')")
            # Running jobs keep the page polling, so wait for this project's response, not network idle.
            with page.expect_response(lambda response: response.url.endswith("/api/v1/projects/project-0")):
                page.locator('.project-tab[data-project-id="project-0"]').click()
            page.wait_for_timeout(300)
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
            assert page.locator(".playback-buttons button").count() == 3  # ▶, ▶4 (count-in), ⏮
            assert page.locator('[data-action="toggle"]').get_attribute("aria-label") == "Воспроизвести"
            assert page.locator('[data-action="reset"]').get_attribute("aria-label") == "В начало и пауза"
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            tabs = page.locator("#project-tabs")
            # No visible scrollbar under the tabs.
            assert tabs.evaluate("el => getComputedStyle(el).scrollbarWidth") == "none"
            assert tabs.evaluate("el => el.offsetHeight - el.clientHeight") == 0
            if tabs.evaluate("el => el.scrollWidth > el.clientWidth"):
                fades = "el => [el.classList.contains('fade-left'), el.classList.contains('fade-right')]"
                tabs.evaluate("el => { el.scrollLeft = 0; }")
                page.wait_for_function("!document.querySelector('#project-tabs').classList.contains('fade-left')")
                assert tabs.evaluate(fades) == [False, True]
                # Mouse wheel scrolls the strip sideways.
                strip = tabs.bounding_box()
                page.mouse.move(strip["x"] + strip["width"] / 2, strip["y"] + strip["height"] / 2)
                page.mouse.wheel(0, 200)
                # Safari updates the fades on the scroll event a frame later.
                page.wait_for_function("document.querySelector('#project-tabs').classList.contains('fade-left')")
                assert tabs.evaluate(fades) == [True, True]
                # Dragging a tab scrolls the strip and does not open that tab.
                tabs.evaluate("el => { el.scrollLeft = 0; }")
                title_before = page.locator("#detail-title").inner_text()
                second_tab = page.locator(".project-tab").nth(1).bounding_box()
                start_x, y = second_tab["x"] + 20, second_tab["y"] + second_tab["height"] / 2
                page.mouse.move(start_x, y)
                page.mouse.down()
                page.mouse.move(start_x - 60, y, steps=6)
                page.mouse.move(start_x - 120, y, steps=6)
                page.mouse.up()
                assert abs(tabs.evaluate("el => el.scrollLeft") - 120) <= 6
                page.wait_for_timeout(300)
                assert page.locator("#detail-title").inner_text() == title_before
                tabs.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
                page.wait_for_function("!document.querySelector('#project-tabs').classList.contains('fade-right')")
                assert tabs.evaluate(fades) == [True, False]
            else:
                assert tabs.evaluate("el => el.classList.contains('fade-left') || el.classList.contains('fade-right')") is False
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

            # The timer sits at the right edge of the title row, as large as the stem names and thin.
            time_box = page.locator("#detail-time").bounding_box()
            first_wave = page.locator(".mixer-waveform").first.bounding_box()
            assert abs((time_box["x"] + time_box["width"]) - (first_wave["x"] + first_wave["width"])) <= 1
            name_font = page.locator(".mixer-name").first.evaluate("el => getComputedStyle(el).fontSize")
            assert page.locator("#detail-time").evaluate("el => [getComputedStyle(el).fontSize, getComputedStyle(el).fontWeight]") == [name_font, "300"]
            assert page.locator("#detail-controls .playback-time, #detail-controls #detail-time").count() == 0
            # Borderless volume slider with a 1px mark under the thumb position for 100 %.
            fader_geometry = page.locator(".mixer-fader").first.evaluate("""el => {
                const input = el.querySelector('input'), r = input.getBoundingClientRect(), f = el.getBoundingClientRect();
                const mark = getComputedStyle(el, '::before');
                return {thumb100: r.left + 6 + (r.width - 12) * 100 / 150, mark: f.left + parseFloat(mark.left) + 0.5,
                        markWidth: mark.width, markHeight: mark.height, markColor: mark.backgroundColor,
                        trackColor: getComputedStyle(document.documentElement).getPropertyValue('--line').trim(),
                        border: getComputedStyle(input).borderTopWidth, value: input.value};
            }""")
            assert fader_geometry["value"] == "100" and fader_geometry["border"] == "0px"
            assert abs(fader_geometry["thumb100"] - fader_geometry["mark"]) <= 1
            assert fader_geometry["markWidth"] == "1px" and fader_geometry["markHeight"] == "12px"
            assert fader_geometry["markColor"] == "rgb(43, 57, 51)" and fader_geometry["trackColor"] == "#2b3933"
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
            # Backspace works like ⏮: back to the start and paused, also while playing.
            page.keyboard.press("Space")
            page.wait_for_function("!document.querySelector('#playback-panel audio').paused")
            page.keyboard.press("Backspace")
            page.wait_for_function("document.querySelector('#playback-panel audio').paused")
            assert page.evaluate(audio_state)["t"] == 0
            assert page.locator('[data-action="toggle"]').get_attribute("aria-label") == "Воспроизвести"
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
            # Tab walks the tracks in a circle instead of moving the focus (so no focus rings).
            focused = page.evaluate("document.activeElement.outerHTML")
            page.keyboard.press("Tab")
            assert page.evaluate(selected)[0] == "true"
            page.keyboard.press("Shift+Tab")
            assert page.evaluate(selected)[-1] == "true"
            assert page.evaluate("document.activeElement.outerHTML") == focused
            for key, index in (("KeyD", 1), ("KeyB", 2), ("KeyG", 3), ("KeyP", 4), ("KeyO", 5), ("KeyV", 0)):
                page.keyboard.press(key)
                assert page.evaluate(selected)[index] == "true", key
            # + and − (=/- and the numpad) change the selected track's volume by 10 %, within 0-150 %.
            vocal_gain = page.locator('[data-control="gain"]').first
            assert vocal_gain.input_value() == "100"
            for key, expected in (("Equal", "110"), ("NumpadAdd", "120"), ("Equal", "130"), ("Equal", "140"),
                                  ("Equal", "150"), ("NumpadAdd", "150"), ("Minus", "140"), ("NumpadSubtract", "130")):
                page.keyboard.press(key)
                assert vocal_gain.input_value() == expected, key
            page.keyboard.press("Control+Equal")  # browser zoom: left alone
            assert vocal_gain.input_value() == "130"
            for _ in range(3):
                page.keyboard.press("Minus")
            assert vocal_gain.input_value() == "100"
            assert page.evaluate("Number(document.querySelector('#playback-panel audio').dataset.effectiveGain)") == 1
            # «Общая» under the stems: 0-100 % for the whole mix, remembered for the project; not a stem row.
            master_row = page.locator(".mixer-master")
            assert master_row.locator(".mixer-name").text_content() == "Общая"
            assert master_row.bounding_box()["y"] >= page.locator(".mixer-row").last.bounding_box()["y"] + 20
            master = page.locator('[data-control="master-volume"]')
            assert master.input_value() == "100" and page.evaluate("document.querySelector('#playback-panel').dataset.masterGain") == "1"
            master.evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            assert page.evaluate("document.querySelector('#playback-panel').dataset.masterGain") == "0.4"
            assert page.evaluate("localStorage.getItem('audio-stems-master-volume:project-0')") == "40"
            assert page.evaluate("Number(document.querySelector('#playback-panel audio').dataset.effectiveGain)") == 1  # stems untouched
            master.evaluate("el => { el.value = '100'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            # A clicked control keeps focus, but the next key never draws a focus ring around it.
            ring = "() => getComputedStyle(document.activeElement).outlineStyle"
            paused = "document.querySelector('#playback-panel audio').paused"
            solo = page.locator('[data-control="solo"]').first
            # The rows' columns line up with the playhead layer (Safari once shifted S/M under the layer).
            layer_x = page.locator(".playhead-layer").bounding_box()["x"]
            assert abs(page.locator(".mixer-waveform").first.bounding_box()["x"] - layer_x) <= 1
            assert solo.bounding_box()["x"] + solo.bounding_box()["width"] < layer_x
            solo.click()
            page.keyboard.press("Space")
            page.wait_for_function(f"!{paused}")
            assert page.evaluate(ring) == "none" and solo.get_attribute("aria-pressed") == "true"  # Space played, not re-pressed S
            page.keyboard.press("Space")
            page.wait_for_function(paused)
            solo.click()
            seek_layer = page.locator('[data-control="seek"]')
            seek_layer.click(position={"x": 5, "y": 5})
            page.keyboard.press("Space")
            page.wait_for_function(f"!{paused}")
            assert page.evaluate("document.activeElement.dataset.control") == "seek" and page.evaluate(ring) == "none"
            page.keyboard.press("Space")
            page.wait_for_function(paused)
            page.locator('.project-tab[data-project-id="project-0"]').click()
            page.keyboard.press("Space")
            page.wait_for_function(f"!{paused}")
            # Safari on macOS never focuses a clicked button, so there the focus stays where it was.
            assert page.evaluate("document.activeElement.dataset.projectId") in ("project-0", None) and page.evaluate(ring) == "none"
            page.keyboard.press("Space")
            page.wait_for_function(paused)
            page.keyboard.press("Home")
            # Metronome: the first press detects beats (progress on the button), then it clicks on them.
            metronome = page.locator(".footer-toggle").first
            assert metronome.text_content() == "Метроном" and metronome.get_attribute("aria-pressed") == "false"
            assert page.locator('[data-control="metronome-volume"]').input_value() == "50"
            metronome.click()
            page.wait_for_function("document.querySelector('.footer-toggle').textContent === 'Метроном · 30%'")
            page.wait_for_function("document.querySelector('.footer-toggle').textContent === 'Метроном' && !document.querySelector('.footer-toggle').dataset.analyzing", timeout=10000)
            assert metronome.get_attribute("aria-pressed") == "true" and beats_polls[0].endswith("/project-0/beats")
            assert metronome.evaluate("el => el.style.getPropertyValue('--job-progress')") == ""  # no leftover fill
            # The song's tempo next to the metronome, from the analysis (every four beats), one decimal, no ".0".
            bpm = page.locator(".metronome-bpm")
            assert bpm.text_content() == "240 BPM"
            page.keyboard.press("Space")
            page.wait_for_function("Number(document.querySelector('.footer-toggle').dataset.clicks) >= 2", timeout=10000)
            page.wait_for_function("document.querySelector('.metronome-bpm').textContent === '238.4 BPM'")
            page.keyboard.press("Space")
            page.wait_for_function(paused)
            page.keyboard.press("Home")
            page.wait_for_function("document.querySelector('.metronome-bpm').textContent === '240 BPM'")
            # ▶4: four clicks first, then the music; with the metronome off no more clicks follow.
            metronome.click()
            assert metronome.get_attribute("aria-pressed") == "false"
            before = int(metronome.get_attribute("data-clicks"))
            page.locator('[data-action="count-in"]').click()
            page.wait_for_function(f"Number(document.querySelector('.footer-toggle').dataset.clicks) === {before + 4}")
            assert page.evaluate(paused)  # still counting in
            page.wait_for_function(f"!{paused}", timeout=5000)
            page.wait_for_timeout(700)
            assert int(metronome.get_attribute("data-clicks")) == before + 4
            page.keyboard.press("Space")
            page.wait_for_function(paused)
            page.keyboard.press("Home")
            # Space during the count-in cancels it: the music does not start.
            page.locator('[data-action="count-in"]').click()
            page.wait_for_function("document.querySelector('[data-action=\"count-in\"]').getAttribute('aria-pressed') === 'true'")
            page.keyboard.press("Space")
            page.wait_for_timeout(1600)
            assert page.evaluate(paused) and page.locator('[data-action="count-in"]').get_attribute("aria-pressed") is None
            # Speed 25-150 % keeps the pitch: other speeds play the decoded stems through Signalsmith Stretch.
            speed = page.locator('[data-control="speed"]')
            assert [speed.get_attribute(a) for a in ("min", "max", "step")] == ["25", "150", "5"] and speed.input_value() == "100"
            # The 100 % mark and its label sit where the thumb is at 100 %; the metronome's mark at 50 %.
            marks = page.evaluate("""() => {
                const at = (fader, share) => { const r = fader.getBoundingClientRect(); return r.left + 6 + (r.width - 12) * share; };
                const mark = (fader) => fader.getBoundingClientRect().left + parseFloat(getComputedStyle(fader, '::before').left);
                const speed = document.querySelector('.speed-fader'), volume = document.querySelector('.footer-fader');
                const label = [...speed.querySelectorAll('.speed-scale span')].find((s) => s.textContent === '100%').getBoundingClientRect();
                return [mark(speed) - at(speed, 0.6), label.left + label.width / 2 - at(speed, 0.6), mark(volume) - at(volume, 0.5),
                        [...speed.querySelectorAll('.speed-scale span')].map((s) => s.textContent)]; }""")
            assert all(abs(offset) <= 1 for offset in marks[:3]), marks
            assert marks[3] == ["25%", "100%", "150%"]
            # Preparing the stems shows a translucent fill with the share done, gone when ready.
            page.evaluate("""() => { window.speedFills = []; const wrap = document.querySelector('.footer-speed');
                new MutationObserver(() => window.speedFills.push([wrap.style.getPropertyValue('--job-progress'), wrap.querySelector('.footer-speed-value').textContent]))
                    .observe(wrap, { attributes: true, subtree: true, childList: true, characterData: true }); }""")
            speed.evaluate("el => { el.value = '50'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            page.wait_for_function("document.querySelector('.footer-speed').dataset.engine === 'stretch'", timeout=20000)
            fills = page.evaluate("window.speedFills")
            assert any(fill not in ("", "0%", "100%") and text.startswith("подготовка ") for fill, text in fills), fills
            assert page.evaluate("getComputedStyle(document.querySelector('.footer-speed')).backgroundImage") == "none"
            assert page.locator(".footer-speed-value").text_content() == "50%"
            page.wait_for_function("document.querySelector('.metronome-bpm').textContent === '120 BPM'")  # heard at 50 %
            position = "Number(document.querySelector('[data-control=\"seek\"]').getAttribute('aria-valuenow'))"
            page.keyboard.press("Space")
            page.wait_for_timeout(2000)
            page.keyboard.press("Space")
            moved = page.evaluate(position)
            assert 0.6 < moved < 1.4, moved  # about one song second in two seconds at 50 %
            assert page.evaluate(paused)  # the streams stay silent while stretching
            speed.dblclick()
            page.wait_for_function("document.querySelector('.footer-speed').dataset.engine === 'stream'")
            assert speed.input_value() == "100" and page.locator(".footer-speed-value").text_content() == "100%"
            assert abs(page.evaluate("document.querySelector('#playback-panel audio').currentTime") - moved) < 0.1
            # «Улучшить качество»: progress on the button while the project keeps playing, then the player
            # moves to the new stems at the same position and plays on; the button then says it is done.
            upgrade = page.locator('[data-action="upgrade"]')
            assert upgrade.text_content() == "Улучшить качество · ~23 мин" and upgrade.is_enabled()
            if viewport[0] > 1000:  # midway between the metronome volume and the speed block
                gaps = page.evaluate("""() => { const v = document.querySelector('.footer-fader').getBoundingClientRect(),
                    u = document.querySelector('.footer-upgrade').getBoundingClientRect(), s = document.querySelector('.footer-speed').getBoundingClientRect();
                    return [u.left - v.right, s.left - u.right]; }""")
                assert abs(gaps[0] - gaps[1]) <= 2, gaps
            page.keyboard.press("Home")
            page.keyboard.press("Space")
            page.wait_for_function(f"!{paused}")
            upgrade.click()
            page.wait_for_function("document.querySelector('[data-action=\"upgrade\"]').textContent.startsWith('Улучшаем · 40%')", timeout=5000)
            assert upgrade.text_content() == "Улучшаем · 40% · осталось ~14 мин" and not page.evaluate(paused)
            before_switch = page.evaluate(position)
            page.wait_for_function("[...document.querySelectorAll('#playback-panel audio')].every(a => a.src.includes('-sw'))", timeout=10000)
            page.wait_for_function(f"!{paused}", timeout=10000)
            assert page.evaluate(position) >= before_switch - 0.3
            upgrade = page.locator('[data-action="upgrade"]')
            assert upgrade.text_content() == "Улучшенное качество разделения" and upgrade.is_disabled()
            assert page.evaluate("""() => getComputedStyle(document.querySelector('[data-action="upgrade"]')).getPropertyValue('--job-progress').trim()""") == "0%"  # no leftover progress fill once done
            page.keyboard.press("Space")
            page.wait_for_function(paused)
            page.keyboard.press("Home")
            metronome.click()
            volume_box = page.locator('[data-control="metronome-volume"]').bounding_box()
            page.mouse.click(volume_box["x"] + volume_box["width"] - 1, volume_box["y"] + volume_box["height"] / 2)
            assert page.locator('[data-control="metronome-volume"]').input_value() == "100"
            assert page.evaluate("localStorage.getItem('audio-stems-metronome-volume')") == "100"
            metronome.click()
            assert metronome.get_attribute("aria-pressed") == "false"
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
            page.wait_for_function("document.activeElement.id === 'inputs'")  # focused on the next tick
            page.keyboard.type("s m ")
            page.keyboard.press("ArrowRight")
            assert page.locator("#inputs").input_value() == "s m "
            page.keyboard.press("Backspace")  # erases in a text field, never moves the song
            assert page.locator("#inputs").input_value() == "s m"
            page.keyboard.type(" ")
            # Inside the search dialog Tab still moves the focus (Safari on macOS skips buttons on Tab, so Option+Tab).
            page.keyboard.press("Alt+Tab" if browser.browser_type.name == "webkit" else "Tab")
            assert page.evaluate("document.activeElement.id") == "submit-button"
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
            # No steady beat found: the metronome, its volume and ▶4 say so and stay off.
            page.locator(".footer-toggle").first.click()
            page.wait_for_function("document.querySelector('.footer-toggle').textContent === 'Ритм не найден'")
            assert page.evaluate("['.footer-toggle', '[data-control=\"metronome-volume\"]', '[data-action=\"count-in\"]'].every(s => document.querySelector(s).disabled)")
            assert page.locator(".footer-toggle").first.get_attribute("aria-pressed") == "false"
            page.locator('.project-tab[data-project-id="project-0"]').click()
            page.wait_for_function(
                "document.querySelector('#detail-title').textContent === 'Track 0' "
                "&& Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                "&& Array.from(document.querySelectorAll('#playback-panel audio')).every("
                "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                timeout=15_000,
            )

            # The project's disk size sits next to the bin; the dialog says what deleting frees.
            assert page.locator("#detail-size").text_content() == "12 МБ"
            page.locator("#delete-project").click()
            assert page.locator("#delete-dialog").is_visible()
            assert "Освободится 12 МБ." in page.locator("#delete-description").text_content()
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
            assert solo_style == ["rgb(216, 194, 0)", "rgb(255, 255, 255)"]
            assert [page.evaluate(opacity, i) for i in range(6)] == ["0.2", "0.2", "1", "0.2", "0.2", "0.2"]
            page.locator('[data-control="solo"]').nth(2).click()
            assert [page.evaluate(opacity, i) for i in range(6)] == ["1"] * 6
            switches_gap = page.locator(".mixer-switches").first.evaluate("el => getComputedStyle(el).columnGap")
            assert switches_gap == "11px"

            # Settings open as a popup over the project: a click outside or Esc closes them, × too.
            settings = page.locator("#settings-overlay")
            page.locator('[data-screen="settings"]').click()
            assert settings.is_visible() and page.locator("#screen-project").is_visible()
            assert page.locator(".back-link").count() == 0
            page.keyboard.press("Space")  # the player's hotkeys rest while the popup is open
            page.wait_for_timeout(300)
            assert page.evaluate(audio_state)["paused"]
            page.mouse.click(5, viewport[1] - 5)
            assert not settings.is_visible()
            page.locator('[data-screen="settings"]').click()
            page.keyboard.press("Escape")
            assert not settings.is_visible()
            page.locator('[data-screen="settings"]').click()
            assert settings.is_visible()
            # Appearance: the theme switch and accent hue apply at once and survive a reload.
            body_bg = "getComputedStyle(document.body).backgroundColor"
            swatch = "getComputedStyle(document.querySelector('.accent-swatch')).backgroundColor"
            dark_bg, default_accent = page.evaluate(body_bg), page.evaluate(swatch)
            # A 1px mark on the accent slider shows the default hue (197 of 360).
            accent_mark = page.locator(".accent-slider").evaluate("""el => {
                const r = el.querySelector('input').getBoundingClientRect(), f = el.getBoundingClientRect();
                const mark = getComputedStyle(el, '::before');
                return [r.left + 6 + (r.width - 12) * 197 / 360, f.left + parseFloat(mark.left) + 0.5, mark.height];
            }""")
            assert abs(accent_mark[0] - accent_mark[1]) <= 1 and accent_mark[2] == "12px"
            # Disk space: sizes per kind and one button for unused and temporary files.
            page.wait_for_function("document.querySelectorAll('#disk-usage dd').length === 4")
            assert page.locator("#disk-usage dd").all_text_contents() == ["3,0 ГБ", "40 МБ", "10 МБ", "800 МБ"]
            assert page.locator("#clean-storage").text_content() == "Очистить 50 МБ"
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#clean-storage").click()
            page.wait_for_function("document.querySelector('#disk-message').textContent === 'Освобождено 50 МБ.'")
            assert page.locator("#clean-storage").is_disabled()
            # «Удалить все проекты» asks once with the size, then deletes every project through the bin's route.
            delete_all = page.locator("#delete-all-projects")
            assert delete_all.text_content() == "Удалить все проекты (3,0 ГБ)" and delete_all.is_enabled()
            questions = []
            page.once("dialog", lambda dialog: (questions.append(dialog.message), dialog.accept()))
            before_deleted = len(deleted)
            delete_all.click()
            page.wait_for_function("document.querySelector('#disk-message').textContent.startsWith('Удалено проектов')")
            assert "3,0 ГБ" in questions[0] and f"({len(projects)})" in questions[0]
            assert sorted(deleted[before_deleted:]) == sorted(f"/api/v1/projects/{p['project_id']}" for p in projects)
            # While a separation runs, neither cleanup nor deleting everything is offered.
            disk["busy"] = True
            page.evaluate("document.querySelector('[data-screen=\"settings\"]').click()")
            page.wait_for_function("document.querySelector('#delete-all-projects').disabled")
            disk["busy"] = False
            # Separation: models are a radio list; the choice is remembered across reloads.
            radios = page.locator('#model-options input[type="radio"]')
            assert radios.count() == 2 and radios.nth(0).is_checked()
            assert page.locator(".model-option-name").nth(1).text_content() == "Demucs ft · 4 дорожки"
            # The time per minute of music: this machine's median, else the default estimate.
            assert page.locator(".model-option-time").all_text_contents() == [
                "~55 с на минуту песни · по последним разделениям на этом компьютере",
                "~11 мин на минуту песни · оценка, уточнится после первого разделения"]
            page.locator(".model-option").nth(1).click()
            # The theme buttons are a moon and a sun, named for screen readers.
            assert page.evaluate("[...document.querySelectorAll('[data-theme-choice]')].map(b => [b.textContent.trim(), b.getAttribute('aria-label'), !!b.querySelector('svg')])") == [
                ["", "Ночная тема", True], ["", "Дневная тема", True]]
            page.locator('[data-theme-choice="light"]').click()
            assert page.evaluate(body_bg) != dark_bg
            assert page.locator('[data-theme-choice="light"]').get_attribute("aria-pressed") == "true"
            page.locator("#accent-hue").evaluate("el => { el.value = '100'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            other_accent = page.evaluate(swatch)
            assert other_accent != default_accent
            page.reload(wait_until="load")
            assert page.evaluate("document.documentElement.dataset.theme") == "light"
            assert page.evaluate(swatch) == other_accent and page.locator("#accent-hue").input_value() == "100"
            page.wait_for_function("document.querySelectorAll('#model-options input').length === 2")
            assert page.locator('#model-options input[value="htdemucs_ft"]').is_checked()
            page.locator('[data-theme-choice="dark"]').click()
            page.locator("#accent-hue").evaluate("el => { el.value = '197'; el.dispatchEvent(new Event('input', { bubbles: true })); }")
            assert page.evaluate(body_bg) == dark_bg and page.evaluate(swatch) == default_accent
            assert page.locator("#settings-overlay").is_visible()  # the reload kept #settings
            page.locator("#close-settings").click()
            assert not page.locator("#settings-overlay").is_visible()
            # After the reload above the project list loads asynchronously.
            page.wait_for_function("document.querySelector('#detail-title').textContent === 'Track 0'")
            # A four-stem project shows four rows, and the playhead spans exactly them.
            page.locator('.project-tab[data-project-id="project-3"]').click()
            page.wait_for_function("document.querySelectorAll('.mixer-row').length === 4")
            assert page.locator('[data-action="upgrade"]').text_content() == "Улучшенное качество разделения"  # already the best model
            assert page.evaluate("""() => getComputedStyle(document.querySelector('[data-action="upgrade"]')).getPropertyValue('--job-progress').trim()""") == "0%"  # the same look as any best-model project
            assert page.evaluate("document.querySelector('.playhead-layer').style.gridRow") == "1 / span 4"
            page.keyboard.press("KeyG")  # no guitar row: the selection stays put
            assert page.evaluate("[...document.querySelectorAll('.mixer-row')].map(r => r.dataset.selected)") == ["true", "false", "false", "false"]
            # Leading silence is skipped: the stems wait at the music start and the timer counts from it.
            page.locator('.project-tab[data-project-id="project-6"]').click()
            page.wait_for_function("[...document.querySelectorAll('#playback-panel audio')].length === 6 && [...document.querySelectorAll('#playback-panel audio')].every(a => Math.abs(a.currentTime - 1) < 0.01)")
            assert page.locator("#detail-time").text_content() == "0:00 / 0:04"
            # Files deleted by hand: the project says which and offers to separate the song again.
            page.locator('.project-tab[data-project-id="project-4"]').click()
            page.wait_for_function("document.querySelector('#playback-panel').textContent.includes('С диска удалены файлы проекта: Vocal, Drums.')")
            page.get_by_role("button", name="Разделить заново").click()
            page.wait_for_function("document.querySelector('.project-tab[data-project-id=\"project-created\"]')")
            assert deleted[-1] == "/api/v1/projects/project-4"
            assert submitted[-1] == {"inputs": ["Track 4"], "model_name": "htdemucs_6s"}
            projects[:] = [p for p in projects if p["project_id"] not in ("project-created",)]
            submitted.clear()
            page.locator('.project-tab[data-project-id="project-0"]').click()
            page.wait_for_function("document.querySelectorAll('.mixer-row').length === 6")

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
            page.wait_for_function("document.activeElement.id === 'inputs'")
            page.locator("#inputs").fill("")  # the text typed earlier is still there
            page.keyboard.type("New track")
            page.keyboard.press("Shift+Enter")  # a new line, not a submission
            assert page.locator("#search-overlay").is_visible()
            page.keyboard.type("Second track")
            page.keyboard.press("Enter")  # starts the search
            page.wait_for_function("document.querySelector('#search-overlay').hidden")
            page.locator('.project-tab[data-project-id="project-created"]').wait_for(state="visible")
            assert submitted[-1] == {"inputs": ["New track", "Second track"], "model_name": "htdemucs_ft"}

            # Your own file: «Открыть аудиофайл» comes first in the dialog, above the YouTube search.
            page.locator("#search-trigger").click()
            assert page.evaluate("""() => document.querySelector('#open-file').getBoundingClientRect().bottom
                < document.querySelector('#inputs').getBoundingClientRect().top""")
            assert "Открыть аудиофайл" in page.locator("#search-preview").inner_text()
            page.locator("#file-input").set_input_files({"name": "Моя запись.wav", "mimeType": "audio/wav", "buffer": wav_bytes})
            page.wait_for_function("document.querySelector('#search-overlay').hidden")
            page.locator('.project-tab[data-project-id="project-upload-1"]').wait_for(state="visible")
            assert uploaded[-1][:2] == ("Моя запись.wav", wav_bytes)
            assert uploaded[-1][2] == "/api/v1/uploads?model_name=htdemucs_ft"
            # Dragging files over the window shows the drop zone; dropping one starts it like the button.
            drag = """([name, kind]) => { const data = new DataTransfer();
                data.items.add(new File([new Uint8Array([102, 76, 97, 67])], name, { type: 'audio/flac' }));
                document.body.dispatchEvent(new DragEvent(kind, { bubbles: true, cancelable: true, dataTransfer: data })); }"""
            page.evaluate(drag, ["dropped.flac", "dragenter"])
            assert page.locator("#drop-overlay").is_visible()
            page.evaluate(drag, ["dropped.flac", "dragleave"])
            assert not page.locator("#drop-overlay").is_visible()
            page.evaluate(drag, ["dropped.flac", "dragenter"])
            page.evaluate(drag, ["dropped.flac", "drop"])
            assert not page.locator("#drop-overlay").is_visible()
            page.locator('.project-tab[data-project-id="project-upload-2"]').wait_for(state="visible")
            assert uploaded[-1][:2] == ("dropped.flac", b"fLaC")
            assert not errors, f"Browser errors: {errors}"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


_soft_failures: list[str] = []


def _soft_failed(line: int, error: Exception, scope: dict) -> None:
    text = Path(__file__).read_text(encoding="utf-8").splitlines()[line - 1].strip()
    message = (str(error).strip().splitlines() or [""])[0]
    _soft_failures.append(f"line {line}: {type(error).__name__}: {message}\n    {text}")
    if len(_soft_failures) >= 25:  # past this they are mostly knock-on failures, and each wait costs 10 s
        pytest.fail(f"Stopped after {len(_soft_failures)} failed checks:" + chr(10) + chr(10).join(_soft_failures), pytrace=False)
    if scope.get("page") is not None:
        folder = Path(SOFT_CHECKS)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            scope["page"].screenshot(path=str(folder / f"{scope.get('viewport', ('',))[0]}-line{line}.png"))
        except Exception:
            pass


def _soft_checks(test):
    """The same test with each simple statement (assert, call, assignment) wrapped in try/except, so a failed
    check is recorded and the run goes on; nested classes and functions stay as they are. Later failures can
    be caused by earlier ones, so read the list from the top."""
    import ast
    import functools
    import inspect

    tree = ast.parse(inspect.getsource(test))
    ast.increment_lineno(tree, test.__code__.co_firstlineno - 1)
    function = tree.body[0]
    function.decorator_list = []

    class Wrap(ast.NodeTransformer):
        def visit_ClassDef(self, node):
            return node

        def visit_FunctionDef(self, node):
            return self.generic_visit(node) if node is function else node

        def generic_visit(self, node):
            super().generic_visit(node)
            for field in ("body", "orelse", "finalbody"):
                statements = getattr(node, field, None)
                if isinstance(statements, list) and statements and isinstance(statements[0], ast.stmt):
                    setattr(node, field, [self.wrap(s) if isinstance(s, (ast.Assert, ast.Expr, ast.Assign)) else s for s in statements])
            return node

        @staticmethod
        def wrap(statement):
            guard = ast.parse(f"try:\n pass\nexcept Exception as _soft_error:\n _soft_failed({statement.lineno}, _soft_error, locals())").body[0]
            for node in ast.walk(guard):
                if hasattr(node, "lineno"):
                    node.lineno = node.end_lineno = statement.lineno
            guard.body = [statement]
            return guard

    namespace = {}
    exec(compile(ast.fix_missing_locations(Wrap().visit(tree)), __file__, "exec"), test.__globals__, namespace)

    @functools.wraps(test)
    def run(*args, **kwargs):
        _soft_failures.clear()
        namespace[test.__name__](*args, **kwargs)
        if _soft_failures:
            pytest.fail(f"{len(_soft_failures)} failed checks:\n" + "\n".join(_soft_failures), pytrace=False)

    return run


if SOFT_CHECKS:
    test_v3_layout_search_navigation_and_mixer_in_browser = _soft_checks(test_v3_layout_search_navigation_and_mixer_in_browser)
