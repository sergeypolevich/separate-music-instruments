"""Versioned localhost HTTP API and WAV waveform peaks; all work is delegated to the SQLite queue."""
from __future__ import annotations

import logging
import re
from pathlib import Path
import subprocess
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from fastapi.staticfiles import StaticFiles

from audio_stems.models import (
    AdapterError,
    FailureCategory,
    SeparationOptions,
)
from audio_stems.separator import DemucsSeparator
from audio_stems.storage import LocalMediaStorage
from audio_stems.db import SqliteMetadataStore
from audio_stems.config import Settings
from audio_stems.models import YouTubeSourceAdapter
from audio_stems.updates import DependencyUpdater, GitHubReleaseClient, UpdateCheckError, UpdateService
import wave
import numpy as np


_OWNER_ID = "local"
# Fixed waveform resolution: enough detail for a full-width desktop mixer, small enough to store.
WAVEFORM_POINTS = 1024
_MEDIA_CHUNK_BYTES = 1024 * 1024
logger = logging.getLogger("audio_stems.api")
_ParameterValue = StrictBool | StrictInt | StrictFloat | StrictStr





class BatchSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(min_length=1, max_length=100)
    idempotency_key: Annotated[str | None, Field(max_length=200)] = None
    model_name: Annotated[str | None, Field(max_length=100)] = None
    parameters: dict[str, _ParameterValue] = Field(default_factory=dict, max_length=10)


class _UiFiles(StaticFiles):
    # Without Cache-Control browsers cache UI files heuristically and can pair a new index.html
    # with a stale app.js/app.css after an update; revalidate (cheap 304 via ETag) on every load.
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


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


def _sample_peak(data: bytes, sample_width: int) -> float:
    max_value = 1 << (sample_width * 8 - 1)
    if sample_width == 1:
        samples = np.frombuffer(data, np.uint8).astype(np.int16) - 128
    elif sample_width == 3:
        raw = np.frombuffer(data, np.uint8).reshape(-1, 3).astype(np.int32)
        samples = raw[:, 0] | raw[:, 1] << 8 | raw[:, 2] << 16
        samples = np.where(samples & 0x800000, samples - (1 << 24), samples)
    else:
        samples = np.frombuffer(data, {2: "<i2", 4: "<i4"}[sample_width])
    # max/min on the native dtype avoids a widened copy; int() keeps -min from overflowing.
    return min(1.0, max(int(samples.max()), -int(samples.min())) / max_value)


def waveform_data(path: Path, levels: int) -> dict[str, object]:
    """Return at most ``levels`` peak points without loading the full WAV."""
    if levels < 1 or levels > 2048:
        raise WaveformError("Waveform level count is outside supported bounds")
    try:
        stat = path.stat()
    except OSError as exc:
        raise WaveformError("WAV file could not be read") from exc
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            if source.getcomptype() != "NONE":
                raise WaveformError("Compressed WAV is not supported for waveform extraction")
            if not 1 <= channels <= 32 or sample_width not in {1, 2, 3, 4} or sample_rate <= 0:
                raise WaveformError("WAV encoding is not supported for waveform extraction")

            bucket_count = min(levels, frame_count)
            peaks: list[float] = []
            for bucket_index in range(bucket_count):
                frame_start = bucket_index * frame_count // bucket_count
                frame_end = (bucket_index + 1) * frame_count // bucket_count
                frames_left = frame_end - frame_start
                peak = 0.0
                while frames_left:
                    count = min(frames_left, 65536)
                    samples = source.readframes(count)
                    actual_frames = len(samples) // (channels * sample_width)
                    if actual_frames == 0:
                        raise WaveformError("WAV file ended before its declared frame count")
                    peak = max(peak, _sample_peak(samples, sample_width))
                    frames_left -= actual_frames
                peaks.append(peak)
    except WaveformError:
        raise
    except (OSError, wave.Error, EOFError) as exc:
        raise WaveformError("WAV file could not be read") from exc

    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "frame_count": frame_count,
        "duration_seconds": frame_count / sample_rate,
        "levels": peaks,
    }


def create_app(
    *,
    repository: SqliteMetadataStore,
    settings: Settings,
    source_adapter: YouTubeSourceAdapter | None = None,
    separator: DemucsSeparator | None = None,
    storage: LocalMediaStorage | None = None,
    update_service: UpdateService | None = None,
) -> FastAPI:
    """Create an API instance using injected local infrastructure."""
    source_adapter = source_adapter or YouTubeSourceAdapter(settings.allowed_source_hosts)
    separator = separator or DemucsSeparator()
    storage = storage or LocalMediaStorage(settings.storage_root, temp_root=settings.temp_root)
    application_root = Path(__file__).resolve().parents[1]
    update_service = update_service or UpdateService(
        GitHubReleaseClient(),
        DependencyUpdater(application_root),
    )
    app = FastAPI(title="Audio Stems API", version="1.0.0")
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
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/models")
    def models() -> list[dict[str, object]]:
        return [
            {
                "name": model.name,
                "stems": list(model.stems),
                "parameters": dict(model.parameters),
                "default": model.name == settings.model_name,
            }
            for model in separator.available_models()
        ]

    @app.get("/api/v1/updates")
    def check_updates() -> dict[str, object]:
        try:
            releases = update_service.check()
        except Exception as exc:
            logger.exception(
                "GitHub release check failed",
                extra={"event": "update_check_failed", "ClientID": _OWNER_ID,
                       "error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=503,
                detail={"code": "update_check_failed", "message": "Could not retrieve release information from GitHub"},
            ) from exc
        return {
            "releases": [
                {**release.__dict__, "updatable": index == 0 and update_service.can_update(release)}
                for index, release in enumerate(releases)
            ]
        }

    @app.post("/api/v1/updates/apply")
    def apply_update(body: UpdateApplyRequest) -> dict[str, object]:
        if not body.confirmed:
            raise HTTPException(
                status_code=409,
                detail={"code": "confirmation_required", "message": "Confirm the dependency update before continuing"},
            )
        try:
            release = update_service.apply(body.tag_name, confirmed=True)
        except UpdateCheckError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "update_check_failed", "message": "Could not retrieve release information from GitHub"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "release_not_available", "message": "That release is not the latest stable version"},
            ) from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.exception(
                "Confirmed dependency update failed",
                extra={"event": "dependency_update_failed", "ClientID": _OWNER_ID,
                       "tag_name": body.tag_name, "error_type": type(exc).__name__},
            )
            raise HTTPException(
                status_code=500,
                detail={"code": "dependency_update_failed", "message": "Dependency installation failed; restart was not requested"},
            ) from exc
        return {"status": "updated", "tag_name": release.tag_name, "restart_required": True}

    @app.post("/api/v1/batches", status_code=status.HTTP_202_ACCEPTED)
    def create_batch(body: BatchSubmission) -> dict[str, object]:
        if len(body.inputs) > settings.batch_limit:
            raise HTTPException(
                status_code=422,
                detail={"code": "batch_limit_exceeded", "message": "Batch exceeds the configured item limit"},
            )
        sources = [source_adapter.normalize(value) for value in body.inputs]
        options = SeparationOptions(
            body.model_name or settings.model_name,
            separator.resolve_device(settings.model_device),
            body.parameters,
        )
        try:
            separator.validate_options(options)
        except AdapterError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_model_options", "message": str(exc)},
            ) from exc
        receipt = repository.create_batch(
            _OWNER_ID, sources, body.idempotency_key, options,
        )
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
        return [
            {
                "project_id": str(project.project_id),
                "input_type": project.input_type.value,
                "input_value": project.input_value,
                "source_id": project.source_id,
                "title": project.title,
                "created_at": project.created_at.isoformat(),
                "job_id": str(project.job_id),
                "job_status": project.job_status,
            }
            for project in repository.list_projects(_OWNER_ID)
        ]

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: UUID) -> dict[str, object]:
        project = repository.get_project(project_id, _OWNER_ID)
        if project is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        job = repository.get_job(project.job_id, _OWNER_ID)
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project not found"})
        options = job.separation_options
        assets: list[dict[str, object]] = []
        for asset in repository.list_assets(job.job_id, _OWNER_ID):
            try:
                available = storage.resolve_path(asset.storage_key).is_file()
            except (AdapterError, OSError):
                available = False
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
            "created_at": project.created_at.isoformat(),
            "job": {
                **_job_payload(job),
                "separation_options": (
                    {"model_name": options.model_name, "device": options.device,
                     "parameters": dict(options.parameters)}
                    if options else None
                ),
            },
            "assets": assets,
        }

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
            for storage_key in deletion.storage_keys:
                storage.delete_asset(storage_key)
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
        repository.complete_project_deletion(project_id, _OWNER_ID)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/assets/{asset_id}/waveform")
    def get_waveform(asset_id: UUID) -> dict[str, object]:
        asset = repository.get_asset(asset_id, _OWNER_ID)
        if asset is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Asset not found"})
        if (asset.media_format or "").lower() != "wav":
            raise HTTPException(
                status_code=415,
                detail={"code": "unsupported_waveform_format", "message": "Waveform data is available for WAV assets"},
            )
        try:
            path = storage.resolve_path(asset.storage_key)
            if not path.is_file():
                raise FileNotFoundError
            stat = path.stat()
            # Peaks are computed once per file version and kept in the database.
            stored = repository.get_waveform(asset.asset_id)
            if stored and stored.get("byte_size") == stat.st_size and stored.get("mtime_ns") == stat.st_mtime_ns:
                data = stored["waveform"]
            else:
                data = waveform_data(path, WAVEFORM_POINTS)
                repository.save_waveform(
                    asset.asset_id, {"byte_size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "waveform": data}
                )
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

    web_root = Path(__file__).resolve().parents[1] / "web"

    app.mount("/", _UiFiles(directory=web_root, html=True), name="web")

    return app


def main() -> None:
    import uvicorn

    from audio_stems.config import configure_logging

    settings = Settings.load()
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("The local API currently supports SQLite database URLs only")
    database_path = settings.database_url.removeprefix("sqlite:///")
    repository = SqliteMetadataStore(Path(database_path))
    configure_logging(settings)
    app = create_app(repository=repository, settings=settings)
    uvicorn.run(app, host="127.0.0.1", port=settings.api_port, log_config=None)


if __name__ == "__main__":
    main()
