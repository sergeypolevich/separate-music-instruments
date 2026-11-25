import json
import logging
import sqlite3

from audio_stems.config import Settings, configure_logging
from tests.test_api import build_client


def _logged(tmp_path, run):
    """Run `run(client)` with the app's log going to a temporary file; the records it wrote."""
    log_path = tmp_path / "logs" / "app.jsonl"
    settings = Settings.load(environ={"AUDIO_STEMS_LOG_FILE": str(log_path)}, dotenv_path=tmp_path / "missing.env")
    logger = configure_logging(settings)
    try:
        client, _ = build_client(tmp_path)
        run(client)
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_extra_fields_reach_the_json_log_redacted(tmp_path):
    log_path = tmp_path / "logs" / "app.jsonl"
    settings = Settings.load(environ={"AUDIO_STEMS_LOG_FILE": str(log_path)}, dotenv_path=tmp_path / "missing.env")
    logger = configure_logging(settings, logger_name="test_audio_stems_extra")
    try:
        logger.info("Step", extra={"event": "step", "project_id": "p1", "details": {"note": "token=hidden-one", "clicks": [1.5, None]}})
        record = json.loads(log_path.read_text(encoding="utf-8"))
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
    assert record["project_id"] == "p1"
    assert record["details"] == {"note": "token=***", "clicks": [1.5, None]}
    assert "taskName" not in record and "levelno" not in record


def test_page_events_go_to_analytics_and_only_their_errors_to_the_log(tmp_path):
    answers = []

    def run(client):
        answers.append(client.post("/api/v1/client-log", json={"events": [
            {"event": "ui_click", "at": "2026-11-17T14:25:00.000Z", "session": "abc", "screen": "settings",
             "device": "desktop", "project": "3b04b9a8-147a-4c66-a4ea-5cc2dc875fee", "details": {"control": "measure"}},
            {"event": "practice_input_failed", "level": "error", "plugin": "practice",
             "details": {"error": "NotAllowedError", "permission": "denied"}},
        ]}))
        answers.append(client.post("/api/v1/client-log", json={"events": [{"event": "Bad Name", "details": {}}]}))
        answers.append(client.post("/api/v1/client-log", json={"events": [{"event": "big", "level": "warning", "details": {"x": "a" * 20000}}]}))

    records = _logged(tmp_path, run)
    assert [answer.status_code for answer in answers] == [204, 422, 204]
    page = [record for record in records if record["module"] == "audio_stems.client"]
    assert [record["event"] for record in page] == ["practice_input_failed", "big"]  # the click: analytics only
    assert page[0]["level"] == "ERROR" and page[0]["details"]["permission"] == "denied" and page[0]["plugin"] == "practice"
    assert page[0]["ClientID"] == "local"
    assert len(page[1]["details"]["truncated"]) == 8000
    # the page's own log calls are not logged again as requests; the refused one is
    requests = [record for record in records if record["event"] == "http_request"]
    assert [(record["path"], record["status"]) for record in requests] == [("/api/v1/client-log", 422)]

    db = sqlite3.connect(tmp_path / "analytics.db")
    db.row_factory = sqlite3.Row
    rows = [dict(row) for row in db.execute("SELECT * FROM events WHERE source = 'page' ORDER BY rowid")]
    db.close()
    click = rows[0]
    assert click["event_name"] == "ui_click" and click["level"] == "info" and click["session_id"] == "abc"
    assert click["event_timestamp"] == 1_794_925_500_000_000 and click["event_date"] == "20261117"
    assert click["screen"] == "settings" and click["device"] == "desktop"
    assert click["project_id"] == "3b04b9a8-147a-4c66-a4ea-5cc2dc875fee" and json.loads(click["params"]) == {"control": "measure"}
    assert click["app_version"] and click["platform"] and len(click["install_id"]) == 36
    assert [row["event_name"] for row in rows] == ["ui_click", "practice_input_failed", "big"]
    assert rows[1]["plugin"] == "practice" and rows[1]["level"] == "error"


def test_changes_and_failed_requests_are_logged_with_their_time(tmp_path):
    def run(client):
        client.post("/api/v1/batches", json={"inputs": ["Artist - Song"]})
        client.get("/api/v1/projects")
        client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")

    requests = [record for record in _logged(tmp_path, run) if record["event"] == "http_request"]
    assert [(record["method"], record["path"], record["status"]) for record in requests] == [
        ("POST", "/api/v1/batches", 202), ("GET", "/api/v1/projects/00000000-0000-0000-0000-000000000000", 404)]
    assert all(isinstance(record["ms"], int) for record in requests)
    assert requests[1]["level"] == "WARNING"
    logging.getLogger("audio_stems").handlers.clear()
