# Baseline test notes

These tests are designed to be copied into the root of the existing project, next to `download_tracks.py`. They do not include or replace the working application script.

## Offline regression suite

`tests/test_baseline.py` mocks YouTube and Demucs. It covers:

- Windows-safe filename handling;
- WebM audio preference and fallback format;
- reusing a non-empty existing audio file;
- three download attempts on repeated failure;
- downloading every batch item before any separation begins;
- flat six-stem output layout and successful temporary-output cleanup;
- skipping a track whose six expected stem files already exist.

## Live end-to-end test

`tests/test_e2e_rock_song.py` is opt-in. It performs a real YouTube search for `Band - Rock Song (Official Music Video)`, verifies that the first result is the official video ID `<video-id>`, downloads the audio, runs the real `htdemucs_6s` CPU model, and checks for six non-empty WAV files in the flat per-track output directory.

The E2E test is intentionally skipped during ordinary `pytest` runs. It needs network access, the current `pytubefix` package, `demucs`, model-weight download on first use, and enough CPU time/disk space. It fails rather than downloading a different video if search returns a different first result.

## Run on Windows PowerShell

Copy `tests/`, `pytest.ini`, `requirements-test.txt`, and this note into the existing project root. Keep the existing `download_tracks.py` unchanged.

```powershell
py -m pip install -r requirements-test.txt
py -m pytest
```

Enable the live end-to-end test explicitly:

```powershell
$env:RUN_LIVE_E2E = "1"
py -m pytest -m e2e -s
```

Unset the flag afterward if desired:

```powershell
Remove-Item Env:RUN_LIVE_E2E
```

## Scope and limitations

The runnable project script is `download_tracks.py`; `tests/test_baseline.py` imports and tests it directly. The live end-to-end test remains opt-in and requires network access and Demucs.
