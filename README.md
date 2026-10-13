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

The application can still be started directly with the Python interpreter, but
that bypasses the dependency check. Use `py run.py` for normal startup.

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
`YouTube Audio` folder and rerun the track; the original is not deleted by this
fallback.
