"""Versioned localhost HTTP API; all work is delegated to the SQLite queue."""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Annotated, Iterator, Protocol, Sequence
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from fastapi.staticfiles import StaticFiles

from contracts import (
    AdapterError,
    BatchReceipt,
    BatchView,
    FailureCategory,
    JobEvent,
    JobView,
    MediaAsset,
    MediaStorage,
    SeparationOptions,
    SourceAdapter,
    SourceRequest,
    StemSeparator,
)
from demucs_adapter import DemucsSeparator
from local_storage import LocalMediaStorage
from metadata import ProjectDeletion, ProjectRecord, SqliteMetadataStore
from settings import Settings
from sources import YouTubeSourceAdapter
from waveform import WaveformError, waveform_data


_OWNER_ID = "local"
logger = logging.getLogger("audio_stems.api")
_ParameterValue = StrictBool | StrictInt | StrictFloat | StrictStr


class ApiRepository(Protocol):
    def create_batch(self, owner_id: str, sources: Sequence[SourceRequest], idempotency_key: str | None,
                     separation_options: SeparationOptions | None = None) -> BatchReceipt: ...
    def get_batch(self, batch_id: UUID, owner_id: str) -> BatchView | None: ...
    def list_batches(self, owner_id: str, limit: int = 50) -> Sequence[BatchView]: ...
    def list_jobs_for_batch(self, batch_id: UUID, owner_id: str) -> Sequence[JobView]: ...
    def get_job(self, job_id: UUID, owner_id: str) -> JobView | None: ...
    def list_events(self, job_id: UUID, owner_id: str) -> Sequence[JobEvent]: ...
    def list_projects(self, owner_id: str) -> Sequence[ProjectRecord]: ...
    def get_project(self, project_id: UUID, owner_id: str) -> ProjectRecord | None: ...
    def list_assets(self, job_id: UUID, owner_id: str) -> Sequence[MediaAsset]: ...
    def get_asset(self, asset_id: UUID, owner_id: str) -> MediaAsset | None: ...
    def begin_project_deletion(self, project_id: UUID, owner_id: str) -> ProjectDeletion: ...
    def complete_project_deletion(self, project_id: UUID, owner_id: str) -> None: ...


class BatchSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(min_length=1, max_length=100)
    idempotency_key: Annotated[str | None, Field(max_length=200)] = None
    model_name: Annotated[str | None, Field(max_length=100)] = None
    device: Annotated[str | None, Field(max_length=50)] = None
    parameters: dict[str, _ParameterValue] = Field(default_factory=dict, max_length=10)


def _job_payload(job) -> dict[str, object]:
    return {
        "job_id": str(job.job_id),
        "status": job.status.value,
        "current_stage": job.current_stage,
        "retry_count": job.retry_count,
        "error_summary": job.error_summary,
        "input_value": job.input_value,
    }


def _parse_byte_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if value is None:
        return 0, max(0, size - 1), False
    try:
        unit, interval = value.strip().split("=", 1)
        if unit != "bytes" or "," in interval or size <= 0:
            raise ValueError
        first, last = interval.split("-", 1)
        if not first:
            suffix_length = int(last)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, size - suffix_length)
            end = size - 1
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
            if start < 0 or end < start or start >= size:
                raise ValueError
        return start, end, True
    except (ValueError, TypeError) as exc:
        raise ValueError("Requested byte range is not satisfiable") from exc


def _file_chunks(path: Path, start: int, length: int, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    with path.open("rb") as source:
        source.seek(start)
        remaining = length
        while remaining:
            chunk = source.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def create_app(
    *,
    repository: ApiRepository,
    settings: Settings,
    source_adapter: SourceAdapter | None = None,
    separator: StemSeparator | None = None,
    storage: MediaStorage | None = None,
) -> FastAPI:
    """Create an API instance using injected local infrastructure."""
    source_adapter = source_adapter or YouTubeSourceAdapter(settings.allowed_source_hosts)
    separator = separator or DemucsSeparator()
    storage = storage or LocalMediaStorage(settings.storage_root, temp_root=settings.temp_root)
    app = FastAPI(title="Audio Stems API", version="1.0.0")

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
            body.device or settings.model_device,
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

    @app.get("/api/v1/batches")
    def list_batches(limit: int = Query(default=50, ge=1, le=100)) -> list[dict[str, object]]:
        return [
            {
                "batch_id": str(batch.batch_id),
                "status": batch.status,
                "submission_method": batch.submission_method,
                "result_summary": batch.result_summary,
                "created_at": batch.created_at.isoformat(),
            }
            for batch in repository.list_batches(_OWNER_ID, limit)
        ]

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

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: UUID) -> dict[str, object]:
        job = repository.get_job(job_id, _OWNER_ID)
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"})
        return _job_payload(job)

    @app.get("/api/v1/jobs/{job_id}/events")
    def get_events(job_id: UUID) -> list[dict[str, object]]:
        if repository.get_job(job_id, _OWNER_ID) is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"})
        return [
            {
                "event_id": str(event.event_id),
                "name": event.name,
                "occurred_at": event.occurred_at.isoformat(),
                "details": dict(event.details),
            }
            for event in repository.list_events(job_id, _OWNER_ID)
        ]

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
    def get_waveform(asset_id: UUID, levels: int = Query(default=512, ge=1, le=2048)) -> dict[str, object]:
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
            data = waveform_data(path, levels)
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
    def stream_asset(asset_id: UUID, request: Request):
        asset = repository.get_asset(asset_id, _OWNER_ID)
        if asset is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Asset not found"})
        try:
            path = storage.resolve_path(asset.storage_key)
            size = path.stat().st_size
            if not path.is_file():
                raise FileNotFoundError
        except (AdapterError, OSError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_unavailable", "message": "Asset file is not available"},
            ) from exc
        range_value = request.headers.get("range")
        try:
            start, end, partial = _parse_byte_range(range_value, size)
        except ValueError as exc:
            raise HTTPException(
                status_code=416,
                detail={"code": "invalid_range", "message": str(exc)},
                headers={"Content-Range": f"bytes */{size}"},
            ) from exc
        length = end - start + 1 if size else 0
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "X-Content-Type-Options": "nosniff",
        }
        response_status = 200
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            response_status = 206
        return StreamingResponse(
            _file_chunks(path, start, length),
            status_code=response_status,
            media_type=media_type,
            headers=headers,
        )

    web_root = Path(__file__).with_name("web")

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(web_root / "index.html")

    app.mount("/static", StaticFiles(directory=web_root), name="static")

    return app


def main() -> None:
    import uvicorn

    from logging_setup import configure_logging

    settings = Settings.load()
    if not settings.database_url.startswith("sqlite:///"):
        raise ValueError("The local API currently supports SQLite database URLs only")
    database_path = settings.database_url.removeprefix("sqlite:///")
    repository = SqliteMetadataStore(Path(database_path))
    configure_logging(settings)
    app = create_app(repository=repository, settings=settings)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_config=None)


if __name__ == "__main__":
    main()
