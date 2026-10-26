"""Validated runtime configuration (.env and environment) and structured application logging."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler


_PREFIX = "AUDIO_STEMS_"
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


_DOTENV = Path(__file__).resolve().parents[1] / ".env"
_LEGACY_FOLDER = "YouTube Audio"  # the media folder's name before version 15


def default_storage_root(home: Path | None = None) -> Path:
    """`~/Audio Stems/Music`, or the pre-version-15 folder while the launcher has not moved it yet."""
    base = (home or Path.home()) / "Audio Stems"
    legacy, music = base / _LEGACY_FOLDER, base / "Music"
    return legacy if legacy.is_dir() and not music.exists() else music


def move_legacy_storage(*, home: Path | None = None, environ: dict[str, str] | None = None,
                        dotenv_path: Path | None = None) -> bool:
    """Rename `~/Audio Stems/YouTube Audio` to `Music` unless a storage root is configured or `Music` exists.
    Storage keys are relative to the folder, so the database stays valid. A folder in use (a file open in
    another program) keeps its old name and is used from there; the next start tries again."""
    configured = {**_read_dotenv(dotenv_path or _DOTENV), **(os.environ if environ is None else environ)}
    base = (home or Path.home()) / "Audio Stems"
    legacy, music = base / _LEGACY_FOLDER, base / "Music"
    if _PREFIX + "STORAGE_ROOT" in configured or not legacy.is_dir() or music.exists():
        return False
    try:
        os.replace(legacy, music)
    except OSError as exc:
        print(f"Could not rename {legacy} to {music} ({exc}); using the old folder.", flush=True)
        return False
    print(f"Moved your songs and tracks to {music}.", flush=True)
    return True


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env key on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _positive_int(name: str, raw: str, *, allow_zero: bool = False) -> int:
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0 or (parsed == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier}")
    return parsed


def _parse_hosts(raw: str) -> tuple[str, ...]:
    hosts = tuple(host.strip().lower().rstrip(".") for host in raw.split(",") if host.strip())
    if not hosts:
        raise ValueError("AUDIO_STEMS_ALLOWED_SOURCE_HOSTS must contain at least one host")
    for host in hosts:
        labels = host.split(".")
        if any(not _HOST_LABEL.fullmatch(label) for label in labels):
            raise ValueError(f"Invalid source host in AUDIO_STEMS_ALLOWED_SOURCE_HOSTS: {host}")
    return hosts


@dataclass(frozen=True)
class Settings:
    storage_root: Path
    temp_root: Path
    database_url: str
    log_level: str
    log_file: Path
    log_max_bytes: int
    log_backup_count: int
    retry_count: int
    retry_delays: tuple[float, ...]
    allowed_source_hosts: tuple[str, ...]
    batch_limit: int
    upload_limit_mb: int
    model_name: str
    model_device: str
    api_port: int
    api_host: str

    @classmethod
    def load(
        cls,
        *,
        environ: dict[str, str] | None = None,
        dotenv_path: Path | None = None,
    ) -> "Settings":
        file_values = _read_dotenv(dotenv_path or _DOTENV)
        process_values = dict(os.environ if environ is None else environ)
        file_values.update(process_values)

        def value(key: str, default: str) -> str:
            return file_values.get(_PREFIX + key, default).strip()

        storage_root = Path(value("STORAGE_ROOT", str(default_storage_root()))).expanduser()
        temp_root = Path(value("TEMP_ROOT", str(storage_root / "_demucs_temp"))).expanduser()
        default_database = "sqlite:///" + (Path.home() / "Audio Stems" / "audio_stems.db").as_posix()

        log_level = value("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("AUDIO_STEMS_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

        raw_delays = value("RETRY_DELAYS", "3,6")
        try:
            retry_delays = tuple(float(item.strip()) for item in raw_delays.split(",") if item.strip())
        except ValueError as exc:
            raise ValueError("AUDIO_STEMS_RETRY_DELAYS must be comma-separated numbers") from exc
        if any(delay < 0 for delay in retry_delays):
            raise ValueError("AUDIO_STEMS_RETRY_DELAYS cannot contain negative values")


        database_url = value("DATABASE_URL", default_database)
        if not database_url.startswith(("sqlite:///", "postgresql://", "postgresql+")):
            raise ValueError("AUDIO_STEMS_DATABASE_URL must use sqlite or PostgreSQL")

        model_name = value("MODEL_NAME", "hdemucs_mmi")
        model_device = value("MODEL_DEVICE", "auto")
        if not model_name or not model_device:
            raise ValueError("AUDIO_STEMS_MODEL_NAME and AUDIO_STEMS_MODEL_DEVICE cannot be empty")

        return cls(
            storage_root=storage_root,
            temp_root=temp_root,
            database_url=database_url,
            log_level=log_level,
            log_file=Path(value("LOG_FILE", str(Path.home() / "Audio Stems" / "logs" / "app.jsonl"))).expanduser(),
            log_max_bytes=_positive_int("AUDIO_STEMS_LOG_MAX_BYTES", value("LOG_MAX_BYTES", str(10 * 1024 * 1024))),
            log_backup_count=_positive_int("AUDIO_STEMS_LOG_BACKUP_COUNT", value("LOG_BACKUP_COUNT", "5"), allow_zero=True),
            retry_count=_positive_int("AUDIO_STEMS_RETRY_COUNT", value("RETRY_COUNT", "3")),
            retry_delays=retry_delays,
            allowed_source_hosts=_parse_hosts(value("ALLOWED_SOURCE_HOSTS", "youtube.com,www.youtube.com,m.youtube.com,youtu.be")),
            batch_limit=_positive_int("AUDIO_STEMS_BATCH_LIMIT", value("BATCH_LIMIT", "25")),
            upload_limit_mb=_positive_int("AUDIO_STEMS_UPLOAD_LIMIT_MB", value("UPLOAD_LIMIT_MB", "500")),
            model_name=model_name,
            model_device=model_device,
            api_port=_port(value("API_PORT", "8000")),
            api_host=_host(value("API_HOST", "127.0.0.1")),
        )


def _port(raw: str) -> int:
    port = _positive_int("AUDIO_STEMS_API_PORT", raw)
    if port > 65535:
        raise ValueError("AUDIO_STEMS_API_PORT must be between 1 and 65535")
    return port


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


def _host(raw: str) -> str:
    """127.0.0.1: this computer only (the default). 0.0.0.0: also every network the computer is on — then anyone
    there can open the app, which has no login. (The launcher checks and opens 127.0.0.1, which both include.)"""
    if raw not in ("127.0.0.1", "0.0.0.0"):
        raise ValueError("AUDIO_STEMS_API_HOST must be 127.0.0.1 (this computer) or 0.0.0.0 (also the network)")
    return raw
