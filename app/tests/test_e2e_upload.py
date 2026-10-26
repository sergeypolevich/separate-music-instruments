"""Live E2E for the user's own file: upload through the file picker, real Demucs on CPU, playback.

Runs with the other live test before each commit:
    $env:RUN_LIVE_E2E = "1"; py -m pytest -q -m e2e -s

It needs no internet for the audio: the file is generated here (tones and noise bursts), so no recording
of anyone's music is involved. App data is isolated under pytest's temporary directory.
"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
import pytest
import soundfile

from tests.test_e2e_live import EXPECTED_STEMS, ROOT, _free_local_port, _launch_visible_browser, _wait_for_health


pytestmark = pytest.mark.e2e


def _synthetic_song(path, seconds=10, rate=44100):
    """A bass line, a chord, a vibrato «voice» and a noise burst on every beat at 120 BPM."""
    t = np.arange(seconds * rate) / rate
    bass = 0.25 * np.sin(2 * np.pi * 55 * (1 + (t // 2 % 2) * 0.5) * t)
    chord = 0.08 * sum(np.sin(2 * np.pi * f * t) for f in (220, 277.2, 329.6))
    voice = 0.15 * np.sin(2 * np.pi * 440 * t + 3 * np.sin(2 * np.pi * 5 * t)) * (t % 4 < 3)
    beat = np.exp(-(t % 0.5) * 40) * np.random.default_rng(1).uniform(-0.3, 0.3, t.size)
    mix = bass + chord + voice + beat
    soundfile.write(path, np.stack([mix, np.roll(mix, 50)], axis=1).astype(np.float32), rate, subtype="PCM_16")


def test_live_upload_of_your_own_file_is_separated_and_plays(tmp_path):
    if os.getenv("RUN_LIVE_E2E") != "1":
        pytest.skip("Set RUN_LIVE_E2E=1 to run the live upload + Demucs browser test")
    from playwright.sync_api import sync_playwright

    song = tmp_path / "Synthetic test.wav"
    _synthetic_song(song)
    port = _free_local_port()
    base_url = f"http://127.0.0.1:{port}"
    media_root = tmp_path / "Music"
    stop_file = tmp_path / "stop-supervisor"
    log_path = tmp_path / "desktop-e2e.log"
    environment = os.environ.copy()
    environment.update({
        "AUDIO_STEMS_API_PORT": str(port),
        "AUDIO_STEMS_DATABASE_URL": "sqlite:///" + (tmp_path / "audio_stems.db").as_posix(),
        "AUDIO_STEMS_STORAGE_ROOT": str(media_root),
        "AUDIO_STEMS_TEMP_ROOT": str(media_root / "_demucs_temp"),
        "AUDIO_STEMS_LOG_FILE": str(tmp_path / "logs" / "app.jsonl"),
        "AUDIO_STEMS_OPEN_BROWSER": "0",
        "AUDIO_STEMS_STOP_FILE": str(stop_file),
    })

    with log_path.open("w", encoding="utf-8") as app_log:
        process = subprocess.Popen([sys.executable, "-m", "audio_stems.desktop"], cwd=ROOT, env=environment,
                                   stdout=app_log, stderr=subprocess.STDOUT)
        page = None
        try:
            _wait_for_health(f"{base_url}/api/v1/health", process)
            with sync_playwright() as playwright:
                browser = _launch_visible_browser(playwright)
                page = browser.new_page(no_viewport=True)
                page.set_default_timeout(30_000)
                page_errors = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)

                page.locator("#search-trigger").click()
                page.locator("#file-input").set_input_files(str(song))
                page.wait_for_function("document.querySelector('#search-overlay').hidden", timeout=120_000)
                page.wait_for_function(
                    "() => { const tab = document.querySelector('.project-tab'); "
                    "return tab && ['completed', 'failed'].includes(tab.dataset.status); }",
                    timeout=30 * 60 * 1000,
                )
                tab = page.locator(".project-tab").first
                if tab.get_attribute("data-status") != "completed":
                    raise AssertionError("Upload job failed: " + page.locator("#detail-error").inner_text())
                assert "synthetic test" in tab.inner_text().lower()
                tab.click()
                page.wait_for_function(
                    "Array.from(document.querySelectorAll('#playback-panel audio')).length === 4 "
                    "&& Array.from(document.querySelectorAll('#playback-panel audio')).every("
                    "audio => Number.isFinite(audio.duration) && audio.duration > 9)",
                    timeout=60_000,
                )
                tracks = page.locator("#playback-panel audio")
                assert [tracks.nth(i).get_attribute("data-stem") for i in range(4)] == list(EXPECTED_STEMS)
                page.locator('[data-action="toggle"]').click()
                page.wait_for_function("Array.from(document.querySelectorAll('#playback-panel audio')).every(audio => !audio.paused)")
                page.locator('[data-action="toggle"]').click()
                assert not page_errors, f"Browser page errors: {page_errors}"
        except Exception:
            app_log.flush()
            print("\n--- Audio Stems upload E2E service log tail ---\n" + log_path.read_text(encoding="utf-8", errors="replace")[-8000:])
            if page is not None:
                try:
                    page.screenshot(path=str(tmp_path / "browser-failure.png"))
                    print("\n--- Browser screenshot ---\n" + str(tmp_path / "browser-failure.png"))
                except Exception:
                    pass
            raise
        finally:
            stop_file.touch()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)

    assert process.returncode == 0
    assert (media_root / "Synthetic test.wav").read_bytes() == song.read_bytes()
    for stem in EXPECTED_STEMS:
        assert list((media_root / "Stems").glob(f"Synthetic test*/{stem}.flac")), f"{stem}.flac was not stored"
    assert not any((media_root / "_demucs_temp" / "uploads").iterdir()), "the upload was left in the temporary folder"
