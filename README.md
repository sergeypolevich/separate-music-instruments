# Audio Stems

## Run on Windows

After downloading and extracting the GitHub folder, double-click
`Start.bat`. It prepares Python 3.12 and the project environment
when needed, starts both the local API and background worker, waits for the API,
and opens the app in your default browser. The first launch needs internet to
install Python (if missing) and the runtime dependencies, including ffmpeg (from
the `imageio-ffmpeg` package, so no system ffmpeg is needed); model weights are
downloaded when a model separates its first song. Later launches skip every setup
check while `app/requirements.txt` matches the installed copy in
`app/.venv/requirements.installed`, and rerun the setup by themselves when a
dependency is missing or the requirements changed. The automatically opened window
shows service logs; close it to stop both services. The app address is
`http://127.0.0.1:8000`.

The page must be opened from that local address. Opening `app/web/index.html`
directly as a file cannot reach the API. For troubleshooting or development,
the equivalent manual command is `py run.py --app`.

## Run on macOS

Double-click `Start.command` (Apple Silicon or Intel). macOS blocks scripts
downloaded from the internet the first time: right-click `Start.command`, choose
**Open** and confirm once (or run `xattr -dr com.apple.quarantine .` in the
folder). If Python 3.12 is missing, the launcher installs it for your user only,
without an administrator password, using a pinned and checksum-verified `uv`
release; then it prepares `app/.venv` and starts the app exactly like `Start.bat`,
including the fast start on later launches. Chrome and Safari 16.4 or newer are
supported.

## Project layout

The folder you download contains only `Start.bat` (Windows), `Start.command`
(macOS), this `README.md`, the agent
instructions (`AGENTS.md`, `CLAUDE.md`, `.claude/`) and the `app` folder with
everything else: code, web UI, tests, docs, requirements and the `.venv`. Your
songs, stems, database and logs are kept separately in `~/Audio Stems`.

All commands below are run in PowerShell from the `app` folder:

```powershell
cd app
```

Install Python 3.10 or newer, then start the app:

```powershell
py run.py
```

To set up the project explicitly and install the test tools:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

The launcher creates and uses `.venv` automatically, so activation is optional
when starting the app. Edit `.env` only for local settings; keep it out of Git.

The Python launcher creates a project-local `.venv` on first run, checks for the runtime
libraries listed in `requirements.txt`, installs them when needed, and starts
the app (API, worker, and browser — the same as `--app`). Demucs and its machine-learning dependencies can take a
while and use substantial disk space on the first installation. If installation
fails, the application is not started and the pip error is shown.

Runtime defaults are validated by `app/audio_stems/config.py`. Copy `.env.example` to `.env`
to override them; process environment variables take precedence over `.env`.
Logs are written as rotating JSON Lines under `~/Audio Stems/logs/` and also
shown in readable form in the console. Do not put real credentials in
`.env.example` or commit the local `.env` file.

To run the offline regression tests after installing pytest:

```powershell
py -m pip install -r requirements-test.txt
py -m pytest -q
```

By default, no live YouTube or Demucs smoke tests run. The separate opt-in
command is `$env:RUN_LIVE_E2E = "1"; py -m pytest -q -m e2e -s`. It starts the
API and worker, opens installed Edge or Chrome in a visible browser window, searches
YouTube for Singer - Short Spoken Clip (16 s), downloads it, runs Demucs, and checks project reopen,
playback, seek, per-track gain, and mute controls. It needs internet, the
Playwright test dependency, installed Edge or Chrome, available disk space, and
several minutes of CPU processing. Run it before every commit as required by
`skills/audio-stems-development.md`; it never installs an application update.

Run the database-backed worker in a separate PowerShell window:

```powershell
py run.py --worker
```

The launcher performs the same isolated environment and dependency checks, then
starts the worker instead of the compatibility CLI. It uses the same `.env`,
SQLite database, media root, and log configuration as the rest of the
application. The worker claims one queued
batch at a time; it completes all downloads in the batch before separating any
track. Stop it with Ctrl+C.

On startup, a batch left in `processing` by a prior worker exit is recovered in
a bounded way: completed jobs remain completed, unfinished jobs become failed
with a safe interruption summary, and the batch is marked failed or partial.
Downloaded files and completed stems remain available for reuse if the source
is submitted again. Jobs are not automatically resumed because no mid-stage
checkpoint proves that partial outputs are valid.

The application can still be started directly with the Python interpreter, but
that bypasses the dependency check. Use `py run.py` for normal startup.

Start the versioned HTTP API in another PowerShell window with:

```powershell
py run.py --api
```

It binds only to `127.0.0.1:8000`; the API process only validates and
queues requests. Keep `py run.py --worker` running separately to process them.
This initial local API uses a server-side `local` owner identity and has no
login screen; do not expose its port beyond the local machine.

The API accepts `POST /api/v1/batches` with an `inputs` array of search queries
or supported YouTube URLs and returns `202` with batch/job IDs. Use
`GET /api/v1/batches/{id}` for persisted job progress (job events are stored in
the database for diagnostics but have no HTTP endpoint); `/api/v1/projects` lists
saved projects and `/api/v1/models` lists model options. Errors use an
`error.code` and safe `error.message` response. The API never accepts file paths.
Project details provide opaque asset IDs. `GET /api/v1/assets/{asset_id}` streams
an available asset and supports single byte-range requests. The corresponding
`/waveform` endpoint returns 1024 peak levels of a WAV or FLAC stem, computed once per stem file
and stored in SQLite, so the page never downloads an audio file just to draw it.

For a quick local API check, with the API running in its own window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/projects
```

### Backup and restore

Stop both the API and worker with Ctrl+C before copying the database and media.
This lets SQLite finish and checkpoint any write-ahead log. The commands below
use the default paths from `app/audio_stems/config.py`; if `.env` overrides them, set `$dbPath`
and `$mediaPath` to those configured locations instead.

```powershell
$backupPath = Join-Path $HOME ("Audio Stems Backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$dbPath = Join-Path $HOME "Audio Stems\audio_stems.db"
$mediaPath = Join-Path $HOME "Audio Stems\Music"
New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
Copy-Item -LiteralPath $dbPath -Destination $backupPath
Copy-Item -LiteralPath $mediaPath -Destination $backupPath -Recurse
```

Keep the backup on a separate drive or other safe location. To restore, stop
the API and worker, then copy both components back to their configured parents:

```powershell
Copy-Item -LiteralPath (Join-Path $backupPath "audio_stems.db") `
  -Destination (Split-Path -Parent $dbPath) -Force
Copy-Item -LiteralPath (Join-Path $backupPath "Music") `
  -Destination (Split-Path -Parent $mediaPath) -Recurse -Force
```

Restore both together so asset records continue to match their files. The local
`.env` file is not part of this media/database backup; preserve it separately
if you customized it.

### Maintenance

- Keep enough free disk space for the source audio, the FLAC stems (about
  20 MB per minute), model weights (BS-RoFormer SW about 700 MB in
  `~/Audio Stems/models`), and temporary float output (about 120 MB per minute
  while a song is separated). A job that does not fit fails with the space it needs. Check the configured storage and temp roots in
  `.env` before moving or cleaning data.
- Use the project UI to delete completed projects. If the API reports pending
  file cleanup, reopen that project ID's delete action by retrying the API
  request; cleanup receipts remain in SQLite until file deletion succeeds.
- Stop the worker before inspecting `_demucs_temp`. Remove only abandoned
  temporary folders after confirming no worker is using them; do not remove
  source audio or `Stems` as routine cleanup.
- Logs rotate at the configured 10 MB/five-file default. Archive or remove old
  log files manually when needed; never use logs as the source of job status.
- If the isolated Python environment is damaged, stop API/worker, remove only
  the project `.venv` directory, then run `py run.py` to recreate it. This does
  not remove the database or media stored under your user profile.

### Updates

The Updates panel checks the fixed public GitHub repository for stable releases
only when you click **Проверить**. It displays the release notes and a link to the
release page. Applying an update requires a second confirmation and reads the
exact `requirements.txt` at that release's fixed GitHub tag. The updater rejects
manifests unless each approved runtime package has one exact `==` version pin,
then runs pip with the current project interpreter; no client-supplied command
or path is accepted. Stop the worker before applying an update. The API request
waits for pip to finish (up
to 30 minutes); after success, restart both the API and worker. If installation
fails, the API reports the failure and you can retry after reviewing the local
pip output. The updater does not replace application source files.

The separator exposes two local models: `htdemucs_6s` (default, fast) and
`bs_roformer_sw` (through audio-separator, overlap 2, much slower but clearly
better); the measurements and listening tests behind the choice are in
`app/DEVELOPMENT_STATUS.md` (versions 10 and 11).
Settings stores the chosen model in the browser and each job sends it; the device
comes from `AUDIO_STEMS_MODEL_DEVICE`
(default `auto`: CUDA, then Apple MPS, then CPU, whichever the installed PyTorch
can use). SQLite stores the resolved configuration and the worker passes it to
the separator.

Before reusing an existing audio file, the application checks its container
signature (for example, the WebM or MP4 header). This catches empty and plainly
corrupt cache files, but it does not decode the audio or guarantee that Demucs
can process every file. Invalid cached files are preserved under
`_demucs_temp/_recovery/` and the downloader can fetch a replacement. If a file
passes the header check but Demucs rejects it, move that source audio out of the
`Music` folder and rerun the track; the original is not deleted by this
fallback.
