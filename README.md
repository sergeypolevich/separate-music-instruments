# Audio Stems

## Run on Windows

Install Python 3.10 or newer, then run this from the project folder in PowerShell:

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

The launcher creates a project-local `.venv` on first run, checks for the runtime
libraries listed in `requirements.txt`, installs them when needed, and starts
`download_tracks.py`. Demucs and its machine-learning dependencies can take a
while and use substantial disk space on the first installation. If installation
fails, the application is not started and the pip error is shown.

Runtime defaults are validated by `settings.py`. Copy `.env.example` to `.env`
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
command is `$env:RUN_LIVE_E2E = "1"; py -m pytest -m e2e -s`; it downloads and
processes the test video and requires authorization to use that source.

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
`GET /api/v1/batches`, `/api/v1/batches/{id}`, `/api/v1/jobs/{id}`, and
`/api/v1/jobs/{id}/events` for persisted progress; `/api/v1/projects` lists
saved projects and `/api/v1/models` lists model options. Errors use an
`error.code` and safe `error.message` response. The API never accepts file paths.
Project details provide opaque asset IDs. `GET /api/v1/assets/{asset_id}` streams
an available asset and supports single byte-range requests. The corresponding
`/waveform` endpoint returns bounded PCM WAV peak levels so the page does not
need to download an entire audio file just to draw its waveform.

For a quick local API check, with the API running in its own window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/projects
```

### Backup and restore

Stop both the API and worker with Ctrl+C before copying the database and media.
This lets SQLite finish and checkpoint any write-ahead log. The commands below
use the default paths from `settings.py`; if `.env` overrides them, set `$dbPath`
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

- Keep enough free disk space for the source audio, six WAV stems, model weights,
  and temporary Demucs output. Check the configured storage and temp roots in
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

The Demucs adapter exposes its supported model and inference parameters. The UI
lets each job select a model and device; SQLite stores the configuration and
the worker passes it to the separator. The standalone compatibility command
continues to use `AUDIO_STEMS_MODEL_NAME` and `AUDIO_STEMS_MODEL_DEVICE` from
`.env`; defaults remain `htdemucs_6s` on CPU.

Before reusing an existing audio file, the application checks its container
signature (for example, the WebM or MP4 header). This catches empty and plainly
corrupt cache files, but it does not decode the audio or guarantee that Demucs
can process every file. Invalid cached files are preserved under
`_demucs_temp/_recovery/` and the downloader can fetch a replacement. If a file
passes the header check but Demucs rejects it, move that source audio out of the
`Music` folder and rerun the track; the original is not deleted by this
fallback.
