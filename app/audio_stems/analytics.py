"""Usage analytics in `~/Audio Stems/analytics.db`: what was pressed, on which screen, and what failed, one row an
event, for SQL (after GA4's BigQuery export). Nothing here leaves the computer. See docs/ANALYTICS.md."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from typing import Callable
from urllib.request import Request, urlopen
from uuid import uuid4

from .config import _RECORD_FIELDS, _redact, _redacted
from .updates import _REPOSITORY

logger = logging.getLogger("audio_stems.analytics")

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
-- GitHub's numbers for the repository, a row a day (AUDIO_STEMS_GITHUB_STATS=1). Download counts are running totals;
-- views and clones are per day (GitHub keeps 14 days); referrers are GitHub's 14-day totals as of `date`.
CREATE TABLE IF NOT EXISTS github_downloads (date TEXT NOT NULL, release TEXT NOT NULL, asset TEXT NOT NULL,
  download_count INTEGER NOT NULL, PRIMARY KEY (date, release, asset));
CREATE TABLE IF NOT EXISTS github_traffic (date TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL,
  uniques INTEGER NOT NULL, PRIMARY KEY (date, kind));
CREATE TABLE IF NOT EXISTS github_referrers (date TEXT NOT NULL, referrer TEXT NOT NULL, count INTEGER NOT NULL,
  uniques INTEGER NOT NULL, PRIMARY KEY (date, referrer));
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


def collect_github_stats(db: sqlite3.Connection, get_json: Callable[[str], object], today: str) -> bool:
    """Today's GitHub numbers into the github_* tables, once a day (False when today's are already there)."""
    if db.execute("SELECT 1 FROM github_downloads WHERE date = ?", (today,)).fetchone():
        return False
    base = f"/repos/{_REPOSITORY}"
    releases = get_json(f"{base}/releases?per_page=100")
    traffic = {kind: get_json(f"{base}/traffic/{kind}") for kind in ("views", "clones")}
    referrers = get_json(f"{base}/traffic/popular/referrers")
    db.execute("BEGIN")
    try:
        for release in releases:
            for asset in release.get("assets", []):
                db.execute("INSERT OR REPLACE INTO github_downloads VALUES (?, ?, ?, ?)",
                           (today, release["tag_name"], asset["name"], int(asset["download_count"])))
        for kind, answer in traffic.items():
            for day in answer.get(kind, []):  # the last day is still counting: replaced by the next start's
                db.execute("INSERT OR REPLACE INTO github_traffic VALUES (?, ?, ?, ?)",
                           (day["timestamp"][:10].replace("-", ""), kind, int(day["count"]), int(day["uniques"])))
        for item in referrers:
            db.execute("INSERT OR REPLACE INTO github_referrers VALUES (?, ?, ?, ?)",
                       (today, item["referrer"], int(item["count"]), int(item["uniques"])))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return True


def collect_github_stats_now(path: Path, *, run=subprocess.run, open_url=urlopen, today: str | None = None) -> None:
    """The owner's token from the GitHub CLI; without the CLI or a login nothing is collected and nothing is said."""
    try:
        token = run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        token = ""
    if not token:
        return

    def get_json(path_and_query: str) -> object:
        request = Request("https://api.github.com" + path_and_query, headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "User-Agent": "Audio-Stems-Stats"})
        with open_url(request, timeout=20) as response:
            return json.loads(response.read(5 * 1024 * 1024))

    try:
        db = connect(path)
        try:
            if collect_github_stats(db, get_json, today or datetime.now(timezone.utc).strftime("%Y%m%d")):
                logger.info("GitHub statistics collected", extra={"event": "github_stats_collected"})
        finally:
            db.close()
    except Exception as exc:  # a network or API problem: tried again at the next start
        logger.warning("GitHub statistics not collected: %s", type(exc).__name__,
                       extra={"event": "github_stats_failed", "error_type": type(exc).__name__})


def collect_github_stats_in_background(path: Path) -> None:
    threading.Thread(target=collect_github_stats_now, args=(path,), name="github-stats", daemon=True).start()
