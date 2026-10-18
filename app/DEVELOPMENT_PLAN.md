# Development Plan

Work one stage at a time. Preserve the working standalone script while adding tests and modular boundaries. Use a local web UI, API, separate worker, SQLite, local filesystem storage, and Demucs as the first model backend. Read `AGENTS.md` and `skills/audio-stems-development.md` before every stage.

## Stages

1. **Verify and publish v1:** inspect the initial commit and ignore rules; remove saved notebook outputs containing local paths while preserving code; run baseline offline checks, `git diff --check`, media/secret and notebook-output checks; publish the clean snapshot to the intended public repository only after all checks pass and a destination is configured. Completed: v1 is published. The obsolete notebook was later removed at the user's request because the maintained script and tests are in Python files.
2. **Workflow checkpoints:** add the project workflow skill, this plan, and `DEVELOPMENT_STATUS.md`; check referenced files and the offline suite.
3. **Baseline regression coverage:** add failure isolation, incomplete-output recovery, and safe temporary cleanup tests; verify retries, reuse, phase order, and flat six-stem layout.
4. **Settings and structured logs:** central validated settings, `.env.example`, console and rotating JSONL logs; test defaults, overrides, invalid settings, fields, rotation, and secret redaction.
5. **Adapter contracts:** typed source, downloader, separator, storage, and job-service contracts; preserve a compatibility entry point; test fake adapters and dependency boundaries.
6. **Storage adapter:** retain root audio and `Stems/<safe title>/`; relative keys, collision handling, and traversal protection; test Windows names, collisions, and flat output.
7. **Input normalization and direct URLs:** distinguish query from exact video URL and validate configured hosts; test first-result compatibility and no substitution/search for direct URLs.
8. **SQLite models:** projects/jobs/assets/events with media metadata and relative keys; test persistence across connections and no media blobs in the database.
9. **Job states and orchestration:** documented transitions, partial success, safe errors, transient retries, and download-all-before-separate ordering; test invalid transitions and failure isolation.
10. **Model selection:** expose Demucs capabilities and persist model/options; test validation and option round trips without running Demucs.
11. **Separate worker:** run long jobs outside the API and define bounded restart recovery; test fake-worker failures, recovery, and batch ordering.
12. **Minimal API:** submit jobs and read status/events/projects on localhost; test schemas, stable errors, URL/path validation, and fast asynchronous responses.
13. **Submission and progress UI:** submit a query or URL and show persisted state/errors; test normal and failed job states.
14. **Project library:** reopen saved projects and outputs; test API restart, missing files, and partial metadata.
15. **Asset streaming and waveform data:** stream authorized assets and provide compact waveform levels; test missing assets, byte ranges, and track mapping.
16. **Synchronized playback and seek:** six aligned tracks with shared transport/seek; test position and transport behavior.
17. **Mixer controls:** session-only gain, mute, and solo; test independent gain, solo combinations, reset, and shared position.
18. **Project deletion:** remove metadata and owned files safely, including active jobs and retryable cleanup; test idempotency, filesystem errors, traversal, and neighbor preservation.
19. **Controlled updates:** inspect releases and update pinned app/dependencies only after confirmation; test mocked responses and failure recovery without updating the real environment.
20. **Documentation and local v2 verification:** PowerShell setup, API/worker launch, backup, maintenance; run full offline suite, diff checks, and manual core UI/API checks. Live smoke remains opt-in.
21. **One-click local launch and browser E2E:** add a Windows button that bootstraps Python and dependencies, supervises the API and worker, and opens the browser; serve page assets with relative sibling URLs; add an opt-in full real YouTube/Demucs workflow controlled with visible installed Edge/Chrome via Playwright, and require it before each commit.
22. **Version 3 interface redesign:** make the project workspace the main screen; keep a compact search trigger in the current content width in the header and open a full-screen submission panel; show projects as horizontally scrolling tabs between the header and workspace; add Settings and Russian Help screens; redesign transport and DAW mixer rows; support 0–150% track gain with a 95–105% snap band; confirm deletion in a styled dialog; add responsive Playwright checks; finally detect available CPU/CUDA/MPS devices, expose a separate API endpoint without changing `/api/v1/models`, and reject unsupported devices.
23. **Version 4 simplification and performance:** document user-facing functionality (`docs/FUNCTIONALITY.md`); make project opening fast (numpy waveform peaks with a cache, Starlette range responses); keep the UI responsive during playback (release old stems on project switch, load stems from the other loopback name, bounded media ranges); remove the legacy CLI, device selection and unused endpoints/protocols; move the code into the `audio_stems/` package; update documentation.
24. **Version 5 project interface rework:** remove the empty-state hint over an open project, the refresh button, the repeated title/model lines, Help and the project files list; stretch tabs to the project card; put a custom timeline in the waveform column with a playhead line across all waveforms and click/drag-to-seek; replace text glyph icons with SVG icons verified by geometry tests and screenshots.
25. **Version 6 waveform rendering and storage:** one 1 px playhead from the timeline thumb across all stems; a fixed 1024-peak waveform stored in SQLite per stem file version; a solid SVG envelope that scales to any width instead of canvas bars.
26. **Version 8 states, hotkeys, tabs, themes, fader, timer:** solid yellow/red pressed S/M and 20 % waveforms for S/M-silenced stems; global hotkeys with keyboard stem selection; wheel/drag tab scrolling with edge fades; colour tokens with a light theme and accent hue in Settings; borderless fader with a 100 % mark; timer at the right of the title.
27. **Version 9 project cleanup:** close test gaps and record coverage/performance baselines first; move everything except `Start.bat` (renamed), `README.md`, `AGENTS.md`, `CLAUDE.md` and `.claude/` into `app/`; remove single-implementation protocols, the unused idempotency key and Demucs parameters, placeholder settings and dead code — with identical UI and no performance regression.

## Stage gate

Each stage needs its stated tests/checks plus the full offline suite to pass, an updated status checkpoint, and a reviewed diff before commit or transition. If a check fails or cannot run, report the blocker and keep the stage open.
