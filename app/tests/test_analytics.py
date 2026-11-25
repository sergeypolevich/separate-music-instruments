import json
import logging
import sqlite3

from audio_stems.config import Settings, configure_logging


def _rows(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY rowid")]
    finally:
        db.close()


def _configured(tmp_path, name):
    settings = Settings.load(environ={"AUDIO_STEMS_LOG_FILE": str(tmp_path / "logs" / "app.jsonl")},
                             dotenv_path=tmp_path / "missing.env")
    assert settings.analytics_file == tmp_path / "analytics.db"  # next to the logs folder, as ~/Audio Stems
    return configure_logging(settings, logger_name=name)


def _closed(logger):
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


def test_server_errors_and_counted_events_land_without_their_stack(tmp_path):
    logger = _configured(tmp_path, "test_analytics_server")
    try:
        logger.info("Batch created", extra={"event": "batch_created", "batch_id": "b1", "project_id": "p1"})
        logger.info("GET /x 200", extra={"event": "http_request"})  # not counted
        try:
            raise RuntimeError("weights missing")
        except RuntimeError:
            logger.exception("Separation failed: token=secret-1", extra={"event": "separation_failed", "job_id": "j1"})
    finally:
        _closed(logger)
    rows = _rows(tmp_path / "analytics.db")
    assert [row["event_name"] for row in rows] == ["batch_created", "separation_failed"]
    assert rows[0]["project_id"] == "p1" and rows[0]["source"] == "server" and rows[0]["level"] == "info"
    assert json.loads(rows[0]["params"])["batch_id"] == "b1"
    failed = json.loads(rows[1]["params"])
    assert rows[1]["level"] == "error" and failed["error_type"] == "RuntimeError" and failed["job_id"] == "j1"
    assert failed["message"] == "Separation failed: token=***" and "Traceback" not in rows[1]["params"]
    log = (tmp_path / "logs" / "app.jsonl").read_text(encoding="utf-8")
    assert "Traceback" in log  # the full stack stays in the JSON log


def test_the_install_id_is_made_once_and_kept(tmp_path):
    for name in ("test_analytics_one", "test_analytics_two"):
        logger = _configured(tmp_path, name)
        try:
            logger.warning("Disk low", extra={"event": "storage_cleanup_skipped"})
        finally:
            _closed(logger)
    first, second = _rows(tmp_path / "analytics.db")
    assert first["install_id"] == second["install_id"]
    assert logging.getLogger("test_analytics_two").handlers == []
