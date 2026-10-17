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
        output.writeframes(b"\0\0\0\0" * 16_000)
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
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(wav_bytes)))
                self.end_headers()
                self.wfile.write(wav_bytes)
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
            assert page.locator(".project-tab").count() == 14
            assert page.locator(".project-tab").first.get_attribute("data-status") == "completed"
            assert "Ошибка" not in page.locator("#project-tabs").inner_text()
            assert page.locator("#search-trigger").bounding_box()["width"] <= 760
            assert page.locator("#project-tabs").evaluate("el => el.compareDocumentPosition(document.querySelector('#project-workspace')) & Node.DOCUMENT_POSITION_FOLLOWING")
            assert page.locator(".mixer-row").count() == 6
            assert page.locator(".mixer-row").first.locator(".mixer-left > *").count() == 3
            assert page.locator(".playback-buttons button").count() == 2
            assert page.locator('[data-action="toggle"]').get_attribute("aria-label") == "Воспроизвести"
            assert page.locator('[data-action="reset"]').get_attribute("aria-label") == "В начало и пауза"
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            tabs = page.locator("#project-tabs")
            tabs.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
            assert tabs.evaluate("el => el.scrollLeft > 0")
            page.wait_for_function(
                "Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                "&& Array.from(document.querySelectorAll('#playback-panel audio')).every(" 
                "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                timeout=15_000,
            )

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
            page.locator('[data-control="mute"]').first.click()
            assert float(audio.get_attribute("data-effective-gain")) == 1.5

            page.locator('[data-screen="settings"]').click()
            assert page.locator("#screen-settings").is_visible()
            page.locator('[data-screen="project"]').first.click()
            assert page.locator("#detail-title").inner_text() == "Track 0"

            page.locator("#search-trigger").click()
            overlay = page.locator("#search-overlay")
            assert overlay.is_visible()
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
