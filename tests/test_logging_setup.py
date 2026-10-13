import json
from settings import Settings
from logging_setup import configure_logging


def test_json_log_has_stable_fields_and_redacts_credentials(tmp_path, capsys):
    settings = Settings.load(
        environ={"AUDIO_STEMS_LOG_FILE": str(tmp_path / "app.jsonl")},
        dotenv_path=tmp_path / "missing.env",
    )
    logger = configure_logging(settings, logger_name="test_audio_stems")
    try:
        logger.info(
            "Request token=top-secret Authorization: Bearer auth-secret api_key=key-secret",
            extra={
                "event": "audio_download_started",
                "attempt": 2,
                "job_id": "job-123",
            },
        )

        record = json.loads((tmp_path / "app.jsonl").read_text(encoding="utf-8"))
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    assert record["level"] == "INFO"
    assert record["module"] == "test_audio_stems"
    assert record["event"] == "audio_download_started"
    assert record["ClientID"] is None
    assert record["batch_id"] is None
    assert record["job_id"] == "job-123"
    assert record["attempt"] == 2
    assert record["message"] == "Request token=*** Authorization=*** api_key=***"
    assert not any(secret in json.dumps(record) for secret in ("top-secret", "auth-secret", "key-secret"))
    captured = capsys.readouterr()
    console = captured.out + captured.err
    assert not any(secret in console for secret in ("top-secret", "auth-secret", "key-secret"))
    assert record["timestamp"].endswith("Z")


def test_json_log_includes_exception_details(tmp_path):
    log_path = tmp_path / "app.jsonl"
    settings = Settings.load(
        environ={"AUDIO_STEMS_LOG_FILE": str(log_path)},
        dotenv_path=tmp_path / "missing.env",
    )
    logger = configure_logging(settings, logger_name="test_audio_stems_error")
    try:
        try:
            raise RuntimeError("token=exception-secret failure")
        except RuntimeError:
            logger.exception("Separation failed", extra={"event": "separation_failed"})
        record = json.loads(log_path.read_text(encoding="utf-8"))
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    assert record["error_type"] == "RuntimeError"
    assert "RuntimeError: token=*** failure" in record["stack_trace"]
    assert "exception-secret" not in record["stack_trace"]


def test_json_log_file_rotates_using_configured_size(tmp_path):
    log_path = tmp_path / "app.jsonl"
    settings = Settings.load(
        environ={
            "AUDIO_STEMS_LOG_FILE": str(log_path),
            "AUDIO_STEMS_LOG_MAX_BYTES": "250",
            "AUDIO_STEMS_LOG_BACKUP_COUNT": "2",
        },
        dotenv_path=tmp_path / "missing.env",
    )
    logger = configure_logging(settings, logger_name="test_audio_stems_rotation")
    try:
        logger.info("x" * 300)
        logger.info("y" * 300)
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    assert log_path.exists()
    assert (tmp_path / "app.jsonl.1").exists()
