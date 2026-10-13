# Development Status

## Current checkpoint

- **Current stage:** 8 — SQLite models (next; stage 7 input normalization completed).
- **Baseline commit:** `84b08b1` (`Initial commit: audio downloader and stem separation`), branch `main`. The unpublished initial commit was amended with the notebook cleanup.
- **Working tree at checkpoint:** stage 7 source-normalization changes are uncommitted; stage 6 commit `0383d34` is pushed to `origin/main`.
- **Remote:** `origin` points to the public `sergeypolevich/separate-music-instruments` GitHub repository; `main` was pushed and verified.
- **Source of truth:** `download_tracks.py` contains the runnable current script; `tests/test_baseline.py` tests that module. The obsolete `main.ipynb` duplicated older code and setup cells and has been removed at the user's request.
- **Latest checks:** stage 7 targeted source/baseline tests passed (28 passed); full `pytest -q` passed (59 passed, 1 opt-in live E2E skipped); `git diff --check` passed. `.gitignore` excludes `.venv/`, generated media, and local environment files.
- **Stage 2 preparation:** `skills/audio-stems-development.md`, `DEVELOPMENT_PLAN.md`, and this checkpoint were committed and referenced by `AGENTS.md`; the offline suite was run after those files were added. Stage 2 is recorded as preparatory and does not authorize advancing past the stage 1 publication gate.
- **Stage 3 result:** added tests proving incomplete stem output retains temporary files and can be repaired on a later run, and a Demucs subprocess failure for one track does not prevent the next track from completing. Existing tests cover batch download-before-separation, a failed download, complete-set skip, and temp cleanup after all six stems exist.
- **Stage 3 boundaries:** fake subprocess recreates Demucs' expected output layout; no network, model execution, audio data, or dependency installation in tests. Output layout, model, retry policy, and phase ordering are unchanged.
- **Stage 4 result:** added standard-library settings loading/validation, `.env` support and placeholders, rotating JSONL plus readable UTC console logs, credential-pattern redaction, and lifecycle events from the CLI. Storage/temp roots, retries, delays, model name/device, and log policy now read from settings with baseline-compatible defaults.
- **Stage 4 checks:** targeted settings/logging/baseline tests passed (22 passed); full `pytest -q` passed (25 passed, 1 opt-in live test skipped); `git diff --check` passed. Tests cover defaults, `.env` and process overrides, invalid config, log schema/ClientID, console and JSON redaction, exception data, rotation, and configured retry delays.
- **Stage 4 boundaries:** `.env` is parsed as data and never shell-evaluated. No search query or output path is placed in structured event messages. No new runtime dependency was introduced. The actual YouTube/Demucs workflow remains opt-in and was not run.
- **Stage 5 result:** added provider-independent typed models, protocols for source normalization, downloader, separator, storage, jobs, and repository, plus categorized adapter errors with retryability.
- **Stage 5 checks:** targeted `pytest -q tests/test_contracts.py` passed (4 passed); full `pytest -q` passed (29 passed, 1 opt-in live test skipped); `git diff --check` passed. Fake adapters satisfy runtime-checkable protocols; AST test confirms contracts do not import `pytubefix` or Demucs.
- **Stage 5 boundaries:** contracts only; no implementation was moved and baseline paths, format policy, model, retries, and phase ordering are unchanged. The compatibility CLI remains intact.
- **Stage 6 result:** added `LocalMediaStorage` behind the `MediaStorage` protocol. Audio and flat stem paths, temporary Demucs directories, complete-set checks, cleanup, storage-key resolution, and asset copy operations now go through the adapter. A small local identity index preserves readable names and gives colliding source IDs distinct stable paths.
- **Stage 6 checks:** targeted storage/contracts/baseline tests passed (30 passed); full `pytest -q` passed (43 passed, 1 opt-in live test skipped); `git diff --check` passed. Coverage includes Windows-invalid/reserved names, Windows and POSIX traversal, colliding titles, stable reuse after adapter restart, relative keys, and flat stems.
- **Stage 6 boundaries:** default non-colliding paths and output layout are preserved; legacy outputs without index entries retain the original name; cleanup remains conditional on all six expected stem files. The existing CLI is still the composition entry point.
- **Stage 7 result:** added `YouTubeSourceAdapter` for query/direct URL normalization and wired the CLI so direct video links bypass search and are passed unchanged to `YouTube`. URL validation requires HTTPS, an allowlisted exact host, and a supported video path; malformed or URL-like unsupported input raises a categorized validation error.
- **Stage 7 checks:** source/CLI targeted tests passed (28 passed); full `pytest -q` passed (59 passed, 1 opt-in live test skipped). Tests prove first-result search compatibility, direct URL exactness/no search, allowed YouTube URL forms, and rejection of bad schemes/hosts/credentials/ports/paths.
- **Stage 7 boundaries:** tests use fake source/download adapters only; no network. WebM preference and fallback remain unchanged.
- **Next action:** run final diff check, commit and push stage 7, then add SQLite metadata models and persistence tests in stage 8.

## Completed stages

Stage 1 publication, stage 3 regression coverage, and stage 4 settings/logging are complete. Stage 4 defaults preserve the standalone CLI's current paths and processing settings.
