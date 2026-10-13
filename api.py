"""Versioned localhost HTTP API; all work is delegated to the SQLite queue."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Protocol, Sequence
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from contracts import (
    AdapterError,
    BatchReceipt,
    BatchView,
    FailureCategory,
    JobEvent,
    JobView,
    SeparationOptions,
    SourceAdapter,
    SourceRequest,
    StemSeparator,
)
from demucs_adapter import DemucsSeparator
from metadata import ProjectRecord, SqliteMetadataStore
from settings import Settings
from sources import YouTubeSourceAdapter


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
    }


def create_app(
    *,
    repository: ApiRepository,
    settings: Settings,
    source_adapter: SourceAdapter | None = None,
    separator: StemSeparator | None = None,
) -> FastAPI:
    """Create an API instance using injected local infrastructure."""
    source_adapter = source_adapter or YouTubeSourceAdapter(settings.allowed_source_hosts)
    separator = separator or DemucsSeparator()
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
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": message}})

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/models")
    def models() -> list[dict[str, object]]:
        return [
            {"name": model.name, "stems": list(model.stems), "parameters": dict(model.parameters)}
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
            }
            for project in repository.list_projects(_OWNER_ID)
        ]

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
