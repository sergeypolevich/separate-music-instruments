"""The page in another language, in a real browser: from the first frame, switched in place, plural forms, right to left."""
import json
import shutil
import threading
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler

import pytest

from audio_stems import i18n
from tests.test_ui_browser import ROOT, _launch_browser, _Server

GERMAN = {
    "Settings": "Einstellungen", "Language": "Sprache", "Appearance": "Darstellung", "Theme": "Design",
    "Open an audio file or find a song…": "Audiodatei öffnen oder Song suchen…", "Close settings": "Einstellungen schließen",
    "Projects will appear here": "Projekte erscheinen hier", "Separation models": "Trennmodelle",
    "The file is larger than {limit} MB": "Die Datei ist größer als {limit} MB",
}
POLISH = {
    "Settings": "Ustawienia", "Queue: {count} tasks": {
        "one": "Kolejka: {count} zadanie", "few": "Kolejka: {count} zadania", "many": "Kolejka: {count} zadań", "other": "Kolejka: {count} zadania"},
}
HEBREW = {"Settings": "הגדרות"}
# Before the first paint: the page is either hidden or already German (no English for a moment).
FIRST_FRAME = """requestAnimationFrame(() => { window.firstFrame = {
  visibility: document.body ? getComputedStyle(document.body).visibility : "no body",
  search: document.querySelector("#search-preview")?.textContent } });"""


def _serve(web):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _send(self, body: bytes, kind: str, status=200):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                cookie = SimpleCookie(self.headers.get("Cookie", "")).get(i18n.COOKIE)
                code = i18n.choose(cookie.value if cookie else None, self.headers.get("Accept-Language", ""), i18n.offered(web))
                self._send(i18n.page(code, web).encode(), "text/html; charset=utf-8")
            elif path.startswith("/api/v1/"):
                answers = {"/api/v1/projects": [], "/api/v1/queue": [], "/api/v1/models": [],
                           "/api/v1/storage": {"projects_bytes": 3 * 2**30, "unused_bytes": 0, "temp_bytes": 0, "models_bytes": 0, "busy": False}}
                self._send(json.dumps(answers.get(path, {})).encode(), "application/json", 200 if path in answers else 404)
            else:
                super().do_GET()

    server = _Server(("127.0.0.1", 0), lambda *args, **kwargs: Handler(*args, directory=str(web), **kwargs))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_the_page_shows_the_chosen_language_from_the_first_frame_and_switches_in_place(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    web = tmp_path / "web"
    shutil.copytree(ROOT / "web", web)
    for shipped in (web / "i18n").glob("*.json"):  # only this test's own languages are offered
        if shipped.name != "catalog.json":
            shipped.unlink()
    for code, dictionary in (("de", GERMAN), ("pl", POLISH), ("he", HEBREW)):
        (web / "i18n" / f"{code}.json").write_text(json.dumps(dictionary, ensure_ascii=False), encoding="utf-8")
    server = _serve(web)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser, launch_error = _launch_browser(playwright)
            if browser is None:
                pytest.skip(f"No browser: {launch_error}")
            context = browser.new_context(locale="de-DE", viewport={"width": 1365, "height": 900})
            context.add_init_script(FIRST_FRAME)
            page = context.new_page()
            # First start with a German browser: the page comes German, and nothing English showed before.
            page.goto(base, wait_until="load")
            page.wait_for_function("window.firstFrame !== undefined")
            first = page.evaluate("window.firstFrame")
            assert first["visibility"] == "hidden" or first["search"] == "Audiodatei öffnen oder Song suchen…", first
            assert page.evaluate("document.documentElement.lang") == "de"
            assert not page.evaluate("document.documentElement.classList.contains('i18n-pending')")
            assert page.locator("#search-preview").text_content() == "Audiodatei öffnen oder Song suchen…"
            assert page.locator('a[data-screen="settings"]').get_attribute("aria-label") == "Einstellungen"
            assert page.locator("#project-tabs .tabs-empty").text_content() == "Projekte erscheinen hier"
            # A server message with a value in it is found by its template; an unknown one stays as it came.
            assert page.evaluate("tServer('The file is larger than 500 MB')") == "Die Datei ist größer als 500 MB"
            assert page.evaluate("tServer('Separator exited with status 3')") == "Separator exited with status 3"

            # Settings → Language lists the shipped languages, each in itself; a choice applies in place.
            page.evaluate("window.notReloaded = true")
            page.locator('a[data-screen="settings"]').click()
            assert page.locator("#settings-title").text_content() == "Einstellungen"
            assert page.locator("#language-current").text_content() == "Deutsch"
            page.locator("#language-button").click()
            items = page.locator("#language-menu [data-language]")
            assert items.evaluate_all("items => items.map(i => [i.dataset.language, i.textContent])") == [
                ["en", "English"], ["de", "DeutschGerman"], ["he", "עברית" + "Hebrew"], ["pl", "PolskiPolish"]]
            page.keyboard.press("Escape")  # closes the list, not Settings
            assert page.locator("#language-menu").is_hidden() and page.locator("#settings-overlay").is_visible()
            page.locator("#language-button").click()
            page.locator('#language-menu [data-language="en"]').click()
            page.wait_for_function("document.documentElement.lang === 'en'")
            assert page.locator("#settings-title").text_content() == "Settings"
            assert page.locator("#search-preview").text_content() == "Open an audio file or find a song…"
            assert page.locator("#close-settings").get_attribute("aria-label") == "Close settings"
            assert page.evaluate("window.notReloaded") and "audio-stems-language=en" in page.evaluate("document.cookie")

            # Polish: the plural form by the language's rules, decimals with its comma.
            page.locator("#language-button").click()
            page.locator('#language-menu [data-language="pl"]').click()
            page.wait_for_function("document.documentElement.lang === 'pl'")
            assert page.locator("#settings-title").text_content() == "Ustawienia"
            assert page.evaluate("[1, 3, 5].map((n) => tn(n, 'Queue: {count} task', 'Queue: {count} tasks'))") == [
                "Kolejka: 1 zadanie", "Kolejka: 3 zadania", "Kolejka: 5 zadań"]
            assert page.evaluate("formatDecimal(1.5)") == "1,5"
            assert page.evaluate("t('{n} GB', { n: formatDecimal(3) })") == "3,0 GB"  # an untranslated text keeps its values

            # Hebrew: the text runs right to left, the layout is not mirrored; the server sends it next time.
            search_x = page.locator("#search-trigger").bounding_box()["x"]
            page.locator("#language-button").click()
            page.locator('#language-menu [data-language="he"]').click()
            page.wait_for_function("document.documentElement.lang === 'he'")
            assert page.evaluate("document.documentElement.hasAttribute('data-rtl')")
            assert page.evaluate("getComputedStyle(document.querySelector('#settings-title')).unicodeBidi") == "plaintext"
            assert page.locator("#search-trigger").bounding_box()["x"] == search_x
            page.reload(wait_until="load")
            assert page.evaluate("document.documentElement.lang") == "he"
            assert page.locator('a[data-screen="settings"]').get_attribute("aria-label") == HEBREW["Settings"]
            browser.close()
    finally:
        server.shutdown()
