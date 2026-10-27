"""Local-only ASGI/HTTP smoke for the documented API and served UI."""
import socket
import threading
import time
from dataclasses import replace
from uuid import UUID

import httpx
import uvicorn

from audio_stems.api import create_app
from audio_stems.separator import StemSeparator
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings
from audio_stems.models import YouTubeSourceAdapter


def test_uvicorn_serves_ui_and_local_api_without_worker_or_network(tmp_path):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'smoke.db').as_posix()}",
        storage_root=tmp_path / "media",
    )
    repository = SqliteMetadataStore(tmp_path / "smoke.db")

    class NoNetworkUpdates:
        def check(self):
            return {"current": 17, "mode": "archive", "releases": []}

        def apply(self, *_args, **_kwargs):
            raise AssertionError("smoke test must not install dependencies")

    app = create_app(
        repository=repository,
        settings=settings,
        source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts),
        separator=StemSeparator(),
        updater=NoNetworkUpdates(),
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        # 10 s: the first batch detects the device (imports torch), 2-3 s on a laptop.
        with httpx.Client(base_url=base_url, timeout=10) as client:
            deadline = time.monotonic() + 5
            while True:
                try:
                    health = client.get("/api/v1/health")
                    break
                except httpx.TransportError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            page = client.get("/")
            script = client.get("/app.js")
            playback = client.get("/playback.mjs")
            styles = client.get("/app.css")
            projects = client.get("/api/v1/projects")
            batch = client.post("/api/v1/batches", json={"inputs": ["Smoke test query"]})

            assert health.status_code == 200 and health.json() == {"status": "ok"}
            assert page.status_code == 200 and 'id="check-updates"' in page.text
            assert script.status_code == styles.status_code == playback.status_code == projects.status_code == 200
            assert projects.json() == []
            assert batch.status_code == 202 and len(batch.json()["job_ids"]) == 1
            stored = repository.get_batch(UUID(batch.json()["batch_id"]), "local")
            assert stored is not None and stored.status == "queued"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "local API server did not stop cleanly"
