from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
from urllib.error import URLError

from audio_stems import analytics
from audio_stems.api import create_app
from audio_stems.config import Settings
from audio_stems.db import SqliteMetadataStore
from audio_stems.models import JobStatus, SourceKind, SourceRequest, YouTubeSourceAdapter
from audio_stems.separator import StemSeparator
from tests.test_api import SyncASGIClient


def build(tmp_path, send_report):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'audio_stems.db').as_posix()}",
        storage_root=tmp_path / "Music", temp_root=tmp_path / "tmp", analytics_file=tmp_path / "analytics.db",
    )
    repository = SqliteMetadataStore(tmp_path / "audio_stems.db")
    app = create_app(repository=repository, settings=settings, separator=StemSeparator(), send_report=send_report,
                     source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts))
    return SyncASGIClient(app), repository


def test_the_install_question_is_asked_once_and_sends_only_version_and_system(tmp_path):
    sent = []
    client, _ = build(tmp_path / "send", sent.append)
    question = client.get("/api/v1/reports/install").json()
    assert question["ask"] is True
    assert set(question["report"]) == {"kind", "version", "platform", "stage", "message"}
    assert client.post("/api/v1/reports", json={"kind": "install_ok"}).status_code == 204
    assert sent == [question["report"]] and client.get("/api/v1/reports/install").json()["ask"] is False

    declined = []
    client, _ = build(tmp_path / "decline", declined.append)
    assert client.post("/api/v1/reports", json={"kind": "install_ok", "send": False}).status_code == 204
    assert declined == [] and client.get("/api/v1/reports/install").json()["ask"] is False


def test_a_failed_projects_report_has_its_stage_and_error_without_title_link_or_home(tmp_path):
    sent = []
    client, repository = build(tmp_path, sent.append)
    job_id = repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "Secret Song")]).job_ids[0]
    project_id = client.get("/api/v1/projects").json()[0]["project_id"]
    assert client.get(f"/api/v1/projects/{project_id}/report").status_code == 409  # not failed

    repository.transition_job(job_id, JobStatus.DOWNLOADING, stage="download")
    repository.transition_job(job_id, JobStatus.FAILED, stage="download",
                              error_summary=f"Could not save Secret Song in {Path.home() / 'Audio Stems'}")
    preview = client.get(f"/api/v1/projects/{project_id}/report").json()
    assert preview["kind"] == "error_report" and preview["stage"] == "download"
    assert preview["message"].startswith("Could not save … in ~") and "Secret" not in preview["message"]
    assert client.post("/api/v1/reports", json={"kind": "error_report", "project_id": project_id}).status_code == 204
    assert sent == [preview]


def test_a_report_that_could_not_be_sent_is_a_clear_error_and_the_question_stays(tmp_path):
    def offline(_payload):
        raise URLError("no network")

    client, _ = build(tmp_path, offline)
    failed = client.post("/api/v1/reports", json={"kind": "install_ok"})
    assert failed.status_code == 502 and failed.json()["error"]["code"] == "report_not_sent"
    assert client.get("/api/v1/reports/install").json()["ask"] is True
    assert client.post("/api/v1/reports", json={"kind": "error_report"}).status_code == 422
    assert client.post("/api/v1/reports", json={"kind": "install_ok", "email": "x"}).status_code == 422


def test_reports_are_pulled_after_the_last_one_with_wrangler(tmp_path):
    path = tmp_path / "analytics.db"
    calls = []
    row = {"id": 7, "day": "2026-11-25", "kind": "install_ok", "version": "25", "platform": "windows", "stage": "", "message": ""}

    def wrangler(command, **kwargs):
        calls.append(command)
        rows = [row] if command[-1].endswith("id > 0 ORDER BY id") else []
        return subprocess.CompletedProcess(command, 0, "banner\n" + json.dumps([{"results": rows}]), "")

    assert analytics.pull_reports_now(path, run=wrangler, which=lambda _name: "npx") == 1
    assert analytics.pull_reports_now(path, run=wrangler, which=lambda _name: "npx") == 0
    assert calls[1][-1] == "SELECT * FROM reports WHERE id > 7 ORDER BY id"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id, kind FROM reports").fetchall() == [(7, "install_ok")]
    assert analytics.pull_reports_now(path, run=wrangler, which=lambda _name: None) == 0  # no Node: nothing happens
    failing = lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "not logged in")
    assert analytics.pull_reports_now(path, run=failing, which=lambda _name: "npx") == 0


def test_a_failed_setup_shows_its_report_and_sends_it_only_on_yes(monkeypatch, capsys):
    import run
    sent = []
    monkeypatch.setattr(analytics, "send_report", sent.append)
    error = f"ERROR: No space left on device: {Path.home() / 'x'}"
    run.offer_report("libraries", error, ask=lambda _question: "n", interactive=True)
    assert sent == []
    run.offer_report("libraries", error, ask=lambda _question: "y", interactive=True)
    assert sent[0]["kind"] == "install_failed" and sent[0]["message"] == "ERROR: No space left on device: ~" + str(Path("/x"))
    assert json.dumps(sent[0], indent=2, ensure_ascii=False) in capsys.readouterr().out  # shown as it is sent
    run.offer_report("libraries", error, ask=lambda _question: "y", interactive=False)  # no console: not asked
    assert len(sent) == 1
