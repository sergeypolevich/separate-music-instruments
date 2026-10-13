# Development Status

## Current checkpoint

- **Current stage:** 3 — Baseline regression coverage (in progress; startup dependency bootstrap added as a small developer-experience follow-up).
- **Baseline commit:** `84b08b1` (`Initial commit: audio downloader and stem separation`), branch `main`. The unpublished initial commit was amended with the notebook cleanup.
- **Working tree at checkpoint:** contains uncommitted notebook removal and dependency-bootstrap changes.
- **Remote:** `origin` points to the public `sergeypolevich/separate-music-instruments` GitHub repository; `main` was pushed and verified.
- **Source of truth:** `download_tracks.py` contains the runnable current script; `tests/test_baseline.py` tests that module. The obsolete `main.ipynb` duplicated older code and setup cells and has been removed at the user's request.
- **Checks:** after the launcher and its unit tests were added, `pytest -q` passed: 12 passed, 1 opt-in live E2E skipped. `git diff --check` passed. `.gitignore` excludes `.venv/`, generated media, and local environment files.
- **Stage 2 preparation:** `skills/audio-stems-development.md`, `DEVELOPMENT_PLAN.md`, and this checkpoint were committed and referenced by `AGENTS.md`; the offline suite was run after those files were added. Stage 2 is recorded as preparatory and does not authorize advancing past the stage 1 publication gate.
- **Next action:** review the complete working diff and continue stage 3 regression coverage. Stage 1 publication is complete.

## Completed stages

Stage 1 publication is complete. The notebook deletion and launcher changes are not committed.
