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
QUERY = "John Denver - The Ballad of Spiro Agnew"
EXPECTED_VIDEO_ID = "GDXxm2gu-_E"
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


def test_spiro_agnew_full_desktop_browser_flow(tmp_path):
    if os.getenv("RUN_LIVE_E2E") != "1":
        pytest.skip("Set RUN_LIVE_E2E=1 to run the full YouTube + Demucs browser test")

    from playwright.sync_api import sync_playwright

    reuse_root_value = os.getenv("AUDIO_STEMS_E2E_REUSE_DIR")
    reuse_root = Path(reuse_root_value) if reuse_root_value else None
    if reuse_root is None:
        from pytubefix import Search

        # Do not process a substitute if YouTube's first search result changes.
        results = Search(QUERY).videos
        assert results, "YouTube search returned no candidates"
        first_id = str(getattr(results[0], "video_id", ""))
        assert first_id == EXPECTED_VIDEO_ID, (
            f"First YouTube result changed to {first_id!r}; refusing to download a different video"
        )

    port = _free_local_port()
    base_url = f"http://127.0.0.1:{port}"
    data_root = reuse_root or tmp_path
    database = data_root / "audio_stems.db"
    media_root = data_root / "YouTube Audio"
    if reuse_root is not None and (not database.is_file() or not media_root.is_dir()):
        pytest.fail("AUDIO_STEMS_E2E_REUSE_DIR must contain audio_stems.db and YouTube Audio")
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
            [sys.executable, "-m", "audio_stems.desktop"],
            cwd=ROOT,
            env=environment,
            stdout=app_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_health(f"{base_url}/api/v1/health", process)
            with sync_playwright() as playwright:
                browser = _launch_visible_browser(playwright)
                page = browser.new_page(no_viewport=True)  # fill the visible window instead of a 1280x720 viewport
                page.set_default_timeout(30_000)
                page_errors = []
                asset_responses = []
                asset_request_failures = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on(
                    "response",
                    lambda response: asset_responses.append((response.status, response.url))
                    if "/api/v1/assets/" in response.url else None,
                )
                page.on(
                    "requestfailed",
                    lambda request: asset_request_failures.append((request.url, request.failure))
                    if "/api/v1/assets/" in request.url else None,
                )
                # The app polls its API, so the page may never become network-idle.
                page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)

                assert page.locator('link[href="app.css"]').count() == 1
                assert page.locator('script[src="app.js"]').count() == 1
                background = page.locator("body").evaluate("element => getComputedStyle(element).backgroundColor")
                assert background not in {"rgba(0, 0, 0, 0)", "transparent"}, "CSS did not apply"
                if reuse_root is None:
                    # Verify that the real browser completed the local update API call.
                    page.locator('[data-screen="settings"]').click()
                    update_message = page.locator("#updates-message")
                    previous_update_message = update_message.inner_text()
                    page.locator("#check-updates").click()
                    page.wait_for_function(
                        "previous => document.querySelector('#updates-message').textContent !== previous",
                        arg=previous_update_message,
                        timeout=120_000,
                    )
                    page.wait_for_function(
                        "!document.querySelector('#check-updates').disabled",
                        timeout=120_000,
                    )
                    assert "failed to fetch" not in update_message.inner_text().lower()

                page.locator('[data-screen="project"]').first.click()
                if reuse_root is None:
                    page.locator("#search-trigger").click()
                    page.locator("#model").select_option("htdemucs_6s")
                    page.locator("#inputs").fill(QUERY)
                    page.locator("#submit-button").click()
                    page.wait_for_function("document.querySelector('#search-overlay').hidden")
                    page.wait_for_function(
                        "() => { const tab = document.querySelector('.project-tab'); "
                        "return tab && ['completed', 'failed'].includes(tab.dataset.status); }",
                        timeout=90 * 60 * 1000,
                    )
                status = page.locator(".project-tab").first.get_attribute("data-status")
                if status != "completed":
                    failure = page.locator("#detail-error").inner_text() if not page.locator("#detail-error").is_hidden() else "no error summary"
                    raise AssertionError(f"Spiro Agnew job finished with status {status!r}: {failure}")
                project = page.locator(".project-tab").first
                project.wait_for(timeout=60_000)
                assert "spiro agnew" in project.inner_text().lower()
                if reuse_root is None:
                    project.click()

                tracks = page.locator("#playback-panel audio")
                tracks.nth(5).wait_for(state="attached", timeout=60_000)
                try:
                    page.wait_for_function(
                        "Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                        "&& Array.from(document.querySelectorAll('#playback-panel audio')).every("
                        "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                        timeout=60_000,
                    )
                except Exception:
                    print("Media element diagnostics:", page.locator("#playback-panel audio").evaluate_all(
                        "els => els.map(audio => ({src: audio.currentSrc, duration: audio.duration, "
                        "readyState: audio.readyState, networkState: audio.networkState, "
                        "error: audio.error && {code: audio.error.code, message: audio.error.message}}))"
                    ))
                    print("Asset responses:", asset_responses)
                    print("Asset request failures:", asset_request_failures)
                    raise
                assert [tracks.nth(index).get_attribute("data-stem") for index in range(6)] == list(EXPECTED_STEMS)
                assert page.locator(".mixer-row").count() == 6
                assert not page.locator("#project-empty").is_visible()

                page.locator('[data-action="toggle"]').click()
                page.wait_for_function("Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => !audio.paused)")
                position = page.locator("#playback-panel audio").first.evaluate("audio => audio.currentTime")
                page.wait_for_function(
                    "position => document.querySelector('#playback-panel audio').currentTime > position",
                    arg=position,
                )

                # Real mouse click at the midpoint of the waveform column (the position layer).
                seek = page.locator('[data-control="seek"]')
                box = seek.bounding_box()
                assert box is not None
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_function(
                    "() => { const audio = document.querySelector('#playback-panel audio'); "
                    "return !audio.paused && Math.abs(audio.currentTime / audio.duration - 0.5) < 0.08; }"
                )
                # Clicking a waveform seeks to the same relative point.
                wave = page.locator(".mixer-waveform").first.bounding_box()
                page.mouse.click(wave["x"] + wave["width"] * 0.25, wave["y"] + wave["height"] / 2)
                page.wait_for_function(
                    "() => { const audio = document.querySelector('#playback-panel audio'); "
                    "return !audio.paused && Math.abs(audio.currentTime / audio.duration - 0.25) < 0.08; }"
                )

                gains = page.locator('[data-control="gain"]')
                mutes = page.locator('[data-control="mute"]')
                solos = page.locator('[data-control="solo"]')
                assert gains.count() == mutes.count() == solos.count() == 6
                for index in range(6):
                    gain = gains.nth(index)
                    # Volume is mouse-only: click the ends of the slider.
                    gain_box = gain.bounding_box()
                    page.mouse.click(gain_box["x"] + 1, gain_box["y"] + gain_box["height"] / 2)
                    page.wait_for_function(
                        "index => Number(document.querySelectorAll('#playback-panel audio')[index].dataset.effectiveGain) === 0",
                        arg=index,
                    )
                    assert gain.input_value() == "0"
                    page.mouse.click(gain_box["x"] + gain_box["width"] - 1, gain_box["y"] + gain_box["height"] / 2)
                    page.wait_for_function(
                        "index => Number(document.querySelectorAll('#playback-panel audio')[index].dataset.effectiveGain) === 1.5",
                        arg=index,
                    )
                    assert gain.input_value() == "150"
                    mute = mutes.nth(index)
                    mute.click()
                    page.wait_for_function(
                        "index => Number(document.querySelectorAll('#playback-panel audio')[index].dataset.effectiveGain) === 0",
                        arg=index,
                    )
                    mute.click()
                    page.wait_for_function(
                        "index => document.querySelectorAll('[data-control=\"mute\"]')[index].getAttribute('aria-pressed') === 'false'",
                        arg=index,
                    )
                    page.wait_for_function(
                        "index => Number(document.querySelectorAll('#playback-panel audio')[index].dataset.effectiveGain) > 0",
                        arg=index,
                    )
                    assert gain.input_value() == "150"
                solos.nth(0).click()
                solos.nth(1).click()
                print("Solo controls after selection:", solos.evaluate_all(
                    "buttons => buttons.map(button => button.getAttribute('aria-pressed'))"
                ))
                print("Effective gains after solo selection:", page.locator("#playback-panel audio").evaluate_all(
                    "tracks => tracks.map(audio => audio.dataset.effectiveGain)"
                ))
                page.wait_for_function(
                    "Array.from(document.querySelectorAll('#playback-panel audio')).slice(0, 2).every(audio => Number(audio.dataset.effectiveGain) > 0)",
                    timeout=5_000,
                )
                page.wait_for_function(
                    "Array.from(document.querySelectorAll('#playback-panel audio')).slice(2).every(audio => Number(audio.dataset.effectiveGain) === 0)"
                )
                solos.nth(0).click()
                solos.nth(1).click()
                page.wait_for_function(
                    "Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => Number(audio.dataset.effectiveGain) === 1.5)"
                )
                page.locator('[data-action="toggle"]').click()
                try:
                    page.wait_for_function(
                        "Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => audio.paused)",
                        timeout=5_000,
                    )
                except Exception:
                    print("Playback state after pause click:", page.locator("#playback-panel audio").evaluate_all(
                        "els => els.map(audio => ({paused: audio.paused, currentTime: audio.currentTime, duration: audio.duration}))"
                    ))
                    print("Transport button after pause click:", page.locator('[data-action="toggle"]').evaluate(
                        "button => ({text: button.textContent, label: button.getAttribute('aria-label')})"
                    ))
                    raise

                # Reopen the saved project from the library and play it again.
                page.locator(".project-tab").first.click()
                page.wait_for_function(
                    "Array.from(document.querySelectorAll('#playback-panel audio')).length === 6 "
                    "&& Array.from(document.querySelectorAll('#playback-panel audio')).every("
                    "audio => Number.isFinite(audio.duration) && audio.duration > 0)",
                    timeout=60_000,
                )
                page.locator('[data-action="toggle"]').click()
                page.wait_for_function("Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => !audio.paused)")
                page.locator('[data-action="toggle"]').click()
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
        stored = list((media_root / "Stems").glob(f"*/{stem}.flac"))
        assert stored, f"{stem}.flac was not stored"
        assert stored[0].read_bytes()[:4] == b"fLaC"
    assert not any((media_root / "_demucs_temp").rglob("*.wav")), "float temporaries were left behind"
