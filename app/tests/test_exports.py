"""Version 26: a mix and its take as recorded, rendered by the page, are kept as FLAC in Music/Exports/ under one
number and their folder opens."""
from dataclasses import replace
import io
import subprocess
import sys

import numpy as np
import soundfile

from audio_stems.api import create_app, reveal_file
from audio_stems.config import Settings
from audio_stems.db import SqliteMetadataStore
from audio_stems.models import SourceKind, SourceRequest, YouTubeSourceAdapter
from audio_stems.separator import StemSeparator
from audio_stems.storage import LocalMediaStorage
from tests.test_api import SyncASGIClient


def build(tmp_path, opened):
    settings = replace(
        Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"),
        database_url=f"sqlite:///{(tmp_path / 'audio_stems.db').as_posix()}",
        storage_root=tmp_path / "Music", temp_root=tmp_path / "tmp", analytics_file=tmp_path / "analytics.db",
    )
    repository = SqliteMetadataStore(tmp_path / "audio_stems.db")
    app = create_app(repository=repository, settings=settings, separator=StemSeparator(), reveal=opened.append,
                     source_adapter=YouTubeSourceAdapter(settings.allowed_source_hosts))
    repository.create_batch("local", [SourceRequest(SourceKind.SEARCH_QUERY, "My Song")])
    client = SyncASGIClient(app)
    return client, client.get("/api/v1/projects").json()[0]["project_id"], settings.storage_root


def wav(channels=2, seconds=0.5, rate=44100):
    samples = (0.5 * np.sin(np.linspace(0, 440 * 2 * np.pi * seconds, int(rate * seconds)))).astype(np.float32)
    buffer = io.BytesIO()
    soundfile.write(buffer, np.stack([samples] * channels, axis=1), rate, subtype="PCM_16", format="WAV")
    return buffer.getvalue(), samples


def test_a_mix_and_takes_are_kept_as_flac_never_overwritten_and_their_folder_opens(tmp_path):
    opened = []
    client, project_id, music = build(tmp_path, opened)
    body, samples = wav()
    names = []
    # a mix and its take, twice; a mix 2 already there (kept by hand): the next pair is 3
    for kind, number, show in (("mix", None, "false"), ("raw", 1, "true"), ("mix", None, "false"), ("raw", 2, "true")):
        query = f"kind={kind}&show={show}" + (f"&number={number}" if number else "")
        response = client.request("POST", f"/api/v1/projects/{project_id}/exports?{query}", content=body)
        assert response.status_code == 200, response.text
        assert response.json()["number"] == (number or len(names) // 2 + 1)
        names.append(response.json()["name"])
    assert names == ["My Song — mix.flac", "My Song — raw take.flac", "My Song — mix 2.flac", "My Song — raw take 2.flac"]
    assert opened == [music.resolve() / "Exports" / names[1], music.resolve() / "Exports" / names[3]]  # once a pair
    (music / "Exports" / "My Song — raw take 2.flac").rename(music / "Exports" / "My Song — raw take 3.flac")
    response = client.request("POST", f"/api/v1/projects/{project_id}/exports?kind=mix&show=false", content=body)
    assert response.json() == {"name": "My Song — mix 4.flac", "number": 4}  # free for both the mix and its take
    taken = client.request("POST", f"/api/v1/projects/{project_id}/exports?kind=raw&number=1", content=body)
    assert taken.json() == {"name": "My Song — raw take 2.flac", "number": 2}  # never over a file already there
    saved, rate = soundfile.read(opened[0], dtype="float32")
    info = soundfile.info(str(opened[0]))
    assert (info.format, info.subtype, info.channels, rate) == ("FLAC", "PCM_16", 2, 44100)
    assert np.abs(saved[:, 0] - samples).max() < 1e-3
    assert not list((tmp_path / "tmp").rglob("*.part"))  # the received body does not stay behind


def test_an_export_that_is_not_wav_or_of_no_project_is_refused(tmp_path):
    opened = []
    client, project_id, music = build(tmp_path, opened)
    refused = client.request("POST", f"/api/v1/projects/{project_id}/exports?kind=mix", content=b"RIFF not really")
    assert refused.status_code == 415 and refused.json()["error"]["code"] == "unsupported_file"
    assert client.request("POST", f"/api/v1/projects/{project_id}/exports?kind=take", content=wav()[0]).status_code == 422
    missing = client.request("POST", "/api/v1/projects/00000000-0000-0000-0000-000000000000/exports?kind=mix", content=wav()[0])
    assert missing.status_code == 404
    assert opened == [] and not (music / "Exports").exists()


def test_cleanup_and_the_disk_report_leave_the_exports_alone(tmp_path):
    storage = LocalMediaStorage(tmp_path / "Music", temp_root=tmp_path / "Music" / "_demucs_temp")
    source = tmp_path / "rendered.wav"
    source.write_bytes(wav(channels=1)[0])
    kept, number = storage.save_export(source, 'A/B: "Song"', "raw")
    assert kept.name == "A_B_ _Song_ — raw take.flac" and number == 1 and kept.parent == storage.exports_root
    assert storage.disk_report(set()) == {"projects_bytes": 0, "unused_bytes": 0, "temp_bytes": 0}
    assert storage.clean_unused(set()) == 0 and kept.is_file()


def test_explorer_gets_the_file_as_one_argument(tmp_path, monkeypatch):
    """Passed as a list, «/select,<path with spaces>» was quoted whole and Explorer opened Documents instead."""
    started = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda command, **_: started.append(command))
    path = tmp_path / "Audio Stems" / "Music" / "Exports" / "My Song — mix.flac"
    reveal_file(path)
    assert started == [f'explorer /select,"{path}"']
