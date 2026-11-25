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


class _Answer:
    def __init__(self, data):
        self.body = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _limit):
        return self.body


_GITHUB = {
    "releases?per_page=100": [{"tag_name": "v24", "assets": [{"name": "Audio-Stems-Windows.zip", "download_count": 7},
                                                             {"name": "Audio-Stems-macOS.zip", "download_count": 2}]},
                              {"tag_name": "v23", "assets": []}],
    "traffic/views": {"views": [{"timestamp": "2026-11-23T00:00:00Z", "count": 10, "uniques": 6},
                                {"timestamp": "2026-11-24T00:00:00Z", "count": 3, "uniques": 1}]},
    "traffic/clones": {"clones": [{"timestamp": "2026-11-24T00:00:00Z", "count": 153, "uniques": 51}]},
    "traffic/popular/referrers": [{"referrer": "github.com", "count": 4, "uniques": 2}],
}


def _fake_github(asked):
    def open_url(request, timeout):
        asked.append((request.full_url, request.get_header("Authorization")))
        return _Answer(_GITHUB[request.full_url.split("/separate-music-instruments/")[1]])
    return open_url


def _token(*_args, **_kwargs):
    from subprocess import CompletedProcess
    return CompletedProcess([], 0, stdout="gho_secret\n")


def test_github_numbers_are_collected_once_a_day(tmp_path):
    from audio_stems.analytics import collect_github_stats_now

    asked = []
    path = tmp_path / "analytics.db"
    collect_github_stats_now(path, run=_token, open_url=_fake_github(asked), today="20261125")
    collect_github_stats_now(path, run=_token, open_url=_fake_github(asked), today="20261125")  # again: skipped
    assert len(asked) == 4 and all(header == "Bearer gho_secret" for _, header in asked)
    db = sqlite3.connect(path)
    try:
        assert sorted(db.execute("SELECT * FROM github_downloads").fetchall()) == [
            ("20261125", "v24", "Audio-Stems-Windows.zip", 7), ("20261125", "v24", "Audio-Stems-macOS.zip", 2)]
        assert sorted(db.execute("SELECT * FROM github_traffic").fetchall()) == [
            ("20261123", "views", 10, 6), ("20261124", "clones", 153, 51), ("20261124", "views", 3, 1)]
        assert db.execute("SELECT * FROM github_referrers").fetchall() == [("20261125", "github.com", 4, 2)]
    finally:
        db.close()
    collect_github_stats_now(path, run=_token, open_url=_fake_github(asked), today="20261126")
    assert len(asked) == 8  # the next day collects again


def test_github_numbers_without_a_token_or_with_a_failure_are_skipped(tmp_path, caplog):
    from subprocess import CompletedProcess
    from audio_stems.analytics import collect_github_stats_now

    def no_cli(*_args, **_kwargs):
        raise FileNotFoundError("gh")

    def broken(request, timeout):
        raise OSError("network down")

    path = tmp_path / "analytics.db"
    collect_github_stats_now(path, run=no_cli, open_url=broken)
    collect_github_stats_now(path, run=lambda *a, **k: CompletedProcess([], 1, stdout=""), open_url=broken)
    assert not path.exists()  # nothing asked, nothing made
    with caplog.at_level(logging.WARNING, logger="audio_stems.analytics"):
        collect_github_stats_now(path, run=_token, open_url=broken)
    assert [record.event for record in caplog.records] == ["github_stats_failed"]
    assert "gho_secret" not in caplog.text
