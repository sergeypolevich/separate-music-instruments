# Audio Stems

## Run on Windows

Install Python 3.10 or newer, then run this from the project folder in PowerShell:

```powershell
py run.py
```

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
py -m pytest
```

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

The Demucs adapter exposes its model names and supported inference parameters.
Jobs store the selected model, device, and parameters in SQLite, and pass that
saved configuration to the separator. The standalone compatibility command
continues to use `AUDIO_STEMS_MODEL_NAME` and `AUDIO_STEMS_MODEL_DEVICE` from
`.env`; defaults remain `htdemucs_6s` on CPU. The future API/UI will expose
per-job selection through the same saved job configuration.

Before reusing an existing audio file, the application checks its container
signature (for example, the WebM or MP4 header). This catches empty and plainly
corrupt cache files, but it does not decode the audio or guarantee that Demucs
can process every file. Invalid cached files are preserved under
`_demucs_temp/_recovery/` and the downloader can fetch a replacement. If a file
passes the header check but Demucs rejects it, move that source audio out of the
`Music` folder and rerun the track; the original is not deleted by this
fallback.
