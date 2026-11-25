"""Usage analytics in `~/Audio Stems/analytics.db`: what was pressed, on which screen, and what failed, one row an
event, for SQL (after GA4's BigQuery export). Nothing here leaves the computer. See docs/ANALYTICS.md."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from .config import _RECORD_FIELDS, _redact, _redacted

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_timestamp INTEGER NOT NULL,  -- microseconds since 1970-01-01, UTC
  event_date TEXT NOT NULL,          -- YYYYMMDD, UTC
  event_name TEXT NOT NULL,
  level TEXT NOT NULL,               -- info | warning | error
  source TEXT NOT NULL,              -- page | server
  install_id TEXT NOT NULL,
  session_id TEXT,                   -- one page load (browser tab)
  screen TEXT,
  project_id TEXT,
  plugin TEXT,
  app_version TEXT,
  platform TEXT,
  device TEXT,                       -- desktop | phone (page events)
  params TEXT NOT NULL DEFAULT '{}'  -- JSON: read with json_extract(params, '$.control')
);
CREATE INDEX IF NOT EXISTS events_by_date ON events (event_date, event_name);
CREATE TABLE IF NOT EXISTS install (id INTEGER PRIMARY KEY CHECK (id = 1), install_id TEXT NOT NULL);
"""

# Server events worth counting besides every warning and error (those go in too, with their message).
SERVER_EVENTS = {"batch_created", "worker_started", "audio_download_reused", "job_retry_scheduled", "job_cancelled"}
_VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
_PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
_MESSAGE_CHARS = 500


def connect(path: Path) -> sqlite3.Connection:
    """The analytics database, created on first use; every process (API, worker, analysis) writes to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.execute("INSERT OR IGNORE INTO install (id, install_id) VALUES (1, ?)", (str(uuid4()),))
    return db


def _microseconds(record: logging.LogRecord) -> int:
    try:  # the page's own clock for its events (the same computer), else when the server got it
        return int(datetime.fromisoformat(record.client_time).timestamp() * 1_000_000)
    except (AttributeError, TypeError, ValueError):
        return int(record.created * 1_000_000)


class AnalyticsHandler(logging.Handler):
    """Page events (`<logger>.client`), the server's warnings and errors and the events in SERVER_EVENTS, as rows."""

    def __init__(self, path: Path):
        super().__init__()
        self.db = connect(path)
        self.install_id = self.db.execute("SELECT install_id FROM install").fetchone()[0]
        version = _VERSION_FILE.read_text(encoding="utf-8").strip() if _VERSION_FILE.is_file() else None
        self.fixed = (self.install_id, version, _PLATFORM)

    def emit(self, record: logging.LogRecord) -> None:
        page = record.name.endswith(".client")
        event = getattr(record, "event", None) or "log"
        if not page and record.levelno < logging.WARNING and event not in SERVER_EVENTS:
            return
        try:
            if page:
                params = record.details if isinstance(getattr(record, "details", None), dict) else {}
            else:  # the extra fields and a short message; the stack stays in the JSON log
                params = {key: value for key, value in vars(record).items()
                          if key not in _RECORD_FIELDS and key not in ("event", "ClientID", "project_id")}
                params["message"] = _redact(record.getMessage())[:_MESSAGE_CHARS]
                if record.exc_info and record.exc_info[0]:
                    params["error_type"] = record.exc_info[0].__name__
            stamp = _microseconds(record)
            self.db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (stamp, datetime.fromtimestamp(stamp / 1_000_000, timezone.utc).strftime("%Y%m%d"), event,
                 record.levelname.lower(), "page" if page else "server", self.fixed[0],
                 getattr(record, "session", None) or None, getattr(record, "screen", None) or None,
                 str(record.project_id) if getattr(record, "project_id", None) else None,
                 getattr(record, "plugin", None) or None, self.fixed[1], self.fixed[2],
                 getattr(record, "device", None) or None,
                 json.dumps(_redacted(params), ensure_ascii=False, default=str)))
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self.db.close()
        super().close()
