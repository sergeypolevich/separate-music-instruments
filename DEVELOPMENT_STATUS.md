# Development Status

## Current checkpoint

- **Current stage:** 4 — Settings and structured logs (next; stage 3 regression coverage completed).
- **Baseline commit:** `84b08b1` (`Initial commit: audio downloader and stem separation`), branch `main`. The unpublished initial commit was amended with the notebook cleanup.
- **Working tree at checkpoint:** stage 3 test and status updates are uncommitted; previous commit `cd44a4d` (`Add automatic runtime dependency setup`) is pushed to `origin/main`.
- **Remote:** `origin` points to the public `sergeypolevich/separate-music-instruments` GitHub repository; `main` was pushed and verified.
- **Source of truth:** `download_tracks.py` contains the runnable current script; `tests/test_baseline.py` tests that module. The obsolete `main.ipynb` duplicated older code and setup cells and has been removed at the user's request.
- **Checks:** stage 3 targeted `pytest -q tests/test_baseline.py` passed (11 passed). Full `pytest -q` passed (14 passed, 1 opt-in live E2E skipped). `git diff --check` passed. `.gitignore` excludes `.venv/`, generated media, and local environment files.
- **Stage 2 preparation:** `skills/audio-stems-development.md`, `DEVELOPMENT_PLAN.md`, and this checkpoint were committed and referenced by `AGENTS.md`; the offline suite was run after those files were added. Stage 2 is recorded as preparatory and does not authorize advancing past the stage 1 publication gate.
- **Stage 3 result:** added tests proving incomplete stem output retains temporary files and can be repaired on a later run, and a Demucs subprocess failure for one track does not prevent the next track from completing. Existing tests cover batch download-before-separation, a failed download, complete-set skip, and temp cleanup after all six stems exist.
- **Stage 3 boundaries:** fake subprocess recreates Demucs' expected output layout; no network, model execution, audio data, or dependency installation in tests. Output layout, model, retry policy, and phase ordering are unchanged.
- **Next action:** commit and push the passing stage 3 regression tests, then start stage 4 configuration and structured logging.

## Completed stages

Stage 1 publication and stage 3 regression coverage are complete. Stage 3 changes add regression tests only; no production behavior was changed.
