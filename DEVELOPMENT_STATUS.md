# Development Status

## Current checkpoint

- **Current stage:** 6 — Storage adapter (next; stage 5 contracts completed).
- **Baseline commit:** `84b08b1` (`Initial commit: audio downloader and stem separation`), branch `main`. The unpublished initial commit was amended with the notebook cleanup.
- **Working tree at checkpoint:** stage 5 contracts and checkpoint are uncommitted; stage 4 commit `3cb92c0` is pushed to `origin/main`.
- **Remote:** `origin` points to the public `sergeypolevich/separate-music-instruments` GitHub repository; `main` was pushed and verified.
- **Source of truth:** `download_tracks.py` contains the runnable current script; `tests/test_baseline.py` tests that module. The obsolete `main.ipynb` duplicated older code and setup cells and has been removed at the user's request.
- **Checks:** stage 3 targeted `pytest -q tests/test_baseline.py` passed (11 passed). Full `pytest -q` passed (14 passed, 1 opt-in live E2E skipped). `git diff --check` passed. `.gitignore` excludes `.venv/`, generated media, and local environment files.
- **Stage 2 preparation:** `skills/audio-stems-development.md`, `DEVELOPMENT_PLAN.md`, and this checkpoint were committed and referenced by `AGENTS.md`; the offline suite was run after those files were added. Stage 2 is recorded as preparatory and does not authorize advancing past the stage 1 publication gate.
- **Stage 3 result:** added tests proving incomplete stem output retains temporary files and can be repaired on a later run, and a Demucs subprocess failure for one track does not prevent the next track from completing. Existing tests cover batch download-before-separation, a failed download, complete-set skip, and temp cleanup after all six stems exist.
- **Stage 3 boundaries:** fake subprocess recreates Demucs' expected output layout; no network, model execution, audio data, or dependency installation in tests. Output layout, model, retry policy, and phase ordering are unchanged.
- **Stage 4 result:** added standard-library settings loading/validation, `.env` support and placeholders, rotating JSONL plus readable UTC console logs, credential-pattern redaction, and lifecycle events from the CLI. Storage/temp roots, retries, delays, model name/device, and log policy now read from settings with baseline-compatible defaults.
- **Stage 4 checks:** targeted settings/logging/baseline tests passed (22 passed); full `pytest -q` passed (25 passed, 1 opt-in live test skipped); `git diff --check` passed. Tests cover defaults, `.env` and process overrides, invalid config, log schema/ClientID, console and JSON redaction, exception data, rotation, and configured retry delays.
- **Stage 4 boundaries:** `.env` is parsed as data and never shell-evaluated. No search query or output path is placed in structured event messages. No new runtime dependency was introduced. The actual YouTube/Demucs workflow remains opt-in and was not run.
- **Stage 5 result:** added provider-independent typed models, protocols for source normalization, downloader, separator, storage, jobs, and repository, plus categorized adapter errors with retryability.
- **Stage 5 checks:** targeted `pytest -q tests/test_contracts.py` passed (4 passed); full `pytest -q` passed (29 passed, 1 opt-in live test skipped); `git diff --check` passed. Fake adapters satisfy runtime-checkable protocols; AST test confirms contracts do not import `pytubefix` or Demucs.
- **Stage 5 boundaries:** contracts only; no implementation was moved and baseline paths, format policy, model, retries, and phase ordering are unchanged. The compatibility CLI remains intact.
- **Next action:** run final diff check, commit and push stage 5, then implement local storage path safety in stage 6.

## Completed stages

Stage 1 publication, stage 3 regression coverage, and stage 4 settings/logging are complete. Stage 4 defaults preserve the standalone CLI's current paths and processing settings.
