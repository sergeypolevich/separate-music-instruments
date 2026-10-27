import errno
from pathlib import Path

import pytest

from audio_stems.models import AdapterError, FailureCategory, OperationCancelled, VideoCandidate
from audio_stems.storage import LocalMediaStorage
from audio_stems.youtube import PytubeDownloader
from audio_stems.config import Settings


class FakeStream:
    def __init__(self, subtype, data=b"\x1aE\xdf\xa3valid"):
        self.subtype = subtype
        self.abr = "128kbps"
        self.data = data
        self.download_args = None

    def download(self, *, output_path, filename):
        self.download_args = (output_path, filename)
        Path(output_path, filename).write_bytes(self.data)


class FakeStreams:
    def __init__(self, stream):
        self.stream = stream
        self.webm_only = False

    def filter(self, **kwargs):
        # Dynamic-range-compressed variants are never chosen.
        assert kwargs in ({"only_audio": True, "mime_type": "audio/webm", "is_drc": False}, {"only_audio": True, "is_drc": False})
        self.webm_only = "mime_type" in kwargs
        return self

    def order_by(self, key):
        assert key == "abr"
        return self

    def desc(self):
        return self

    def first(self):
        return self.stream if self.stream.subtype == "webm" or not self.webm_only else None


class FakeYoutube:
    def __init__(self, url, client, *, streams):
        self.requested_url = url
        self.title = "A Track"
        self.watch_url = "https://youtube.com/watch?v=canonical"
        self.video_id = "canonical"
        self.streams = streams


def test_search_adapter_returns_candidates_without_downloading(monkeypatch, tmp_path):
    candidate = type("Candidate", (), {
        "video_id": "source-1", "title": "Track", "watch_url": "https://youtube.com/watch?v=source-1"
    })()
    monkeypatch.setattr("audio_stems.youtube.Search", lambda query: type("Result", (), {"videos": [candidate]})())
    settings = Settings.load(environ={}, dotenv_path=tmp_path / "missing.env")
    downloader = PytubeDownloader(LocalMediaStorage(tmp_path / "media"), settings)


    results = downloader.search("query")

    assert results == [VideoCandidate("source-1", "Track", candidate.watch_url)]


def test_download_preserves_exact_source_url_and_selects_webm(monkeypatch, tmp_path):
    selected = FakeStream("webm")
    observed, downloaded_video = {}, {}
    monkeypatch.setattr(
        "audio_stems.youtube.YouTube",
        lambda url, client=None, **_kwargs: observed.update(url=url, client=client) or downloaded_video.setdefault(
            "video", FakeYoutube(url, client, streams=FakeStreams(selected))
        ),
    )
    settings = Settings.load(environ={}, dotenv_path=tmp_path / "missing.env")
    storage = LocalMediaStorage(tmp_path / "media")
    downloader = PytubeDownloader(storage, settings)
    exact = "https://youtu.be/direct-id?feature=share"

    downloaded = downloader.download(VideoCandidate("direct-id", "direct-id", exact))

    assert observed["url"] == exact
    assert observed["client"] is None  # pytubefix's default client (VISION_OS)
    assert downloaded_video["video"].fallback_clients == ["IOS"]  # never TV, which decrypts signatures
    assert downloaded.source.url == exact
    assert downloaded.media_format == "webm"
    assert storage.validate_audio_file(downloaded.path, "webm")
    assert selected.download_args[1].endswith(".webm")


def test_download_falls_back_to_audio_only_when_webm_is_missing(monkeypatch, tmp_path):
    fallback = FakeStream("m4a", data=b"0000ftypM4A ")
    monkeypatch.setattr(
        "audio_stems.youtube.YouTube",
        lambda url, client=None, **_kwargs: FakeYoutube(url, client, streams=FakeStreams(fallback)),
    )
    settings = Settings.load(environ={}, dotenv_path=tmp_path / "missing.env")
    downloader = PytubeDownloader(LocalMediaStorage(tmp_path / "media"), settings)

    downloaded = downloader.download(VideoCandidate("id", "Track", "https://youtube.com/watch?v=id"))

    assert downloaded.media_format == "m4a"
    assert downloaded.path.suffix == ".m4a"


def test_invalid_existing_audio_is_quarantined_and_downloaded_again(monkeypatch, tmp_path):
    fresh = FakeStream("webm")
    monkeypatch.setattr(
        "audio_stems.youtube.YouTube",
        lambda url, client=None, **_kwargs: FakeYoutube(url, client, streams=FakeStreams(fresh)),
    )
    settings = Settings.load(environ={}, dotenv_path=tmp_path / "missing.env")
    storage = LocalMediaStorage(tmp_path / "media", temp_root=tmp_path / "temp")
    broken = storage.audio_target("A Track", "webm", "canonical").path
    broken.write_bytes(b"not webm")

    downloaded = PytubeDownloader(storage, settings).download(
        VideoCandidate("canonical", "A Track", "https://youtube.com/watch?v=canonical")
    )

    assert fresh.download_args is not None
    assert downloaded.path.read_bytes() == fresh.data
    assert [p.read_bytes() for p in (tmp_path / "temp" / "_recovery").iterdir()] == [b"not webm"]


def test_a_full_disk_during_download_is_reported_instead_of_retried(monkeypatch, tmp_path):
    class FullDiskStream(FakeStream):
        def download(self, *, output_path, filename):
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(
        "audio_stems.youtube.YouTube",
        lambda url, client=None, **_kwargs: FakeYoutube(url, client, streams=FakeStreams(FullDiskStream("webm"))),
    )
    settings = Settings.load(environ={}, dotenv_path=tmp_path / "missing.env")
    downloader = PytubeDownloader(LocalMediaStorage(tmp_path / "media"), settings)

    with pytest.raises(AdapterError) as error:
        downloader.download(VideoCandidate("id", "Track", "https://youtube.com/watch?v=id"))

    assert error.value.category is FailureCategory.DISK_FULL and not error.value.retryable


def test_download_reports_byte_progress(monkeypatch, tmp_path):
    observed = {}

    def youtube(url, client=None, on_progress_callback=None):
        observed["callback"] = on_progress_callback
        return FakeYoutube(url, client, streams=FakeStreams(FakeStream("webm")))

    monkeypatch.setattr("audio_stems.youtube.YouTube", youtube)
    settings = Settings.load(environ={}, dotenv_path=tmp_path / "missing.env")
    seen = []
    PytubeDownloader(LocalMediaStorage(tmp_path / "media"), settings).download(
        VideoCandidate("id", "Track", "https://youtube.com/watch?v=id"), progress=seen.append
    )

    observed["callback"](type("Stream", (), {"filesize": 400})(), b"", 100)
    assert seen == [0.75]


def test_pytubefix_still_has_the_fallback_list_the_adapter_narrows():
    import inspect
    from pytubefix import YouTube, innertube

    assert "self.fallback_clients" in inspect.getsource(YouTube.__init__)  # renamed on an update → this fails
    clients = innertube._default_clients
    assert not clients["IOS"]["require_js_player"] and not clients["VISION_OS"]["require_js_player"]


def test_a_cancel_during_the_download_removes_the_half_file_and_is_not_retried(monkeypatch, tmp_path):
    class HalfStream(FakeStream):
        def download(self, *, output_path, filename):
            Path(output_path, filename).write_bytes(b"\x1aE\xdf\xa3half")
            raise OperationCancelled()  # what the progress callback raises when the user cancels

    monkeypatch.setattr("audio_stems.youtube.YouTube",
                        lambda url, **_kwargs: FakeYoutube(url, None, streams=FakeStreams(HalfStream("webm"))))
    storage = LocalMediaStorage(tmp_path / "media")
    downloader = PytubeDownloader(storage, Settings.load(environ={}, dotenv_path=tmp_path / "missing.env"))

    with pytest.raises(OperationCancelled):
        downloader.download(VideoCandidate("canonical", "A Track", "https://youtube.com/watch?v=canonical"))

    assert not list((tmp_path / "media").rglob("*.webm"))
