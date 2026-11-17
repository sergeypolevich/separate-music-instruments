import json
import logging

from audio_stems.config import Settings, configure_logging
from tests.test_api import build_client


def _logged(tmp_path, run):
    """Run `run(client)` with the app's log going to a temporary file; the records it wrote."""
    log_path = tmp_path / "app.jsonl"
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
    log_path = tmp_path / "app.jsonl"
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


def test_page_events_go_to_the_log_with_their_details(tmp_path):
    answers = []

    def run(client):
        answers.append(client.post("/api/v1/client-log", json={"events": [
            {"event": "ui_click", "at": "2026-11-17T14:25:00.000Z", "session": "abc", "details": {"control": "measure"}},
            {"event": "practice_input_failed", "level": "error", "details": {"error": "NotAllowedError", "permission": "denied"}},
        ]}))
        answers.append(client.post("/api/v1/client-log", json={"events": [{"event": "Bad Name", "details": {}}]}))
        answers.append(client.post("/api/v1/client-log", json={"events": [{"event": "big", "details": {"x": "a" * 20000}}]}))

    records = _logged(tmp_path, run)
    assert [answer.status_code for answer in answers] == [204, 422, 204]
    page = [record for record in records if record["module"] == "audio_stems.client"]
    assert page[0]["event"] == "ui_click" and page[0]["details"] == {"control": "measure"}
    assert page[0]["session"] == "abc" and page[0]["client_time"] == "2026-11-17T14:25:00.000Z" and page[0]["ClientID"] == "local"
    assert page[1]["level"] == "ERROR" and page[1]["details"]["permission"] == "denied"
    assert len(page[2]["details"]["truncated"]) == 8000
    # the page's own log calls are not logged again as requests; the refused one is
    requests = [record for record in records if record["event"] == "http_request"]
    assert [(record["path"], record["status"]) for record in requests] == [("/api/v1/client-log", 422)]


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
