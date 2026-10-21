# Audio Stems — Project Specification and Coding-Agent Instructions

Before starting each implementation stage, read [`app/skills/audio-stems-development.md`](app/skills/audio-stems-development.md), [`app/DEVELOPMENT_PLAN.md`](app/DEVELOPMENT_PLAN.md), and the current checkpoint in [`app/DEVELOPMENT_STATUS.md`](app/DEVELOPMENT_STATUS.md).

Repository layout: the root holds only what a user or an agent needs first — `Start.bat` and `Start.command` (the Windows and macOS launchers), the user guides `README.md`, `README.ru.md` and `README.de.md`, `AGENTS.md`, `CLAUDE.md` and `.claude/`. Everything else (code, tests, web UI, docs, requirements, the `.venv`) lives in `app/`; run Python and test commands from `app/`. User data stays outside the code at `~/Audio Stems` (`YouTube Audio/`, `audio_stems.db`, `logs/`).

## 1. Purpose

Build a Windows-first application that accepts a YouTube search query or a direct video URL, downloads the available audio, separates it into instrument/vocal stems, and stores the source audio and outputs. It should eventually support a web UI and a Chrome extension, with authentication, job history, logs, and replaceable implementations for downloading, separation, and storage.

This file is the shared project specification. Read it before making changes. Implement the project incrementally; do not attempt to build every future feature in one change.

## 2. Non-negotiable engineering rules

1. **Preserve the working behavior before refactoring.** The current application — described from the user's perspective in [`app/docs/FUNCTIONALITY.md`](app/docs/FUNCTIONALITY.md) and covered by the test suite — is the behavioral baseline. Add tests around behavior before moving logic. Do not rewrite large parts of working code in one step.
2. Make small, reviewable changes. For every change, explain the intent, run the relevant tests, and report what passed and what could not be verified.
3. Do not silently change output paths, output naming, processing order, model settings, format-selection policy, retry behavior, or skip behavior. Change these only when explicitly requested and update tests.
4. Keep downloader, separator, source/input normalization, job orchestration, authentication, metadata, logging, and storage in separate modules with narrow, documented methods. Modules use each other's concrete classes directly; introduce an abstract interface (Protocol) only when a second implementation actually exists. Tests replace slow or external pieces with small duck-typed fakes.
5. Start with a **modular monolith and a separate worker process**, not a collection of microservices. Keep boundaries clear so modules can be extracted later if justified by actual scale or deployment needs.
6. Unit tests must not require internet access, real YouTube downloads, GPU access, or execution of a real ML model. Use fakes/mocks for those dependencies. Optional live smoke tests must be explicitly marked and disabled by default.
7. Never claim tests passed unless they were actually run. Do not hide failures or weaken tests merely to make a build green.
8. Do not commit, push, delete user data, overwrite unrelated files, or change secrets/configuration unless explicitly asked. Preserve existing user changes.
9. Respect YouTube's terms, copyright, and access restrictions. Support only content the user is authorized to download. Do not implement DRM, private-content, authentication, or other access-control bypasses.

## 3. Current working baseline — preserve it

The application's current behavior is considered working and is captured in tests. (The original standalone script `download_tracks.py` was retired in version 4; its behaviors are tested at the service level.)

- Primary operating system: Windows. Use `pathlib.Path`; avoid hard-coded Windows path separators.
- Current output root: `Path.home() / "Audio Stems" / "YouTube Audio"`.
- Audio files are saved directly under the output root, using a sanitized YouTube video title plus the selected stream extension.
- Stem files are saved as 16-bit FLAC in a flat per-track directory: `Stems/<safe YouTube title>/vocals.flac`, `drums.flac`, `bass.flac`, `guitar.flac`, `piano.flac`, and `other.flac` for the original model `htdemucs_6s`, and `Stems/<safe YouTube title> (<model>)/` for any other model (four-stem models write `vocals`, `drums`, `bass`, `other`). All stems of one set share one gain so nothing clips and the balance is unchanged. Sets stored as WAV before version 10 stay valid. Do **not** create one subdirectory per stem.
- A batch is processed in two phases: (1) attempt to download all requested tracks; (2) after the download phase ends, run separation for each successfully available audio file. A failed item must not stop processing the remaining items.
- The current downloader uses `pytubefix`: for a search query it selects the first search result; it prefers an audio-only `audio/webm` stream ordered by descending bitrate, then falls back to another available audio-only stream.
- Download attempts are retried up to three times with delays between attempts. If a target audio file already exists and passes container validation, it is reused instead of downloading again; an invalid one is quarantined and downloaded again.
- Separation models: Demucs `hdemucs_mmi` with shifts 2 (default, four stems) and BS-RoFormer SW through audio-separator (overlap 1, six stems, also used by «Улучшить качество»), each run in its own process; users pick one in Settings. `htdemucs_6s`, the default before version 13, is no longer offered but stays registered for earlier projects. The choice comes from the listening studies in https://github.com/sergeypolevich/separate-music-instruments-research. The device comes from `AUDIO_STEMS_MODEL_DEVICE`; the default `auto` picks CUDA, then Apple MPS, then CPU. Users do not choose the device in the UI.
- Temporary separator outputs are cleaned up only after all expected stems of the model exist. Existing complete stem sets of the same model are skipped.
- Filenames are sanitized for Windows-invalid characters and reserved device names.
- Current hard-coded example queries are test fixtures/examples only. In the product, users must be able to supply inputs through a client; the production workflow must not require editing the Python source to change the track list.

Baseline behavior is not a reason to preserve known bugs. If a reliability or security issue is discovered, first add a failing test or a reproducible case, explain the behavioral change, and implement the smallest safe fix.

## 4. Target architecture

Use a modular monolith with independently testable components. The API and the long-running worker must be separate runtime processes so downloads and ML inference do not block HTTP requests.

```text
Clients
  ├── Web UI
  └── Chrome extension
          │
          ▼
      FastAPI API
          ├── authentication / authorization
          ├── input validation and source adapters
          ├── job and batch API
          └── metadata / job-event persistence
                    │
                    ▼
              Persistent job queue
                    │
                    ▼
                  Worker
                    ├── batch orchestrator
                    ├── downloader adapter
                    ├── stem-separator adapter
                    └── storage adapter

Shared infrastructure: database, local/object storage, structured logs
```

### Module responsibilities

Code lives in the `app/audio_stems/` package; the responsibilities below map to these modules: `models` (shared data models and the `sources` input normalizer), `youtube` (downloader), `separator` (Demucs), `pipeline` (jobs orchestration and the worker loop), `storage`, `db` (metadata and `migrations/`), `config` (settings and observability/logging), `api` (routes and waveform peaks), `updates`, and `desktop` (local supervisor). `run.py` at the project root is the bootstrap launcher. Auth is not implemented yet (single local owner).

- `sources`: normalizes search-query and direct-URL submissions. It must not download audio itself.
- `downloader`: searches source candidates and downloads audio. It must not know about web pages, browser extensions, user sessions, or stem models.
- `separator`: accepts an input audio path plus separator/model settings and returns a structured result. It must not know where the audio came from.
- `jobs`: owns job/batch state, phase ordering, retries, idempotency, recovery, and error handling.
- `storage`: saves and resolves audio/stem objects. Business logic must not construct storage paths directly.
- `metadata`: database models and repositories for clients/users, sources, batches/jobs, media assets, and job events.
- `auth`: identity, sessions/tokens, and authorization policy. Authentication data must not be implemented inside downloader/separator modules.
- `observability`: logging setup, correlation context, and stable event definitions.
- `api`: HTTP routes and request/response schemas; it should be thin and delegate to application services.
- `web`: user interface. It talks to the API and must not reimplement download/separation logic.

Suggested initial stack: Python, FastAPI, Pydantic, SQLAlchemy, Alembic, pytest, SQLite for local development, and a local-filesystem storage adapter. Keep the database and storage interfaces replaceable so PostgreSQL and S3-compatible storage can be added later. Confirm dependency compatibility in the actual environment before pinning versions. Use a database-backed queue initially; add Redis/Celery or another queue only when a concrete requirement justifies it.

## 5. Input sources and client behavior

The application must support two input modes:

1. **Search query** — for example, `Portishead — Glory Box`. The downloader returns candidates and a selected source. Preserve `first_result` as the compatibility default. The architecture must allow a web UI to display candidates and let the user choose another result without changing the downloader implementation.
2. **Direct video URL** — process the specific video identified by the URL; do not perform a title search or silently substitute a different video.

The source-adapter contract must be independent of the client. The same normalized request should be accepted from the web UI, a CLI/import tool, or the Chrome extension.

- The web UI should support submitting one URL or multiple newline-separated search queries/URLs, reviewing candidates where applicable, starting a batch, viewing progress/errors, and accessing output files.
- The future Chrome extension should read the URL of the currently open video after an explicit user action and submit that URL and necessary non-sensitive metadata to the API. The extension must not scrape or transmit account cookies or passwords.
- The API must validate URL schemes/hosts and reject unsupported sources rather than accepting arbitrary URLs for unrestricted server-side fetching.
- Long-running work must be asynchronous from the HTTP client's perspective. The API returns a batch/job identifier and clients poll or subscribe to progress; they do not hold an HTTP request open while downloading or separating.

## 6. Batch and job semantics

Use a `Batch` to group one or more source requests and a `Job` for each requested source. A single direct URL can be represented as a batch containing one job.

For a batch, preserve the two-phase behavior:

1. Resolve each source and attempt all requested audio downloads.
2. After all download attempts in the batch have finished, begin separation for each successfully downloaded item.

Do not start separation for the first item while other items in the same batch are still in the download phase. This is an explicit compatibility requirement.

Persist state transitions so a restart can recover work. Use explicit, documented statuses; a suggested initial set is `queued`, `searching`, `downloading`, `downloaded`, `separating`, `completed`, `failed`, and `cancelled`. Represent partial batch success explicitly; do not label a batch fully successful if some item failed. Each transition must be recorded as a job event.

Required job behavior:

- A failed item must not stop other items in the batch.
- Retry only transient failures; make retry count and delay configurable.
- Avoid duplicate work on repeated client submissions using an idempotency key or a documented deduplication rule.
- Reuse valid existing audio and complete stem outputs when appropriate.
- Handle partial/corrupt outputs safely. A non-empty file alone is not proof that an audio download is valid; validate enough metadata or document the limitation and add a recovery path.
- Persist error summaries and the failed stage, while keeping full technical details in logs.
- Define cancellation and restart/recovery behavior before implementing them; do not claim a job is resumable until this is tested.

## 7. Stable component contracts

Keep typed input/output data models (`app/audio_stems/models.py`) and pass collaborators in at the composition layer (`api.create_app`, `pipeline.build_worker`). Do not add Protocol/abstract classes for a single implementation; add one only together with the second implementation, and then cover both with the same tests.

The component methods to keep stable:

- `PytubeDownloader.search(query) -> list[VideoCandidate]` and `.download(source) -> DownloadedAudio`
- `StemSeparator.separate(audio_path, output_dir, options, progress=None) -> SeparationResult` (stems plus the input level that storage undoes)
- `LocalMediaStorage.save_audio(...)`, `save_stem(...)`, `resolve_path(...)` and related typed operations
- `SqliteMetadataStore` methods for creation, state transitions, querying, and event persistence

Contracts must describe expected failures, not just successful returns. Avoid having one method return unrelated types or use `None` for many indistinguishable failure cases. Prefer typed results/exceptions with clear error categories.

The Demucs implementation and the `pytubefix` implementation are adapters, not application-wide dependencies. Do not import either third-party library from API routes, job models, authentication, or storage code.

## 8. Storage and naming requirements

- The local storage adapter must retain the current root by default: `~/Audio Stems/YouTube Audio`.
- Keep source audio at the root and stems under `Stems/<safe track title>/`.
- Expected current stem names: `vocals.flac`, `drums.flac`, `bass.flac`, `guitar.flac`, `piano.flac`, and `other.flac` (four-stem models: no guitar or piano).
- Do not create subfolders for individual stems.
- Save file locations and metadata in the database as relative storage keys or managed asset IDs, not as arbitrary client-supplied paths.
- Prevent path traversal, unsafe filenames, accidental overwrite, and collisions. Preserve the readable title; if titles collide, add a stable source identifier to disambiguate.
- Temporary files belong in a dedicated configurable temporary directory. Clean them only after verifying all expected outputs exist and are valid.
- Store media metadata such as source URL/video ID, original title, media type, format/extension, size, checksum if practical, created time, and processing model/version. Do not duplicate large audio data inside the relational database.
- Keep all storage operations in `LocalMediaStorage` so local disk can later be replaced by object storage without changing the orchestrator.

## 9. Logging, events, and observability

Implement logging early. Use Python's standard `logging` with structured JSON output initially; do not add a separate observability platform until necessary.

### Application logs

- Write human-readable console logs and rotating JSON Lines files (one JSON object per line).
- Default level: `INFO`; allow `DEBUG` through configuration.
- Initial rotation policy: 10 MB per file and five backup files, configurable.
- Use UTC ISO-8601 timestamps.
- Include at minimum: `timestamp`, `level`, `module`, `event`, `ClientID`, `batch_id` when applicable, `job_id`, `message`, and error details when present.
- `ClientID` is the exact, case-sensitive JSON key. Internal Python code may use `client_id`, but serialized logs must emit `ClientID` for compatibility.
- `ClientID` is an identifier, not a credential. Derive it server-side from authenticated context; never trust a client-supplied `ClientID` to authorize access. Use `null` when no client context exists.
- Include `attempt` for retries and `error_type` plus a stack trace/exception field when an exception occurs.
- Use stable event names such as `batch_created`, `source_resolved`, `audio_download_started`, `audio_download_completed`, `audio_download_failed`, `separation_started`, `separation_completed`, `separation_failed`, and `job_state_changed`.
- Do not log passwords, auth tokens, session cookies, browser cookies, secret configuration values, or full sensitive HTTP headers. Avoid unnecessarily logging raw user input if it could contain secrets.

### Persistent job-event history

Store business-relevant job events in the database independently of text logs. The UI should use persisted state/event records for progress; it must not parse log files to determine the job status. Persist every job state transition and retain a concise user-safe error summary.

### Metrics

Begin with basic counters/durations that can be queried from stored job data: submissions, download successes/failures, separation successes/failures, retry counts, and stage durations. A dedicated metrics backend is a later option.

## 10. Authentication and security

Authentication is a separate module. Design for authenticated users/clients and enforce authorization on every endpoint that reads or mutates a batch, job, or asset.

- Never use `ClientID` alone as proof of identity or permission.
- Do not expose local filesystem paths to clients; return API asset IDs or authorized download URLs.
- Store secrets only in environment variables or a local ignored `.env` file; commit `.env.example` with placeholders only.
- Use secure password hashing if passwords are managed locally. Use secure session/token handling, sensible expiry, and CSRF protection where cookie-based browser sessions require it.
- Restrict CORS to configured origins. Validate uploads/URLs and cap request size, batch size, and concurrent work through configuration.
- Prevent arbitrary server-side URL fetching; allow only supported and explicitly configured source hosts/protocols.
- Apply per-user/client rate limits and resource limits before exposing the service beyond localhost.
- Keep auth integration replaceable. Do not overbuild OAuth or multi-tenant features before the initial local workflow is working, but never design data access in a way that assumes all users can see all files.

## 11. Data model — minimum entities

Keep entities small and normalize only where it improves correctness. A reasonable initial model:

- `Client` or `User`: stable ID, display/login fields, auth-related metadata, created/updated timestamps.
- `Batch`: ID, owner/client ID, submission method, status, created/updated timestamps, aggregate result summary.
- `Job`: ID, batch ID, owner/client ID, input type, normalized source/query, selected source/video ID, status, current stage, retry count, safe error summary, timestamps.
- `MediaAsset`: ID, job ID, asset type (`source_audio` or `stem`), stem name where applicable, storage key, format, byte size, checksum if enabled, model/version metadata where applicable.
- `JobEvent`: ID, batch/job IDs, event name, timestamp, safe structured details, and actor/client ID where applicable.

Use UUIDs or another documented opaque identifier. Add database migrations for schema changes. Do not store secrets in job/event details.

## 12. API and UI expectations

Use a versioned API prefix such as `/api/v1`. Keep HTTP schemas separate from ORM models.

Initial API capability set:

- Create a batch from a list of search queries and/or direct video URLs.
- List/search the current user's batches and inspect batch/job status.
- Retrieve candidates for a search query where manual selection is requested.
- Retry an eligible failed stage/job without duplicating completed work.
- List output assets and stream/download them only after authorization.
- Return structured validation errors and stable error codes.

Use the same API for the web UI and Chrome extension. Do not implement two separate business pipelines. The web UI should initially prioritize functionality: submit input, show candidate selection when enabled, show download/separation state, show errors, and offer authorized links to resulting files. Avoid spending time on visual polish before the workflow and contracts are tested.

## 13. Test strategy

Use `pytest`. Organize tests by unit, integration, and optional live smoke tests. Use temporary directories for storage tests and fake adapters for slow/external components.

### Baseline regression tests — implement first

- A batch attempts all downloads before any separation starts.
- One failed download does not prevent other downloads or later separations.
- Search mode selects the first result under the compatibility policy.
- Direct-URL mode uses the exact requested video and does not run a title search.
- WebM audio is preferred; fallback audio selection is used when WebM is unavailable.
- Retry count/delays follow configuration and a final failure is handled without aborting the batch.
- Existing non-empty audio is reused according to the documented validation policy.
- All six stems are written directly into `Stems/<safe title>/`, with no per-stem subfolders.
- A complete stem set is skipped; an incomplete set is retried/repaired safely.
- Temporary processing files are deleted only after validating all expected outputs.
- Windows-invalid filenames and reserved device names are sanitized; colliding titles do not overwrite another source's assets.

### Contract and integration tests

- If a second downloader/separator/storage implementation is added, both pass the same tests.
- Job state transitions are valid and persisted.
- Batch phase ordering is maintained after process restart/recovery.
- Log JSON contains required fields, especially the exact `ClientID` key, with `null` when unknown.
- Secrets and cookies do not appear in serialized log records.
- Users cannot access another user's batches/jobs/assets.
- Repeated submission with the same idempotency key does not create duplicate work.
- API routes return documented schemas and stable error codes.

Live tests against YouTube or a real Demucs model must be opt-in, clearly marked, and never required for the default test suite. Do not make CI depend on an external website's availability.

## 14. Configuration and developer experience

- Keep runtime settings in one validated settings module (for example, Pydantic Settings) and `.env.example`.
- Configure at least: storage root, temporary root, database URL, log level, log rotation, retry count/delays, allowed source hosts, batch limits, model name/device, and the API port. Add settings for worker concurrency and authentication/sessions together with those features, not before.
- Keep the root `README*.md` files for users (download and start, in English, Russian and German). Provide reproducible Windows PowerShell instructions in `app/README.md` for environment setup, tests, starting API, starting worker, and running any compatibility CLI.
- Pin or constrain dependencies after testing them together. Record Python and dependency versions used for the working environment.
- Add formatting/lint/type-check tools only when configured with commands the agent can actually run. Favor a small, reproducible quality gate over a large toolchain.

## 15. Implementation roadmap

Work through these milestones in order. Do not skip ahead without reporting the reason.

### Milestone 1 — Baseline and tests

- Inspect the repository and existing script before changing files.
- Record current dependency/Python versions and output structure.
- Add regression tests around current behavior using fakes and temporary directories.
- Keep the original runnable script intact. (Completed; the script was retired in version 4 after its behaviors were covered by service-level tests.)
- Deliver: tests, a short baseline note, and evidence of test results.

### Milestone 2 — Core module boundaries

- Extract source, downloader, separator, storage, and batch orchestration behind typed interfaces.
- Keep a compatibility entry point that preserves current behavior.
- Run regression tests after each extraction.

### Milestone 3 — Persistent jobs and logs

- Add database migrations, batch/job state, `JobEvent`, structured JSON logging, and `ClientID` propagation.
- Implement a separate worker process and crash/restart handling.

### Milestone 4 — API and functional web UI

- Add FastAPI routes and a practical UI that calls the API.
- Support search queries and direct URLs; display state and results.

Authentication and the Chrome extension are not planned for now (decided in version 11).

### Milestone 5 — Replaceable providers and scale

- Add a second downloader, separator, or storage implementation only when needed; prove replacement using contract tests.
- Extract a module into a separate service only when deployment, scaling, or reliability requirements justify it.

## 16. Definition of done for every change

A change is done only when all applicable conditions are satisfied:

1. Requirements and expected behavior are stated clearly.
2. Tests cover the new behavior and relevant regressions.
3. The relevant tests and quality checks were actually run; results are reported honestly.
4. The working baseline has not changed unintentionally.
5. Errors are observable through structured logs and, for job operations, persisted state/events.
6. Security and authorization implications have been considered.
7. Configuration and documentation are updated where needed.
8. No secrets, local audio, generated stems, database files, temporary outputs, or log files were added to version control.
9. The final response summarizes files changed, behavior changed, commands/tests run, known limitations, and the next recommended step.

## 17. Required workflow for Codex / Claude Code

For each task:

1. Read this specification and inspect the existing repository, instructions, working tree, and tests before editing.
2. Identify the smallest useful change and state a short implementation plan for multi-file work.
3. Check whether the requested behavior already exists; do not duplicate or replace functioning code unnecessarily.
4. Add or update tests before or alongside implementation.
5. Implement in small increments, respecting module boundaries and existing user changes.
6. Run relevant tests and checks. If a dependency, environment, or network problem prevents a test, report the limitation rather than claiming success.
7. Review the diff for accidental behavior changes, unsafe paths, secret leakage, temporary files, and untested error cases.
8. Summarize changed files, behavior, test commands/results, and unresolved issues. Do not commit or push unless requested.

If repository reality conflicts with this specification, do not blindly rewrite the repository. Describe the conflict, preserve working behavior, and propose the smallest migration step.

## 18. Reference documentation

- Python logging: https://docs.python.org/3/library/logging.html
- pytest: https://docs.pytest.org/
- FastAPI: https://fastapi.tiangolo.com/
- SQLAlchemy: https://docs.sqlalchemy.org/
- Alembic: https://alembic.sqlalchemy.org/
- pytubefix: https://github.com/JuanBindez/pytubefix
- Demucs: https://github.com/facebookresearch/demucs
- Codex repository instructions (`AGENTS.md`): https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide
- Claude Code project memory/instructions: https://docs.anthropic.com/en/docs/claude-code/memory
