"""Human-readable console and rotating structured JSONL application logs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re

from settings import Settings


_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|secret|cookie|authorization|api[_-]?key|access[_-]?key)\b(\s*[:=]\s*|\s+)(?:bearer\s+)?([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


def _redact(value: str) -> str:
    value = _CREDENTIAL_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=***", value)
    return _BEARER_TOKEN.sub("Bearer ***", value)


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds")
        payload: dict[str, object] = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "module": record.name,
            "event": getattr(record, "event", "log"),
            "ClientID": getattr(record, "ClientID", None),
            "batch_id": getattr(record, "batch_id", None),
            "job_id": getattr(record, "job_id", None),
            "message": _redact(record.getMessage()),
        }
        if hasattr(record, "attempt"):
            payload["attempt"] = record.attempt
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            payload["stack_trace"] = _redact(self.formatException(record.exc_info))
        elif hasattr(record, "error_type"):
            payload["error_type"] = record.error_type
        return json.dumps(payload, ensure_ascii=False)


class UtcConsoleFormatter(logging.Formatter):
    converter = staticmethod(lambda timestamp: datetime.fromtimestamp(timestamp, timezone.utc).timetuple())

    def format(self, record: logging.LogRecord) -> str:
        record.msg = _redact(record.getMessage())
        record.args = ()
        return super().format(record)

    def formatException(self, exc_info: tuple[object, object, object]) -> str:
        return _redact(super().formatException(exc_info))


def configure_logging(settings: Settings, *, logger_name: str = "audio_stems") -> logging.Logger:
    logger = logging.getLogger(logger_name)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(getattr(logging, settings.log_level))
    logger.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(UtcConsoleFormatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    logger.addHandler(console)

    log_path: Path = settings.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    json_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    json_handler.setFormatter(JsonLineFormatter())
    logger.addHandler(json_handler)
    return logger
