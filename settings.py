"""Validated runtime configuration loaded from .env and process environment."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re


_PREFIX = "AUDIO_STEMS_"
_DEFAULT_STORAGE_ROOT = Path.home() / "Audio Stems" / "Music"
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


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
    worker_concurrency: int
    model_name: str
    model_device: str
    auth_enabled: bool
    session_secret: str | None
    session_ttl_seconds: int
    api_port: int

    @classmethod
    def load(
        cls,
        *,
        environ: dict[str, str] | None = None,
        dotenv_path: Path | None = None,
    ) -> "Settings":
        file_values = _read_dotenv(dotenv_path or Path(__file__).with_name(".env"))
        process_values = dict(os.environ if environ is None else environ)
        file_values.update(process_values)

        def value(key: str, default: str) -> str:
            return file_values.get(_PREFIX + key, default).strip()

        storage_root = Path(value("STORAGE_ROOT", str(_DEFAULT_STORAGE_ROOT))).expanduser()
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

        auth_enabled_raw = value("AUTH_ENABLED", "false").lower()
        if auth_enabled_raw not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            raise ValueError("AUDIO_STEMS_AUTH_ENABLED must be a boolean")
        auth_enabled = auth_enabled_raw in {"true", "1", "yes", "on"}
        session_secret = value("SESSION_SECRET", "") or None
        if auth_enabled and not session_secret:
            raise ValueError("AUDIO_STEMS_SESSION_SECRET is required when authentication is enabled")

        database_url = value("DATABASE_URL", default_database)
        if not database_url.startswith(("sqlite:///", "postgresql://", "postgresql+")):
            raise ValueError("AUDIO_STEMS_DATABASE_URL must use sqlite or PostgreSQL")

        model_name = value("MODEL_NAME", "htdemucs_6s")
        model_device = value("MODEL_DEVICE", "cpu")
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
            worker_concurrency=_positive_int("AUDIO_STEMS_WORKER_CONCURRENCY", value("WORKER_CONCURRENCY", "1")),
            model_name=model_name,
            model_device=model_device,
            auth_enabled=auth_enabled,
            session_secret=session_secret,
            session_ttl_seconds=_positive_int("AUDIO_STEMS_SESSION_TTL_SECONDS", value("SESSION_TTL_SECONDS", "3600")),
            api_port=_port(value("API_PORT", "8000")),
        )


def _port(raw: str) -> int:
    port = _positive_int("AUDIO_STEMS_API_PORT", raw)
    if port > 65535:
        raise ValueError("AUDIO_STEMS_API_PORT must be between 1 and 65535")
    return port
