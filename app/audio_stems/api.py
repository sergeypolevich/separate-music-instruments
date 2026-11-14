"""Versioned localhost HTTP API and WAV waveform peaks; all work is delegated to the SQLite queue."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import mimetypes
import os
import re
from pathlib import Path, PureWindowsPath
import subprocess
import sys
from typing import Annotated, Callable
from urllib.parse import unquote
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from fastapi.staticfiles import StaticFiles

from audio_stems.models import (
    AdapterError,
    FailureCategory,
    JobStatus,
    MediaAssetType,
    SeparationOptions,
    SourceKind,
    SourceRequest,
    disk_full_error,
    is_disk_full,
)
from audio_stems import i18n, plugins
from audio_stems.chords import ALGORITHM as CHORDS_ALGORITHM
from audio_stems.beats import ALGORITHM as BEATS_ALGORITHM
from audio_stems.separator import BEST_MODEL, StemSeparator
from audio_stems.storage import LocalMediaStorage
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings
from audio_stems.models import YouTubeSourceAdapter
from audio_stems.updates import RESTART_EXIT_CODE, RESUME_MARKER, UpdateCheckError, UpdateError, Updater
import numpy as np
import soundfile


_OWNER_ID = "local"
# Fixed waveform resolution: enough detail for a full-width desktop mixer, small enough to store.
WAVEFORM_POINTS = 1024
DETAIL_POINTS = 8192  # for the zoom plugin (version 22): a few seconds of a song still show their shape
_MEDIA_CHUNK_BYTES = 1024 * 1024
logger = logging.getLogger("audio_stems.api")





class BatchSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(min_length=1, max_length=100)
    model_name: Annotated[str | None, Field(max_length=100)] = None


class _UiFiles(StaticFiles):
    # Without Cache-Control browsers cache UI files heuristically and can pair a new index.html
    # with a stale app.js/app.css after an update; revalidate (cheap 304 via ETag) on every load.
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(max_length=200)]


class PluginSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class PluginError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Annotated[str, Field(max_length=500)]


class PluginData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict


_PLUGIN_DATA_BYTES = 64 * 1024  # trim bounds, EQ bands, edited chords: a few kilobytes


class ProjectOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_ids: list[UUID] = Field(max_length=10_000)


class StorageCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool


def _folder_bytes(path: Path) -> int:
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())
    except OSError:
        return 0


class UpdateApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag_name: Annotated[str, Field(min_length=1, max_length=64)]
    confirmed: StrictBool


def _job_payload(job) -> dict[str, object]:
    return {
        "job_id": str(job.job_id),
        "status": job.status.value,
        "current_stage": job.current_stage,
        "retry_count": job.retry_count,
        "error_summary": job.error_summary,
        "input_value": job.input_value,
    }


class WaveformError(ValueError):
    pass


# Windows maps .flac to audio/x-flac; browsers expect audio/flac.
mimetypes.add_type("audio/flac", ".flac")
_WAVEFORM_FORMATS = {"wav", "flac"}


def waveform_data(path: Path, levels: int, start_seconds: float = 0.0) -> dict[str, object]:
    """Return at most ``levels`` peak points from ``start_seconds`` on, without loading the whole file."""
    if levels < 1 or levels > DETAIL_POINTS:
        raise WaveformError("Waveform level count is outside supported bounds")
    try:
        stat = path.stat()
    except OSError as exc:
        raise WaveformError("Audio file could not be read") from exc
    try:
        with soundfile.SoundFile(path) as source:
            channels = source.channels
            sample_rate = source.samplerate
            if not 1 <= channels <= 32 or sample_rate <= 0:
                raise WaveformError("Audio encoding is not supported for waveform extraction")
            start_frame = min(source.frames, int(start_seconds * sample_rate))
            source.seek(start_frame)
            frame_count = source.frames - start_frame

            bucket_count = min(levels, frame_count)
            peaks: list[float] = []
            for bucket_index in range(bucket_count):
                frame_start = bucket_index * frame_count // bucket_count
                frame_end = (bucket_index + 1) * frame_count // bucket_count
                frames_left = frame_end - frame_start
                peak = 0.0
                while frames_left:
                    count = min(frames_left, 65536)
                    samples = source.read(count, dtype="float32")
                    actual_frames = len(samples)
                    if actual_frames == 0:
                        raise WaveformError("Audio file ended before its declared frame count")
                    peak = max(peak, min(1.0, float(np.abs(samples).max())))
                    frames_left -= actual_frames
                peaks.append(peak)
    except WaveformError:
        raise
    except (OSError, RuntimeError) as exc:  # libsndfile raises RuntimeError subclasses
        raise WaveformError("Audio file could not be read") from exc

    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "frame_count": frame_count,
        "duration_seconds": frame_count / sample_rate,
        "start_seconds": start_seconds,
        "levels": peaks,
    }


def create_app(
    *,
    repository: SqliteMetadataStore,
    settings: Settings,
    source_adapter: YouTubeSourceAdapter | None = None,
    separator: StemSeparator | None = None,
    storage: LocalMediaStorage | None = None,
    updater: Updater | None = None,
    start_analysis: Callable[[], None] | None = None,
    exit_process: Callable[[int], None] = os._exit,
) -> FastAPI:
    """Create an API instance using injected local infrastructure."""
    source_adapter = source_adapter or YouTubeSourceAdapter(settings.allowed_source_hosts)
    separator = separator or StemSeparator()
    storage = storage or LocalMediaStorage(settings.storage_root, temp_root=settings.temp_root)
    application_root = Path(__file__).resolve().parents[1]
    # The release is unpacked in the data folder, next to Music/, never inside the app it will replace.
    updater = updater or Updater(application_root.parent, settings.storage_root.parent / "update")
    def finish_deletion(deletion) -> None:
        for storage_key in deletion.storage_keys:
            storage.delete_asset(storage_key)
        # Leftovers of a failed separation, unless another job of the same song is using them now.
        if deletion.title and deletion.source_id and not repository.source_is_busy(deletion.source_id):
            storage.remove_temporary_directory(deletion.title, deletion.source_id)
        repository.complete_project_deletion(deletion.project_id, deletion.owner_id)

    def spawn_analysis_runner() -> None:
        # Its own process: beat tracking loads torch and must not block API requests.
        subprocess.Popen([sys.executable, "-m", "audio_stems.analysis", str(repository.database_path)],
                         cwd=application_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    start_analysis = start_analysis or spawn_analysis_runner
    interrupted = [repository.requeue_interrupted_analyses(kind) for kind in ("beats", "chords")]
    if any(interrupted):
        start_analysis()

    # Finish cleanups that an earlier run could not complete (a file was locked at the time).
    for pending in repository.pending_project_deletions():
        try:
            finish_deletion(pending)
        except (AdapterError, OSError):
            logger.warning("Project file cleanup is still pending",
                           extra={"event": "project_cleanup_pending", "project_id": str(pending.project_id)})

    app = FastAPI(title="Audio Stems API", version="1.0.0")
    web_root = Path(__file__).resolve().parents[1] / "web"
    # The UI loads stems from the other loopback name (see web/app.js); allow only those two origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://{host}:{settings.api_port}" for host in ("127.0.0.1", "localhost")],
        allow_methods=["GET"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": "Request data is invalid"}},
        )

    @app.exception_handler(AdapterError)
    async def adapter_error(_request: Request, exc: AdapterError):
        code = "invalid_source" if exc.category is FailureCategory.INVALID_INPUT else "processing_error"
        http_status = 422 if exc.category is FailureCategory.INVALID_INPUT else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(status_code=http_status, content={"error": {"code": code, "message": str(exc)}})

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code", "http_error")
        message = detail.get("message", "Request could not be completed")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
            headers=exc.headers,
        )

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "process": os.getpid()}  # a new number tells the page the app has restarted

    def rates() -> dict[str, float]:
        """This machine's measured speed per model, else the test laptop's."""
        measured = repository.separation_rates()
        return {m.name: measured.get(m.name, m.seconds_per_audio_second) for m in separator.available_models()}

    def eta_seconds(project, speed: dict[str, float]) -> float | None:
        if project.job_status in {"completed", "failed", "cancelled"} or not project.audio_seconds:
            return None
        expected = speed.get(project.model_name or settings.model_name, 1.0) * project.audio_seconds
        if project.job_status != "separating" or not project.separation_started_at:
            return round(expected)
        elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(project.separation_started_at)).total_seconds())
        done = (project.job_progress - 0.10) / 0.87  # the separation's share of the job's progress
        # Early on the separator's own progress is too coarse; trust the measured speed until then.
        remaining = elapsed / done - elapsed if done >= 0.05 else expected - elapsed
        return round(max(0.0, remaining))

    @app.get("/api/v1/models")
    def models() -> list[dict[str, object]]:
        speed, measured = rates(), repository.separation_rates()
        return [
            {
                "name": model.name,
                "label": model.label,
                "description": model.description,
                "stems": list(model.stems),
                "default": model.name == settings.model_name,
                "seconds_per_audio_second": round(speed[model.name], 2),
                "measured": model.name in measured,  # the median of this machine's runs, else the test laptop's
            }
            for model in separator.available_models()
        ]

    @app.get("/api/v1/updates")
    def check_updates() -> dict[str, object]:
        try:
            found = updater.check()
        except UpdateCheckError as exc:
            logger.exception(
                "GitHub release check failed",
                extra={"event": "update_check_failed", "ClientID": _OWNER_ID, "error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=503,
                detail={"code": "update_check_failed", "message": "Could not retrieve release information from GitHub"},
            ) from exc
        return {**found, "releases": [release.__dict__ for release in found["releases"]]}

    def beats_state(project_id: UUID) -> dict[str, object]:
        state = repository.get_analysis(project_id, "beats") or {"status": "none", "progress": 0, "result": None, "error": None}
        return {key: state[key] for key in ("status", "progress", "result", "error")}

    @app.get("/api/v1/projects/{project_id}/beats")
    def get_beats(project_id: UUID) -> dict[str, object]:
        if repository.get_project(project_id, _OWNER_ID) is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        return beats_state(project_id)

    @app.post("/api/v1/projects/{project_id}/beats")
    def request_beats(project_id: UUID) -> JSONResponse:
        project = repository.get_project(project_id, _OWNER_ID)
        job = repository.get_job(project.job_id, _OWNER_ID) if project else None
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        state = repository.get_analysis(project_id, "beats")
        outdated = bool(state and state["status"] == "done"
                        and (state["result"] or {}).get("algorithm") != BEATS_ALGORITHM)  # analysed again once
        if state and state["status"] != "failed" and not outdated:
            return JSONResponse(beats_state(project_id), status_code=200 if state["status"] == "done" else 202)
        assets = repository.list_assets(job.job_id, _OWNER_ID)
        source = next((a for a in assets if a.asset_type is MediaAssetType.SOURCE_AUDIO), None)
        stems = [a for a in assets if a.asset_type is MediaAssetType.STEM]
        try:
            audio_path = storage.resolve_path(source.storage_key) if source else None
        except AdapterError:
            audio_path = None
        if job.status is not JobStatus.COMPLETED or audio_path is None or not audio_path.is_file() or not stems:
            raise HTTPException(
                status_code=409,
                detail={"code": "source_unavailable", "message": "Beats need the source audio of a finished project"},
            )
        try:
            minutes = soundfile.info(storage.resolve_path(stems[0].storage_key)).duration / 60
        except (AdapterError, OSError, RuntimeError):
            minutes = 4.0
        repository.queue_analysis(project_id, "beats", {
            "audio_path": str(audio_path), "start_seconds": job.start_seconds or 0.0, "minutes": minutes,
        }, redo=outdated)
        start_analysis()
        return JSONResponse(beats_state(project_id), status_code=202)

    def chords_state(project_id: UUID) -> dict[str, object]:
        state = repository.get_analysis(project_id, "chords") or {"status": "none", "progress": 0, "result": None, "error": None}
        return {key: state[key] for key in ("status", "progress", "result", "error")}

    @app.get("/api/v1/projects/{project_id}/chords")
    def get_chords(project_id: UUID) -> dict[str, object]:
        if repository.get_project(project_id, _OWNER_ID) is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        return chords_state(project_id)

    @app.post("/api/v1/projects/{project_id}/chords")
    def request_chords(project_id: UUID) -> JSONResponse:
        """Chords for the chords plugin (version 22): a background analysis of the stems without vocals and drums."""
        project = repository.get_project(project_id, _OWNER_ID)
        job = repository.get_job(project.job_id, _OWNER_ID) if project else None
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        state = repository.get_analysis(project_id, "chords")
        outdated = bool(state and state["status"] == "done"
                        and (state["result"] or {}).get("algorithm") != CHORDS_ALGORITHM)
        if state and state["status"] != "failed" and not outdated:
            return JSONResponse(chords_state(project_id), status_code=200 if state["status"] == "done" else 202)
        stems = [a for a in repository.list_assets(job.job_id, _OWNER_ID)
                 if a.asset_type is MediaAssetType.STEM and a.stem_name not in ("vocals", "drums")]
        try:
            paths = [storage.resolve_path(a.storage_key) for a in stems]
        except AdapterError:
            paths = []
        if job.status is not JobStatus.COMPLETED or not paths or not all(path.is_file() for path in paths):
            raise HTTPException(
                status_code=409,
                detail={"code": "source_unavailable", "message": "Chords need the stems of a finished project"},
            )
        found = repository.get_analysis(project_id, "beats") or {}
        beats = (found.get("result") or {}).get("beats", []) if found.get("status") == "done" else []
        try:
            minutes = soundfile.info(paths[0]).duration / 60
        except (OSError, RuntimeError):
            minutes = 4.0
        repository.queue_analysis(project_id, "chords", {
            "stem_paths": [str(path) for path in paths], "start_seconds": job.start_seconds or 0.0,
            "beats": beats, "minutes": minutes,
        }, redo=outdated)
        start_analysis()
        return JSONResponse(chords_state(project_id), status_code=202)

    # --- plugins (version 22): the store is the plugin folders shipped in web/plugins -----------------------
    def plugin_manifest(plugin_id: str) -> dict[str, object]:
        manifest = plugins.catalog(web_root).get(plugin_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Plugin not found"})
        return manifest

    def plugin_state(plugin_id: str, manifest: dict[str, object]) -> dict[str, object]:
        installed = repository.list_plugins().get(plugin_id)
        return {**manifest, "installed": installed is not None, "enabled": bool(installed and installed["enabled"]),
                "error": installed["error"] if installed else None}

    @app.get("/api/v1/plugins")
    def list_plugins() -> list[dict[str, object]]:
        return [plugin_state(plugin_id, manifest) for plugin_id, manifest in plugins.catalog(web_root).items()]

    @app.put("/api/v1/plugins/{plugin_id}")
    def switch_plugin(plugin_id: str, body: PluginSwitch) -> dict[str, object]:
        """Install (the first time) and turn on, or turn off; turning on again clears an error."""
        manifest = plugin_manifest(plugin_id)
        repository.set_plugin(plugin_id, body.enabled)
        return plugin_state(plugin_id, manifest)

    @app.delete("/api/v1/plugins/{plugin_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_plugin(plugin_id: str) -> Response:
        plugin_manifest(plugin_id)
        repository.remove_plugin(plugin_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/plugins/{plugin_id}/error")
    def plugin_failed(plugin_id: str, body: PluginError) -> dict[str, object]:
        """The page caught an error in the plugin: it is turned off and keeps the message for its card."""
        manifest = plugin_manifest(plugin_id)
        if plugin_id in repository.list_plugins():
            repository.set_plugin(plugin_id, False, body.message.strip() or "Unknown error")
        return plugin_state(plugin_id, manifest)

    def installed_plugin(plugin_id: str, project_id: UUID) -> None:
        plugin_manifest(plugin_id)
        if repository.get_project(project_id, _OWNER_ID) is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        if plugin_id not in repository.list_plugins():
            raise HTTPException(status_code=409, detail={"code": "plugin_not_installed", "message": "The plugin is not installed"})

    @app.get("/api/v1/projects/{project_id}/plugins/{plugin_id}")
    def get_plugin_data(project_id: UUID, plugin_id: str) -> dict[str, object]:
        installed_plugin(plugin_id, project_id)
        return {"data": repository.get_plugin_data(plugin_id, project_id)}

    @app.put("/api/v1/projects/{project_id}/plugins/{plugin_id}", status_code=status.HTTP_204_NO_CONTENT)
    def save_plugin_data(project_id: UUID, plugin_id: str, body: PluginData) -> Response:
        installed_plugin(plugin_id, project_id)
        if len(json.dumps(body.data)) > _PLUGIN_DATA_BYTES:
            raise HTTPException(status_code=413, detail={"code": "too_large", "message": "The plugin data is too large"})
        repository.set_plugin_data(plugin_id, project_id, body.data)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def upgrade_state(project, job) -> dict[str, object]:
        """«Improve quality»: whether the project already has the best model, how long the upgrade takes
        on this machine, and how far a running one is."""
        state = repository.get_analysis(project.project_id, "upgrade") or {
            "status": "none", "progress": 0.0, "error": None, "updated_at": None}
        best = bool(job.separation_options and job.separation_options.model_name == BEST_MODEL)
        seconds = project.audio_seconds
        if not seconds:  # projects from before version 11 never stored the song's length
            stems = [a for a in repository.list_assets(job.job_id, _OWNER_ID) if a.asset_type is MediaAssetType.STEM]
            try:
                seconds = soundfile.info(storage.resolve_path(stems[0].storage_key)).duration if stems else None
            except (AdapterError, OSError, RuntimeError):
                seconds = None
        rate = rates().get(BEST_MODEL)
        estimate = round(rate * seconds) if rate and seconds else None
        eta = estimate if state["status"] == "queued" else None
        if state["status"] == "running" and estimate is not None and state["updated_at"]:
            # updated_at is the claim time while running; progress only comes from the separator.
            elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(state["updated_at"])).total_seconds())
            done = state["progress"]
            eta = round(max(0.0, elapsed / done - elapsed if done >= 0.05 else estimate - elapsed))
        return {"status": state["status"], "progress": round(state["progress"], 3), "error": state["error"],
                "best": best, "estimate_seconds": estimate, "eta_seconds": eta}

    def project_and_job(project_id: UUID):
        project = repository.get_project(project_id, _OWNER_ID)
        job = repository.get_job(project.job_id, _OWNER_ID) if project else None
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        return project, job

    @app.get("/api/v1/projects/{project_id}/upgrade")
    def get_upgrade(project_id: UUID) -> dict[str, object]:
        return upgrade_state(*project_and_job(project_id))

    @app.post("/api/v1/projects/{project_id}/upgrade")
    def request_upgrade(project_id: UUID) -> JSONResponse:
        project, job = project_and_job(project_id)
        state = upgrade_state(project, job)
        if state["status"] in ("queued", "running"):
            return JSONResponse(state, status_code=202)
        if state["best"]:
            raise HTTPException(status_code=409, detail={"code": "already_best", "message": "The project is already separated with the best model"})
        source = next((a for a in repository.list_assets(job.job_id, _OWNER_ID) if a.asset_type is MediaAssetType.SOURCE_AUDIO), None)
        try:
            source_ok = source is not None and storage.resolve_path(source.storage_key).is_file()
        except AdapterError:
            source_ok = False
        if job.status is not JobStatus.COMPLETED or not source_ok:
            raise HTTPException(
                status_code=409,
                detail={"code": "source_unavailable", "message": "Improving needs the source audio of a finished project"},
            )
        repository.queue_analysis(project_id, "upgrade", {"owner_id": _OWNER_ID})  # the worker runs it
        return JSONResponse(upgrade_state(project, job), status_code=202)

    @app.delete("/api/v1/projects/{project_id}/upgrade")
    def cancel_upgrade(project_id: UUID) -> dict[str, object]:
        """Cancel «Improve quality»: a queued one is gone at once, a running one stops within a second or so
        (the worker); the project keeps its stems either way."""
        project, job = project_and_job(project_id)
        outcome = repository.cancel_analysis(project_id, "upgrade")
        if outcome is None:
            raise HTTPException(status_code=409, detail={"code": "no_upgrade", "message": "No quality improvement is running"})
        return {"state": outcome, **upgrade_state(project, job)}

    @app.get("/api/v1/storage")
    def storage_report() -> dict[str, object]:
        report = storage.disk_report(repository.storage_keys_in_use())
        # audio-separator weights, and the torch hub cache where Demucs keeps its own.
        torch_hub = Path(os.environ.get("TORCH_HOME", Path.home() / ".cache" / "torch")) / "hub" / "checkpoints"
        return {
            **report,
            "models_bytes": _folder_bytes(separator.model_dir) + _folder_bytes(torch_hub),
            "busy": repository.has_active_jobs(),
        }

    @app.post("/api/v1/storage/cleanup")
    def storage_cleanup(body: StorageCleanupRequest) -> dict[str, object]:
        if not body.confirmed:
            raise HTTPException(status_code=409, detail={"code": "confirmation_required", "message": "Confirm the cleanup first"})
        # shortcut: checked once, not locked; a job starting during the cleanup could lose its first temp file.
        if repository.has_active_jobs():
            raise HTTPException(
                status_code=409,
                detail={"code": "processing_active", "message": "Wait until the separation ends, then clean up"},
            )
        return {"freed_bytes": storage.clean_unused(repository.storage_keys_in_use())}

    @app.post("/api/v1/updates/apply")
    def apply_update(body: UpdateApplyRequest, background: BackgroundTasks) -> dict[str, object]:
        if not body.confirmed:
            raise HTTPException(
                status_code=409,
                detail={"code": "confirmation_required", "message": "Confirm the update before continuing"},
            )
        try:
            state = updater.apply(body.tag_name)
        except UpdateCheckError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "update_check_failed", "message": "Could not retrieve release information from GitHub"},
            ) from exc
        except UpdateError as exc:
            raise HTTPException(status_code=409, detail={"code": "update_refused", "message": str(exc)}) from exc
        except (subprocess.SubprocessError, OSError) as exc:
            logger.exception(
                "Update failed",
                extra={"event": "update_failed", "ClientID": _OWNER_ID, "tag_name": body.tag_name,
                       "error_type": type(exc).__name__},
            )
            raise HTTPException(status_code=500, detail={"code": "update_failed", "message": "The update could not be installed"}) from exc
        logger.info("Update prepared", extra={"event": "update_" + state, "ClientID": _OWNER_ID, "tag_name": body.tag_name})
        # Running work is not waited for: the next worker puts it back into the queue. After the answer is sent the
        # API exits, and the launcher restarts the app with the new code.
        (settings.storage_root.parent / RESUME_MARKER).touch()
        background.add_task(exit_process, RESTART_EXIT_CODE)
        return {"state": state, "tag_name": body.tag_name, "restart_required": True}

    @app.post("/api/v1/batches", status_code=status.HTTP_202_ACCEPTED)
    def create_batch(body: BatchSubmission) -> dict[str, object]:
        if len(body.inputs) > settings.batch_limit:
            raise HTTPException(
                status_code=422,
                detail={"code": "batch_limit_exceeded", "message": "Batch exceeds the configured item limit"},
            )
        sources = [source_adapter.normalize(value) for value in body.inputs]
        return queue_batch(sources, body.model_name)

    def separation_options(model_name: str | None) -> SeparationOptions:
        options = SeparationOptions(model_name or settings.model_name, separator.resolve_device(settings.model_device))
        try:
            separator.validate_options(options)
        except AdapterError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_model_options", "message": str(exc)},
            ) from exc
        return options

    def queue_batch(sources: list[SourceRequest], model_name: str | None, title: str | None = None) -> dict[str, object]:
        options = separation_options(model_name)
        receipt = repository.create_batch(
            _OWNER_ID, sources, separation_options=options,
        )
        if title is not None:  # a local file is named at once; the worker finds nothing to look up
            repository.update_project_source(receipt.job_ids[0], sources[0].value[:16], title)
        stored_batch = repository.get_batch(receipt.batch_id, _OWNER_ID)
        stored_jobs = repository.list_jobs_for_batch(receipt.batch_id, _OWNER_ID)
        logger.info(
            "Batch submitted to the persistent queue",
            extra={
                "event": "batch_created",
                "ClientID": _OWNER_ID,
                "batch_id": str(receipt.batch_id),
                "job_id": str(receipt.job_ids[0]) if receipt.job_ids else None,
                "job_count": len(receipt.job_ids),
            },
        )
        return {
            "batch_id": str(receipt.batch_id),
            "status": stored_batch.status if stored_batch else "queued",
            "job_ids": [str(job_id) for job_id in receipt.job_ids],
            "jobs": [_job_payload(job) for job in stored_jobs],
        }

    @app.post("/api/v1/uploads", status_code=status.HTTP_202_ACCEPTED)
    async def upload_file(request: Request, model_name: str | None = None) -> dict[str, object]:
        """The request body is the audio file itself; its name comes URL-encoded in X-Filename."""
        name = PureWindowsPath(unquote(request.headers.get("x-filename", ""))).name.strip()  # no folders
        if not name or len(name) > 255:
            raise HTTPException(status_code=400, detail={"code": "missing_filename", "message": "File name is required"})
        separation_options(model_name)  # refuse a bad model before receiving the whole file
        limit = settings.upload_limit_mb * 2**20
        too_large = HTTPException(status_code=413, detail={
            "code": "file_too_large", "message": "The file is larger than {limit} MB".format(limit=settings.upload_limit_mb)})
        if int(request.headers.get("content-length") or 0) > limit:
            raise too_large
        partial = storage.new_upload_path()
        try:
            received = 0
            with partial.open("wb") as upload:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > limit:
                        raise too_large
                    upload.write(chunk)
            token = await run_in_threadpool(storage.finish_upload, partial, name)  # hashing takes a moment
        except AdapterError as exc:
            if exc.category is not FailureCategory.INVALID_INPUT:
                raise
            raise HTTPException(status_code=415, detail={
                "code": "unsupported_file", "message": "This does not look like an audio file that can be separated"}) from exc
        except OSError as exc:
            raise (disk_full_error() if is_disk_full(exc) else
                   AdapterError("Upload could not be saved", FailureCategory.TRANSIENT)) from exc
        finally:
            partial.unlink(missing_ok=True)
        return queue_batch([SourceRequest(SourceKind.LOCAL_FILE, f"{token}/{name}")], model_name,
                           title=PureWindowsPath(name).stem or name)

    @app.get("/api/v1/batches/{batch_id}")
    def get_batch(batch_id: UUID) -> dict[str, object]:
        batch = repository.get_batch(batch_id, _OWNER_ID)
        if batch is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Batch not found"})
        jobs = repository.list_jobs_for_batch(batch_id, _OWNER_ID)
        return {
            "batch_id": str(batch.batch_id),
            "status": batch.status,
            "result_summary": batch.result_summary,
            "created_at": batch.created_at.isoformat(),
            "jobs": [_job_payload(job) for job in jobs],
        }

    @app.get("/api/v1/projects")
    def list_projects() -> list[dict[str, object]]:
        speed = rates()
        return [
            {
                "project_id": str(project.project_id),
                "input_type": project.input_type.value,
                "input_value": project.input_value,
                "source_id": project.source_id,
                "title": project.title,
                "name": project.name,
                "created_at": project.created_at.isoformat(),
                "job_id": str(project.job_id),
                "job_status": project.job_status,
                "progress": round(project.job_progress, 3),
                # Seconds until the stems are ready: the usual length before separation, then what is left.
                "eta_seconds": eta_seconds(project, speed),
            }
            for project in repository.list_projects(_OWNER_ID)
        ]

    @app.put("/api/v1/projects/order", status_code=status.HTTP_204_NO_CONTENT)
    def reorder_projects(body: ProjectOrder) -> Response:
        # The whole list, so a project added meanwhile is never left without a place: the page reloads and retries.
        if not repository.reorder_projects(_OWNER_ID, body.project_ids):
            raise HTTPException(status_code=409, detail={"code": "conflict", "message": "The project list has changed"})
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/queue")
    def get_queue() -> list[dict[str, object]]:
        """What the worker is doing and what waits, in its order: the running task first, then the songs
        waiting to be separated, then the waiting «Improve quality» (the worker takes batches first)."""
        speed = rates()
        jobs = [
            {"kind": "job", "job_id": str(project.job_id), "project_id": str(project.project_id),
             "title": project.name or project.title or project.input_value, "status": project.job_status,
             "progress": round(project.job_progress, 3), "eta_seconds": eta_seconds(project, speed)}
            for project in repository.list_projects(_OWNER_ID)
            if project.job_status not in {"completed", "failed", "cancelled"}
        ]
        upgrades = []
        for project_id in repository.active_analyses("upgrade"):
            project, job = project_and_job(UUID(project_id))
            state = upgrade_state(project, job)
            upgrades.append({"kind": "upgrade", "job_id": str(job.job_id), "project_id": project_id,
                             "title": project.name or project.title or project.input_value, "status": state["status"],
                             "progress": state["progress"], "eta_seconds": state["eta_seconds"]})
        working = {"searching", "downloading", "separating", "running"}
        return sorted(jobs + upgrades, key=lambda item: item["status"] not in working)  # stable: the order otherwise

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: UUID) -> dict[str, object]:
        project = repository.get_project(project_id, _OWNER_ID)
        if project is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        job = repository.get_job(project.job_id, _OWNER_ID)
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        options = job.separation_options
        stem_assets = [a for a in repository.list_assets(job.job_id, _OWNER_ID) if a.asset_type is MediaAssetType.STEM]
        start_seconds = job.start_seconds
        if start_seconds is None and job.status is JobStatus.COMPLETED and stem_assets:
            # Measured once, on the first open: only the stems' first seconds are read.
            paths = []
            for asset in stem_assets:
                try:
                    paths.append(storage.resolve_path(asset.storage_key))
                except AdapterError:
                    pass
            if all(path.is_file() for path in paths):
                start_seconds = storage.music_start(paths)
                repository.set_start_seconds(job.job_id, start_seconds)
        assets: list[dict[str, object]] = []
        shared = repository.shared_storage_keys(job.job_id)
        disk_bytes = shared_bytes = 0
        for asset in repository.list_assets(job.job_id, _OWNER_ID):
            try:
                path = storage.resolve_path(asset.storage_key)
                size = path.stat().st_size if path.is_file() else None
            except (AdapterError, OSError):
                size = None
            available = size is not None
            if available and asset.storage_key in shared:
                shared_bytes += size
            elif available:
                disk_bytes += size
            assets.append({
                "asset_id": str(asset.asset_id),
                "asset_type": asset.asset_type.value,
                "stem_name": asset.stem_name,
                "media_format": asset.media_format,
                "byte_size": asset.byte_size,
                "available": available,
            })
        return {
            "project_id": str(project.project_id),
            "input_type": project.input_type.value,
            "input_value": project.input_value,
            "source_id": project.source_id,
            "title": project.title,
            "name": project.name,
            "created_at": project.created_at.isoformat(),
            "job": {
                **_job_payload(job),
                "separation_options": (
                    {"model_name": options.model_name, "device": options.device}
                    if options else None
                ),
            },
            "assets": assets,
            # Leading silence the player skips: its zero is where the music starts.
            "start_seconds": start_seconds or 0.0,
            # What deleting frees, and what stays because another project uses the same files.
            "disk_bytes": disk_bytes,
            "shared_bytes": shared_bytes,
            "upgrade": upgrade_state(project, job),
        }

    @app.patch("/api/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    def rename_project(project_id: UUID, body: ProjectUpdate) -> Response:
        # Only what is shown changes: the files keep the source title's names.
        if not repository.rename_project(project_id, _OWNER_ID, body.name.strip() or None):
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/api/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_project(project_id: UUID) -> Response:
        try:
            deletion = repository.begin_project_deletion(project_id, _OWNER_ID)
        except AdapterError as exc:
            if exc.category is FailureCategory.NOT_FOUND:
                raise HTTPException(
                    status_code=404, detail={"code": "not_found", "message": "Project not found"}
                ) from exc
            if exc.category is FailureCategory.CONFLICT:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "project_processing", "message": "This project cannot be deleted while its batch is active"},
                ) from exc
            raise
        try:
            finish_deletion(deletion)
        except (AdapterError, OSError) as exc:
            logger.exception(
                "Project file cleanup remains pending",
                extra={"event": "project_cleanup_pending", "ClientID": _OWNER_ID,
                       "project_id": str(project_id), "error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=503,
                detail={"code": "cleanup_pending", "message": "Project files could not all be removed; retry delete to continue cleanup"},
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: UUID) -> dict[str, str]:
        """Stop a job: one that has not started is deleted here; a running one is stopped by the worker — deleted
        before its separation, kept with its audio after the download."""
        try:
            project_id, not_started = repository.request_job_cancel(job_id, _OWNER_ID)
        except AdapterError as exc:
            if exc.category is FailureCategory.NOT_FOUND:
                raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"}) from exc
            if exc.category is FailureCategory.CONFLICT:
                raise HTTPException(status_code=409, detail={"code": "job_finished", "message": "The job has already finished"}) from exc
            raise
        if not_started:
            try:
                finish_deletion(repository.begin_project_deletion(project_id, _OWNER_ID))
                return {"state": "deleted"}
            except AdapterError:
                pass  # the worker claimed the batch meanwhile, or it holds other songs: the worker cancels it
        return {"state": "cancelling"}

    @app.post("/api/v1/projects/{project_id}/separate", status_code=status.HTTP_202_ACCEPTED)
    def separate_again(project_id: UUID) -> dict[str, str]:
        """«Separate»: a cancelled or failed project with its source audio goes back into the queue."""
        try:
            job_id = repository.requeue_separation(project_id, _OWNER_ID)
        except AdapterError as exc:
            if exc.category is FailureCategory.NOT_FOUND:
                raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"}) from exc
            if exc.category is FailureCategory.CONFLICT:
                raise HTTPException(status_code=409, detail={"code": "not_separable", "message": str(exc)}) from exc
            raise
        return {"job_id": str(job_id)}

    @app.get("/api/v1/assets/{asset_id}/waveform")
    def get_waveform(asset_id: UUID, points: int = WAVEFORM_POINTS) -> dict[str, object]:
        asset = repository.get_asset(asset_id, _OWNER_ID)
        if asset is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Asset not found"})
        if (asset.media_format or "").lower() not in _WAVEFORM_FORMATS:
            raise HTTPException(
                status_code=415,
                detail={"code": "unsupported_waveform_format", "message": "Waveform data is available for WAV and FLAC assets"},
            )
        if points not in (WAVEFORM_POINTS, DETAIL_POINTS):
            raise HTTPException(status_code=422, detail={"code": "invalid_request", "message": "Request data is invalid"})
        key = "waveform" if points == WAVEFORM_POINTS else f"waveform_{points}"
        try:
            path = storage.resolve_path(asset.storage_key)
            if not path.is_file():
                raise FileNotFoundError
            stat = path.stat()
            job = repository.get_job(asset.job_id, _OWNER_ID)
            start = (job.start_seconds if job else None) or 0.0
            # Peaks are computed once per file version and music start, and kept in the database.
            stored = repository.get_waveform(asset.asset_id)
            fresh = (stored and stored.get("byte_size") == stat.st_size and stored.get("mtime_ns") == stat.st_mtime_ns
                     and stored.get("start_seconds", 0.0) == start)
            if fresh and key in stored:
                data = stored[key]
            else:
                data = waveform_data(path, points, start)
                if points != WAVEFORM_POINTS:
                    data["levels"] = [round(level, 3) for level in data["levels"]]  # 8192 of them: keep it small
                base = stored if fresh else {"byte_size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "start_seconds": start}
                repository.save_waveform(asset.asset_id, {**base, key: data})
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_unavailable", "message": "Asset file is not available"},
            ) from exc
        except (AdapterError, OSError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_unavailable", "message": "Asset file is not available"},
            ) from exc
        except WaveformError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_waveform_source", "message": str(exc)},
            ) from exc
        return {"asset_id": str(asset.asset_id), **data}

    @app.get("/api/v1/assets/{asset_id}")
    def stream_asset(asset_id: UUID, request: Request) -> FileResponse:
        asset = repository.get_asset(asset_id, _OWNER_ID)
        if asset is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Asset not found"})
        try:
            path = storage.resolve_path(asset.storage_key)
            if not path.is_file():
                raise FileNotFoundError
        except (AdapterError, OSError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_unavailable", "message": "Asset file is not available"},
            ) from exc
        # <audio> asks for "bytes=N-" and holds the connection while buffering; six stems then use
        # all of the browser's per-host connections and every API call stalls. Answer open-ended
        # ranges with a bounded chunk so each request finishes; the browser fetches the next one.
        open_range = re.fullmatch(r"bytes=(\d+)-", request.headers.get("range", "").strip())
        if open_range:
            start = int(open_range[1])
            request.scope["headers"] = [
                (name, value) for name, value in request.scope["headers"] if name != b"range"
            ] + [(b"range", f"bytes={start}-{start + _MEDIA_CHUNK_BYTES - 1}".encode())]
        # Starlette serves Range requests (206/416) itself.
        return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index_page(request: Request) -> HTMLResponse:
        """The page in the user's language from the first byte (version 21): the Settings choice from the cookie,
        on the first start the browser's language when its translation ships, else English."""
        offered = i18n.offered(web_root)
        code = i18n.choose(request.cookies.get(i18n.COOKIE), request.headers.get("accept-language", ""), offered)
        # Plugins run in this page (version 22): it may talk only to the app itself (both loopback names, which
        # the stems use), so no plugin can send anything out.
        loopback = " ".join(f"http://{host}:{settings.api_port}" for host in ("127.0.0.1", "localhost"))
        return HTMLResponse(i18n.page(code, web_root), headers={
            "Cache-Control": "no-cache", "Vary": "Cookie, Accept-Language",
            "Content-Security-Policy": f"connect-src 'self' {loopback}"})

    app.mount("/", _UiFiles(directory=web_root, html=True), name="web")

    return app


def quiet_connection_resets(loop, context) -> None:
    """Loop exception handler: drops the one known-harmless case and passes everything else on. On Windows the
    Proactor loop raises ConnectionResetError (WinError 10054) while closing a connection the browser has already
    reset — Chrome does that whenever the app abandons a stem (switching projects, seeking) — and asyncio printed
    it as «Exception in callback _ProactorBasePipeTransport._call_connection_lost» in the launcher's window."""
    if isinstance(context.get("exception"), ConnectionResetError) and "_call_connection_lost" in repr(context.get("handle")):
        return
    loop.default_exception_handler(context)


def main() -> None:
    import asyncio
    import uvicorn

    from audio_stems.config import configure_logging

    settings = Settings.load()
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("The local API currently supports SQLite database URLs only")
    database_path = settings.database_url.removeprefix("sqlite:///")
    repository = SqliteMetadataStore(Path(database_path))
    configure_logging(settings)
    app = create_app(repository=repository, settings=settings)

    class Server(uvicorn.Server):
        async def serve(self, sockets=None):
            asyncio.get_running_loop().set_exception_handler(quiet_connection_resets)
            await super().serve(sockets)

    Server(uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_config=None)).run()


if __name__ == "__main__":
    main()
