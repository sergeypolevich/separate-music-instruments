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
