"""Reports in a real browser (version 25): the question after the installation and «Send report» on a failed project."""
import json
import threading

import pytest

from tests.test_ui_browser import ROOT, _launch_browser, _project_detail, _QuietHandler, _Server

REPORT = {"kind": "install_ok", "version": "25", "platform": "windows", "stage": "", "message": ""}
FAILED = {"kind": "error_report", "version": "25", "platform": "windows", "stage": "download", "message": "Video search failed"}


def _serve(posted):
    class Handler(_QuietHandler):
        def _json(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.partition("?")[0]
            if path == "/api/v1/reports/install":
                self._json({"ask": not posted, "report": REPORT})
            elif path == "/api/v1/projects/p1/report":
                self._json(FAILED)
            elif path == "/api/v1/projects":
                self._json([{"project_id": "p1", "input_type": "search_query", "input_value": "Song", "source_id": "s",
                             "title": "Song", "created_at": "2026-11-25T10:00:00+00:00", "job_id": "j1",
                             "job_status": "failed", "progress": 0}])
            elif path == "/api/v1/projects/p1":
                detail = _project_detail("p1", "j1", "Song", ())
                detail["job"].update(status="failed", error_summary="Video search failed")
                detail["assets"] = []
                self._json(detail)
            elif path in ("/api/v1/queue", "/api/v1/models", "/api/v1/plugins"):
                self._json([])
            elif path.startswith("/api/v1/"):
                self._json({})
            else:
                super().do_GET()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path == "/api/v1/reports":
                posted.append(body)
            self.send_response(204)
            self.end_headers()

    server = _Server(("127.0.0.1", 0), lambda *args, **kwargs: Handler(*args, directory=str(ROOT / "web"), **kwargs))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.browser
def test_the_install_question_and_a_failed_projects_report_show_what_is_sent_and_send_on_click():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    posted = []
    server = _serve(posted)
    try:
        with sync_playwright() as playwright:
            browser, launch_error = _launch_browser(playwright)
            if browser is None:
                pytest.skip(f"No browser: {launch_error}")
            page = browser.new_page(viewport={"width": 1365, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            dialog = page.locator("#confirm-dialog")
            dialog.wait_for(state="visible")
            assert page.locator("#confirm-title").text_content() == "Audio Stems is installed"
            assert "version 25, Windows" in page.locator("#confirm-description").text_content()
            assert page.locator("#confirm-cancel").text_content() == "No, thanks"
            assert posted == []  # nothing before the click
            page.locator("#confirm-action").click()
            dialog.wait_for(state="hidden")
            assert posted == [{"kind": "install_ok", "send": True}]

            page.locator(".project-tab").first.click()
            button = page.locator("#send-report")
            button.wait_for(state="visible")
            button.click()
            dialog.wait_for(state="visible")
            text = page.locator("#confirm-description").text_content()
            assert "Stage: download." in text and "Error: Video search failed" in text
            assert page.locator("#confirm-cancel").text_content() == "Cancel"
            page.locator("#confirm-action").click()
            dialog.wait_for(state="hidden")
            assert posted[-1] == {"kind": "error_report", "project_id": "p1"} and button.is_hidden()
            with page.expect_response("**/api/v1/reports/install"):
                page.reload(wait_until="load")
            page.wait_for_timeout(300)
            assert dialog.is_hidden()  # answered: not asked again
            page.locator('a[data-screen="settings"]').click()
            assert page.locator("#privacy-title").is_visible()
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
