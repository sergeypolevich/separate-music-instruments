import pytest

from audio_stems.models import AdapterError, FailureCategory, SourceKind
from audio_stems.models import YouTubeSourceAdapter


@pytest.fixture
def source_adapter():
    return YouTubeSourceAdapter(("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"))


def test_search_query_is_normalized_without_rewriting(source_adapter):
    query = "  Artist — Song title  "

    request = source_adapter.normalize(query)

    assert request.kind is SourceKind.SEARCH_QUERY
    assert request.value == "Artist — Song title"


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/watch?v=video-123",
        "https://youtu.be/video-123?feature=share",
        "https://m.youtube.com/shorts/video-123",
        "https://www.youtube.com/embed/video-123",
    ],
)
def test_supported_video_urls_are_preserved_exactly(source_adapter, url):
    request = source_adapter.normalize(url)

    assert request.kind is SourceKind.VIDEO_URL
    assert request.value == url


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=video-123",
        "ftp://youtube.com/watch?v=video-123",
        "https://youtube.com.evil.example/watch?v=video-123",
        "https://youtube.com@evil.example/watch?v=video-123",
        "https://youtube.com:444/watch?v=video-123",
        "https://youtube.com/results?search_query=music",
        "https://youtube.com/watch?v=",
        "www.youtube.com/watch?v=video-123",
        "evil.example/watch?v=video-123",
        "https://[::1/watch?v=video-123",
    ],
)
def test_unsupported_video_urls_are_rejected(source_adapter, url):
    with pytest.raises(AdapterError) as error:
        source_adapter.normalize(url)

    assert error.value.category is FailureCategory.INVALID_INPUT
