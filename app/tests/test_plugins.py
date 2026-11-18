"""Plugins (version 22): the store, installing, per-project data, the page's security policy, chords."""
import asyncio
import json
from uuid import uuid4

import httpx
import numpy as np
import soundfile

from audio_stems import analysis, chords, i18n, plugins
from audio_stems.models import MediaAsset, MediaAssetType
from tests.test_analysis import add_song

WEB = i18n.WEB


def test_the_store_lists_the_plugins_with_valid_manifests():
    found = plugins.catalog(WEB)
    assert list(found) == ["zoom", "beat-grid", "trim", "eq", "chords", "tuner", "practice"]
    assert found["eq"]["permissions"] == ["sound"] and found["trim"]["permissions"] == ["playback"]
    assert found["tuner"]["permissions"] == ["tone"] and found["practice"]["permissions"] == ["input", "analysis"]
    assert found["zoom"]["permissions"] == []
    assert found["practice"]["storage_mb"] == 100 and "storage_mb" not in found["eq"]


def test_every_plugin_has_a_readme_named_as_its_manifest_that_tells_its_permissions():
    """Version 24: the store's «Read more» shows the plugin folder's README.md."""
    labels = {"sound": "Changes the sound while playing", "playback": "Changes what is played", "tone": "Plays its own sound",
              "input": "Records from your audio input", "analysis": "Runs an analysis on this computer"}
    for plugin_id, manifest in plugins.catalog(WEB).items():
        text = (WEB / "plugins" / plugin_id / "README.md").read_text(encoding="utf-8")
        assert text.splitlines()[0] == f"# {manifest['name']}", plugin_id
        permissions = text.split("## Permissions", 1)[1]
        assert all(labels[name] in permissions for name in manifest["permissions"]), plugin_id
        assert manifest["permissions"] or "None." in permissions, plugin_id


def _app(tmp_path):
    from dataclasses import replace

    from audio_stems.api import create_app
    from audio_stems.config import Settings
    from audio_stems.db import SqliteMetadataStore
    from audio_stems.storage import LocalMediaStorage

    settings = replace(Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"), storage_root=tmp_path / "media")
    repository = SqliteMetadataStore(tmp_path / "plugins.db")
    storage = LocalMediaStorage(settings.storage_root)
    queued = []
    app = create_app(repository=repository, settings=settings, storage=storage, start_analysis=lambda: queued.append(1))

    def call(method, path, body=None, content=None):
        async def go():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                return await client.request(method, path, json=body, content=content)
        return asyncio.run(go())

    return call, repository, storage, queued, settings


def test_install_turn_off_on_report_an_error_and_remove(tmp_path):
    call, repository, storage, _queued, _settings = _app(tmp_path)
    listed = call("GET", "/api/v1/plugins").json()
    assert [item["id"] for item in listed] == ["zoom", "beat-grid", "trim", "eq", "chords", "tuner", "practice"]
    assert not any(item["installed"] for item in listed)

    assert call("PUT", "/api/v1/plugins/trim", {"enabled": True}).json()["enabled"] is True
    project_id = add_song(repository, storage)
    data = f"/api/v1/projects/{project_id}/plugins/trim"
    assert call("GET", data).json() == {"data": {}}
    assert call("PUT", data, {"data": {"start": 10.5, "end": 60}}).status_code == 204
    assert call("GET", data).json() == {"data": {"start": 10.5, "end": 60}}
    assert call("PUT", data, {"data": {"x": "y" * 70_000}}).status_code == 413

    # The page reports an error: the plugin is off with its message; turning it on clears it; the data stays.
    failed = call("POST", "/api/v1/plugins/trim/error", {"message": "boom"}).json()
    assert (failed["installed"], failed["enabled"], failed["error"]) == (True, False, "boom")
    assert call("PUT", "/api/v1/plugins/trim", {"enabled": True}).json()["error"] is None
    assert call("GET", data).json()["data"]["start"] == 10.5

    # Settings are the plugin's, for every project: stored, listed, refused when too large or not installed.
    assert call("GET", "/api/v1/plugins").json()[2]["settings"] == {}
    assert call("PUT", "/api/v1/plugins/trim/settings", {"settings": {"size": 1.2}}).json()["settings"] == {"size": 1.2}
    assert {item["id"]: item for item in call("GET", "/api/v1/plugins").json()}["trim"]["settings"] == {"size": 1.2}
    assert call("PUT", "/api/v1/plugins/trim/settings", {"settings": {"x": "y" * 70_000}}).status_code == 413
    assert call("PUT", "/api/v1/plugins/chords/settings", {"settings": {}}).status_code == 409
    assert call("PUT", "/api/v1/plugins/evil/settings", {"settings": {}}).status_code == 404

    # Removing it deletes its data in every project and its settings; another project's plugin is untouched.
    call("PUT", "/api/v1/plugins/eq", {"enabled": False})
    assert call("DELETE", "/api/v1/plugins/trim").status_code == 204
    assert call("GET", data).status_code == 409
    assert repository.get_plugin_data("trim", project_id) == {}
    states = {item["id"]: item for item in call("GET", "/api/v1/plugins").json()}
    assert not states["trim"]["installed"] and states["eq"]["installed"] and not states["eq"]["enabled"]
    assert states["trim"]["settings"] == {} and call("PUT", "/api/v1/plugins/trim", {"enabled": True}).json()["settings"] == {}

    # Only the store's plugins: an unknown id, or a path in it, is refused.
    assert call("PUT", "/api/v1/plugins/evil", {"enabled": True}).status_code == 404
    assert call("PUT", "/api/v1/plugins/..%2Fapi", {"enabled": True}).status_code in (404, 405)  # never another route
    assert call("POST", "/api/v1/plugins/eq/error", {"message": "x" * 501}).status_code == 422


def test_plugin_data_goes_with_the_project(tmp_path):
    call, repository, storage, _queued, _settings = _app(tmp_path)
    call("PUT", "/api/v1/plugins/eq", {"enabled": True})
    project_id = add_song(repository, storage)
    call("PUT", f"/api/v1/projects/{project_id}/plugins/eq", {"data": {"stems": {}}})
    with repository._connection() as connection:
        connection.execute("DELETE FROM projects WHERE id = ?", (str(project_id),))
        assert connection.execute("SELECT COUNT(*) FROM plugin_data").fetchone()[0] == 0


def test_the_page_may_talk_only_to_the_app(tmp_path):
    call, _repository, _storage, _queued, settings = _app(tmp_path)
    policy = call("GET", "/").headers["content-security-policy"]
    app_only = f"'self' http://127.0.0.1:{settings.api_port} http://localhost:{settings.api_port}"
    assert policy == f"connect-src {app_only}; img-src 'self' data:; media-src {app_only} blob:; form-action 'self'"
    assert call("GET", "/plugins/eq/plugin.mjs").status_code == 200  # the modules are page files


def test_detailed_peaks_for_the_zoom_are_stored_next_to_the_normal_ones(tmp_path):
    call, repository, storage, _queued, _settings = _app(tmp_path)
    add_song(repository, storage)
    asset = next(a for a in repository.list_assets(repository.list_projects("local")[-1].job_id, "local")
                 if a.asset_type is MediaAssetType.STEM)
    normal = call("GET", f"/api/v1/assets/{asset.asset_id}/waveform").json()
    detailed = call("GET", f"/api/v1/assets/{asset.asset_id}/waveform?points=8192").json()
    assert len(normal["levels"]) == 1024 and len(detailed["levels"]) == 8192
    stored = repository.get_waveform(asset.asset_id)
    assert len(stored["waveform"]["levels"]) == 1024 and len(stored["waveform_8192"]["levels"]) == 8192
    assert call("GET", f"/api/v1/assets/{asset.asset_id}/waveform?points=5000").status_code == 422


def _tone(midis, seconds, rate=chords.RATE):
    t = np.arange(int(seconds * rate)) / rate
    return sum(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t * h) / h for m in midis for h in (1, 2, 3))


def test_chords_of_a_simple_progression():
    signal = np.concatenate([_tone([48, 60, 64, 67], 2), _tone([45, 57, 60, 64], 2), _tone([43, 55, 59, 62], 2),
                             _tone([41, 57, 60, 65], 2)]).astype(np.float32)
    found = chords.detect(signal)
    assert [span["chord"] for span in found] == ["C", "Am", "G", "F"]
    assert [round(span["start"]) for span in found] == [0, 2, 4, 6]
    on_beats = chords.detect(signal, beats=[i * 0.5 for i in range(16)])
    assert [span["chord"] for span in on_beats] == ["C", "Am", "G", "F"]
    assert chords.detect(np.zeros(chords.RATE * 3, np.float32)) == []  # silence: no chord


def test_chords_are_found_in_the_background_from_the_stems_without_vocals_and_drums(tmp_path):
    call, repository, storage, queued, _settings = _app(tmp_path)
    project_id = add_song(repository, storage)  # a vocals stem only, 1.25 s of silence before the music
    assert call("POST", f"/api/v1/projects/{project_id}/chords").status_code == 409  # nothing but vocals
    job_id = repository.list_projects("local")[-1].job_id
    bass = storage.stem_file_target("Song", "bass", "song-1")
    soundfile.write(bass.path, np.concatenate([np.zeros(int(1.25 * 8000)), np.ones(8000)]).astype(np.float32), 8000)
    repository.add_asset(MediaAsset(uuid4(), job_id, MediaAssetType.STEM, bass.storage_key, 4, "bass", "flac"))

    assert call("POST", f"/api/v1/projects/{project_id}/chords").status_code == 202 and queued
    seen = []

    def fake_detect(request):
        seen.append(request)
        return {"chords": [{"start": 0.0, "end": 2.0, "chord": "Am"}], "algorithm": chords.ALGORITHM}

    analysis.run_queue(repository, fake_detect, kind="chords", tick=0.01)
    assert [path.rsplit("bass", 1)[0] != path for path in seen[0]["stem_paths"]] == [True]
    assert seen[0]["start_seconds"] == 1.25
    state = call("GET", f"/api/v1/projects/{project_id}/chords").json()
    assert state["status"] == "done" and state["result"]["chords"][0]["chord"] == "Am"
    assert call("POST", f"/api/v1/projects/{project_id}/chords").status_code == 200  # found once


def test_detect_chords_reads_the_stems_in_song_time(tmp_path, monkeypatch):
    rate = chords.RATE
    lead = np.zeros(rate, np.float32)  # one second before the music
    tone = np.concatenate([lead, _tone([45, 57, 60, 64], 3).astype(np.float32)])
    monkeypatch.setattr(analysis, "decode_mono", lambda path, rate: tone if path.name == "a.flac" else tone[: rate * 2] * 0.5)
    result = analysis.detect_chords({"stem_paths": [str(tmp_path / "a.flac"), str(tmp_path / "b.flac")], "start_seconds": 1.0})
    assert result["algorithm"] == chords.ALGORITHM
    assert [span["chord"] for span in result["chords"]] == ["Am"]
    assert result["chords"][0]["start"] == 0.0 and result["chords"][0]["end"] <= 3.0


def test_plugin_texts_are_in_the_translation_catalog():
    catalog = json.loads((WEB / "i18n" / "catalog.json").read_text(encoding="utf-8"))
    blocks = {block["block"]: [element["text"] for element in block["elements"]] for block in catalog["blocks"]}
    assert blocks["plugin: eq"][:2] == [plugins.catalog(WEB)["eq"]["name"], plugins.catalog(WEB)["eq"]["description"]]
    assert "Find chords" in blocks["plugin: chords"] and "Trim the start" in blocks["plugin: trim"]


def test_a_plugin_keeps_files_per_project_within_its_space(tmp_path, monkeypatch):
    """Version 24: the practice plugin's takes. Up to the manifest's storage_mb in all; own file names only; kept
    away from the storage cleanup; deleted with the project and with the plugin."""
    catalog = plugins.catalog
    monkeypatch.setattr(plugins, "catalog", lambda web: {key: ({**value, "storage_mb": 1} if key == "practice" else value)
                                                          for key, value in catalog(web).items()})
    call, repository, storage, _queued, _settings = _app(tmp_path)
    project_id = add_song(repository, storage)
    files = f"/api/v1/projects/{project_id}/plugins/practice/files"
    assert call("GET", files).status_code == 409  # not installed
    call("PUT", "/api/v1/plugins/practice", {"enabled": True})
    call("PUT", "/api/v1/plugins/eq", {"enabled": True})
    assert call("GET", files).json() == {"files": [], "limit_bytes": 2**20}
    assert call("GET", f"/api/v1/projects/{project_id}/plugins/eq/files").status_code == 409  # eq keeps no files

    assert call("PUT", f"{files}/take-1.wav", content=b"RIFF" + b"\0" * 600_000).status_code == 204
    assert call("GET", f"{files}/take-1.wav").content[:4] == b"RIFF"
    assert call("PUT", f"{files}/take-2.wav", content=b"\0" * 500_000).status_code == 413  # 1 MB in all
    assert call("PUT", f"{files}/take-1.wav", content=b"\0" * 900_000).status_code == 204  # replacing counts once
    for bad in ("..%2Fx.wav", "Take.wav", "take", ".hidden.wav", "a%5Cb.wav"):
        assert call("PUT", f"{files}/{bad}", content=b"x").status_code in (400, 404, 405), bad  # never written
    assert call("GET", f"{files}/take-9.wav").status_code == 404
    assert call("GET", files).json()["files"] == [{"name": "take-1.wav", "bytes": 900_000}]
    path = storage.plugin_file("practice", project_id, "take-1.wav")
    assert path == storage.root / "Plugins" / "practice" / str(project_id) / "take-1.wav"

    # The cleanup of unused files leaves them, and the disk report counts them with the projects.
    assert storage.disk_report(repository.storage_keys_in_use())["unused_bytes"] == 0
    storage.clean_unused(repository.storage_keys_in_use())
    assert path.is_file()

    assert call("DELETE", f"{files}/take-1.wav").status_code == 204 and not path.exists()
    call("PUT", f"{files}/take-1.wav", content=b"x")
    other = add_song(repository, storage, with_source=False)
    call("PUT", f"/api/v1/projects/{other}/plugins/practice/files/take-1.wav", content=b"y")
    assert call("DELETE", f"/api/v1/projects/{project_id}").status_code == 204  # the project's files go with it
    assert not storage.plugin_folder("practice", project_id).exists()
    assert storage.plugin_files("practice", other) == {"take-1.wav": 1}
    assert call("DELETE", "/api/v1/plugins/practice").status_code == 204  # and all of them with the plugin
    assert not (storage.root / "Plugins" / "practice").exists()
