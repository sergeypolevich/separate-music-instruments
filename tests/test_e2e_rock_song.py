"""Full live E2E driven by Playwright in a visible installed Edge/Chrome window.

Required before each commit:
    $env:RUN_LIVE_E2E = "1"; py -m pytest -q -m e2e -s

The test searches YouTube, downloads real audio, runs Demucs on CPU, and
exercises the application through the actual browser UI. App data is isolated
under pytest's temporary directory.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest


pytestmark = pytest.mark.e2e
ROOT = Path(__file__).parents[1]
QUERY = "Band - Rock Song (Official Music Video)"
EXPECTED_VIDEO_ID = "<video-id>"
EXPECTED_STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")


def _free_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_for_health(url: str, process: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Desktop supervisor exited early with code {process.returncode}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise TimeoutError("Desktop supervisor did not make the local API ready")


def _launch_visible_browser(playwright):
    failures = []
    for browser_name, channel in (("Edge", "msedge"), ("Chrome", "chrome")):
        try:
            return playwright.chromium.launch(
                channel=channel,
                headless=False,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
        except Exception as exc:
            failures.append(f"{browser_name}: {exc}")
    raise AssertionError("Install Microsoft Edge or Chrome: " + " | ".join(failures))


def test_rock_song_full_desktop_browser_flow(tmp_path):
    if os.getenv("RUN_LIVE_E2E") != "1":
        pytest.skip("Set RUN_LIVE_E2E=1 to run the full YouTube + Demucs browser test")

    from pytubefix import Search
    from playwright.sync_api import sync_playwright

    # Do not process a substitute if YouTube's first search result changes.
    results = Search(QUERY).videos
    assert results, "YouTube search returned no candidates"
    first_id = str(getattr(results[0], "video_id", ""))
    assert first_id == EXPECTED_VIDEO_ID, (
        f"First YouTube result changed to {first_id!r}; refusing to download a different video"
    )

    port = _free_local_port()
    base_url = f"http://127.0.0.1:{port}"
    database = tmp_path / "audio_stems.db"
    media_root = tmp_path / "Music"
    stop_file = tmp_path / "stop-supervisor"
    log_path = tmp_path / "desktop-e2e.log"
    environment = os.environ.copy()
    environment.update({
        "AUDIO_STEMS_API_PORT": str(port),
        "AUDIO_STEMS_DATABASE_URL": "sqlite:///" + database.as_posix(),
        "AUDIO_STEMS_STORAGE_ROOT": str(media_root),
        "AUDIO_STEMS_TEMP_ROOT": str(media_root / "_demucs_temp"),
        "AUDIO_STEMS_LOG_FILE": str(tmp_path / "logs" / "app.jsonl"),
        "AUDIO_STEMS_RETRY_DELAYS": "0,0",
        "AUDIO_STEMS_OPEN_BROWSER": "0",
        "AUDIO_STEMS_STOP_FILE": str(stop_file),
    })

    browser = None
    page = None
    process = None
    with log_path.open("w", encoding="utf-8") as app_log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "desktop.py")],
            cwd=ROOT,
            env=environment,
            stdout=app_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_health(f"{base_url}/api/v1/health", process)
            with sync_playwright() as playwright:
                browser = _launch_visible_browser(playwright)
                page = browser.new_page()
                page.set_default_timeout(30_000)
                page_errors = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(base_url, wait_until="networkidle", timeout=60_000)

                assert page.locator('link[href="app.css"]').count() == 1
                assert page.locator('script[src="app.js"]').count() == 1
                background = page.locator("body").evaluate("element => getComputedStyle(element).backgroundColor")
                assert background not in {"rgba(0, 0, 0, 0)", "transparent"}, "CSS did not apply"
                page.locator("#model").select_option("htdemucs_6s")

                # Verify that the real browser completed the local API call.
                with page.expect_response(lambda response: response.url.endswith("/api/v1/updates")):
                    page.locator("#check-updates").click()
                page.wait_for_function("!document.querySelector('#check-updates').disabled")
                assert "Failed to fetch" not in page.locator("#updates-message").inner_text()

                page.locator("#inputs").fill(QUERY)
                page.locator("#submit-button").click()
                page.wait_for_function(
                    "() => { const job = document.querySelector('.job-card'); "
                    "return job && ['completed', 'failed'].includes(job.dataset.status); }",
                    timeout=90 * 60 * 1000,
                )
                status = page.locator(".job-card").get_attribute("data-status")
                if status != "completed":
                    failure = page.locator(".job-error").inner_text() if page.locator(".job-error").count() else "no error summary"
                    raise AssertionError(f"Rock Song job finished with status {status!r}: {failure}")
                project = page.locator(".project-card").first
                project.wait_for(timeout=60_000)
                assert "blur" in project.inner_text().lower() and "rock song" in project.inner_text().lower()
                project.click()

                tracks = page.locator("#playback-panel audio")
                tracks.nth(5).wait_for(state="attached", timeout=60_000)
                page.wait_for_function(
                    "Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                    "&& Array.from(document.querySelectorAll('#playback-panel audio')).every("
                    "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                    timeout=60_000,
                )
                assert [tracks.nth(index).get_attribute("data-stem") for index in range(6)] == list(EXPECTED_STEMS)
                assert page.locator("#asset-list .asset-available").count() == 7

                page.locator('[data-action="play"]').click()
                page.wait_for_function("Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => !audio.paused)")
                position = page.locator("#playback-panel audio").first.evaluate("audio => audio.currentTime")
                page.wait_for_function(
                    "position => document.querySelector('#playback-panel audio').currentTime > position",
                    arg=position,
                )

                # Real mouse click at the rendered midpoint of the shared timeline.
                seek = page.locator('[data-control="seek"]')
                box = seek.bounding_box()
                assert box is not None
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_function(
                    "() => { const audio = document.querySelector('#playback-panel audio'); "
                    "return !audio.paused && Math.abs(audio.currentTime / audio.duration - 0.5) < 0.08; }"
                )

                gains = page.locator('[data-control="gain"]')
                mutes = page.locator('[data-control="mute"]')
                assert gains.count() == mutes.count() == 6
                for index in range(6):
                    gain = gains.nth(index)
                    box = gain.bounding_box()
                    assert box is not None
                    page.mouse.click(box["x"] + box["width"] * 0.25, box["y"] + box["height"] / 2)
                    page.wait_for_function(
                        "index => Math.abs(document.querySelectorAll('#playback-panel audio')[index].volume - 0.25) < 0.08",
                        arg=index,
                    )
                    page.mouse.click(box["x"] + box["width"] * 0.75, box["y"] + box["height"] / 2)
                    page.wait_for_function(
                        "index => Math.abs(document.querySelectorAll('#playback-panel audio')[index].volume - 0.75) < 0.08",
                        arg=index,
                    )
                    mute = mutes.nth(index)
                    mute.check()
                    page.wait_for_function(
                        "index => document.querySelectorAll('#playback-panel audio')[index].volume === 0",
                        arg=index,
                    )
                    mute.uncheck()
                    page.wait_for_function(
                        "index => Math.abs(document.querySelectorAll('#playback-panel audio')[index].volume - 0.75) < 0.08",
                        arg=index,
                    )
                page.locator('[data-action="pause"]').click()
                page.wait_for_function("Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => audio.paused)")

                # Reopen the saved project from the library and play it again.
                page.locator(".project-card").first.click()
                page.wait_for_function(
                    "Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                    "&& Array.from(document.querySelectorAll('#playback-panel audio')).every("
                    "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                    timeout=60_000,
                )
                page.locator('[data-action="play"]').click()
                page.wait_for_function("Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => !audio.paused)")
                page.locator('[data-action="pause"]').click()
                assert not page_errors, f"Browser page errors: {page_errors}"
        except Exception:
            app_log.flush()
            print("\n--- Audio Stems E2E service log tail ---\n" + log_path.read_text(encoding="utf-8", errors="replace")[-8000:])
            if page is not None:
                try:
                    print("\n--- Browser URL ---\n" + page.url)
                    print("\n--- Browser screenshot ---\n" + str(tmp_path / "browser-failure.png"))
                    page.screenshot(path=str(tmp_path / "browser-failure.png"))
                except Exception:
                    pass
            raise
        finally:
            # The Playwright context manager owns and closes the visible browser.
            stop_file.touch()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)

    assert process.returncode == 0
    assert database.is_file()
    assert any(media_root.glob("*.webm")) or any(media_root.glob("*.m4a")) or any(media_root.glob("*.mp3"))
    for stem in EXPECTED_STEMS:
        assert list((media_root / "Stems").glob(f"*/{stem}.wav")), f"{stem}.wav was not stored"
