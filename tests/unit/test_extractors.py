"""Extraction across modalities, against golden fixtures.

The failure test matters most: a corrupt input must mark the source failed, not
raise past the pipeline and take the worker down with it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmwiki.extractors.base import ExtractionError, detect_modality, get_extractor
from llmwiki.extractors.pdf import PdfExtractor
from llmwiki.extractors.web import WebExtractor, title_from_html
from llmwiki.extractors.youtube import (
    YouTubeExtractor,
    fetch_transcript,
    fetch_video_title,
    pick_caption_track,
    segments_from_json3,
    video_id,
)
from llmwiki.models.source import SourceMeta

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def meta(modality: str, **kwargs) -> SourceMeta:
    return SourceMeta(source_id="a" * 16, modality=modality, sha256="0" * 64, **kwargs)


@pytest.mark.parametrize(
    ("mime", "filename", "url", "expected"),
    [
        ("application/pdf", None, None, "pdf"),
        ("", "paper.pdf", None, "pdf"),
        ("text/html", None, "https://example.org/a", "web"),
        ("", None, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("", None, "https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("image/png", "shot.png", None, "image"),
        ("text/plain", "notes.txt", None, "text"),
        # A link whose server answered with a PDF is a PDF, not a web page.
        ("application/pdf", None, "https://arxiv.org/pdf/2401.00001", "pdf"),
        ("application/pdf; charset=binary", None, None, "pdf"),
        # A served text file is a text source; boilerplate removal would gut it.
        ("text/plain", None, "https://example.org/notes.txt", "text"),
        ("text/markdown", None, "https://example.org/readme.md", "text"),
        # Unknown content type behind a URL is still assumed to be a page.
        ("", None, "https://blog.example.org/post", "web"),
        ("application/octet-stream", None, None, "text"),
        # YouTube shapes beyond /watch and youtu.be.
        ("", None, "https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube"),
        ("", None, "https://m.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("", None, "https://www.youtube.com/live/dQw4w9WgXcQ", "youtube"),
        # A channel page has no video id: it is a page, not a transcript.
        ("", None, "https://www.youtube.com/@karpathy", "web"),
    ],
)
def test_modality_detection(mime, filename, url, expected) -> None:
    assert detect_modality(mime, filename, url) == expected


def test_pdf_extraction_keeps_page_markers() -> None:
    doc = PdfExtractor().extract(meta("pdf"), (FIXTURES / "sample.pdf").read_bytes())

    assert "Retrieval augmented generation" in doc.text
    assert "### Page 1" in doc.text and "### Page 2" in doc.text
    assert doc.extra["page_count"] == 2


def test_pdf_without_a_text_layer_fails_clearly() -> None:
    import pymupdf

    empty = pymupdf.open()
    empty.new_page()
    data = empty.tobytes()
    empty.close()

    with pytest.raises(ExtractionError, match="no text layer"):
        PdfExtractor().extract(meta("pdf"), data)


def test_corrupt_pdf_raises_extraction_error_not_a_bare_exception() -> None:
    with pytest.raises(ExtractionError):
        PdfExtractor().extract(meta("pdf"), b"not a pdf at all")


def test_web_extraction_drops_navigation_and_footer() -> None:
    doc = WebExtractor().extract(
        meta("web", url="https://example.org/chunking"),
        (FIXTURES / "sample.html").read_bytes(),
    )

    assert "Structural splitting" in doc.text
    assert "navigation boilerplate" not in doc.text
    assert "About" not in doc.text
    assert doc.title == "Chunking Strategies for Retrieval"


def test_web_extraction_of_contentless_html_fails() -> None:
    with pytest.raises(ExtractionError):
        WebExtractor().extract(meta("web", url="https://x.org"), b"<html><body></body></html>")


def test_web_title_comes_from_the_html_when_meta_has_none() -> None:
    html = (FIXTURES / "sample.html").read_text(encoding="utf-8")
    assert title_from_html(html) == "Chunking Strategies for Retrieval"


def test_youtube_extract_uses_the_captured_title() -> None:
    doc = YouTubeExtractor().extract(
        meta("youtube", url="https://youtu.be/dQw4w9WgXcQ", title="Never Gonna Give You Up"),
        (FIXTURES / "transcript.json").read_bytes(),
    )
    assert doc.title == "Never Gonna Give You Up"


def test_fetch_video_title_reads_oembed(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert "oembed" in url
        # The request must be set, or raise_for_status() raises RuntimeError and
        # fetch_video_title swallows it as a failed lookup.
        return httpx.Response(
            200,
            json={"title": "Never Gonna Give You Up"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    assert fetch_video_title("https://youtu.be/dQw4w9WgXcQ") == "Never Gonna Give You Up"


def test_fetch_video_title_is_empty_when_oembed_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert fetch_video_title("https://youtu.be/dQw4w9WgXcQ") == ""


def test_youtube_transcript_becomes_timestamped_paragraphs() -> None:
    doc = YouTubeExtractor().extract(
        meta("youtube", url="https://youtu.be/dQw4w9WgXcQ"),
        (FIXTURES / "transcript.json").read_bytes(),
    )

    assert "### 00:00:00" in doc.text
    assert "segment 0" in doc.text
    assert doc.extra["segment_count"] == 40


def test_youtube_empty_transcript_fails() -> None:
    with pytest.raises(ExtractionError):
        YouTubeExtractor().extract(meta("youtube"), json.dumps([]).encode())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://youtu.be/dQw4w9WgXcQ", True),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", True),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", True),
        ("https://www.youtube.com/playlist?list=PL1234", False),
        # The host must match too, or a query string could smuggle an id in.
        ("https://example.org/read?ref=youtu.be/dQw4w9WgXcQ", False),
    ],
)
def test_is_youtube_url(url, expected) -> None:
    from llmwiki.extractors.youtube import is_youtube_url

    assert is_youtube_url(url) is expected


def test_video_id_extraction() -> None:
    assert video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("https://youtu.be/dQw4w9WgXcQ?t=30") == "dQw4w9WgXcQ"
    with pytest.raises(ExtractionError):
        video_id("https://example.org/not-a-video")


def test_registry_returns_the_right_extractor() -> None:
    assert isinstance(get_extractor("pdf"), PdfExtractor)
    assert isinstance(get_extractor("web"), WebExtractor)
    assert isinstance(get_extractor("youtube"), YouTubeExtractor)


# --- fetch_transcript: the capture-time network call (mocked at the library) ---


class _FakeTranscriptApi:
    """Stands in for youtube_transcript_api.YouTubeTranscriptApi.

    Records the constructor kwargs and either returns canned segments or raises
    whatever ``raise_`` names, so the proxy plumbing and the error mapping can
    be pinned without a network.
    """

    constructed: list[dict] = []
    raise_: type[Exception] | None = None

    def __init__(self, **kwargs) -> None:
        type(self).constructed.append(kwargs)

    def fetch(self, video_id: str):
        if self.raise_ is not None:
            raise self.raise_(video_id)

        class _Fetched:
            @staticmethod
            def to_raw_data():
                return [{"text": "hi", "start": 0.0, "duration": 1.0}]

        return _Fetched()


@pytest.fixture
def transcript_api(monkeypatch):
    pytest.importorskip("youtube_transcript_api")
    _FakeTranscriptApi.constructed = []
    _FakeTranscriptApi.raise_ = None
    monkeypatch.setattr("youtube_transcript_api.YouTubeTranscriptApi", _FakeTranscriptApi)
    return _FakeTranscriptApi


def test_fetch_transcript_goes_direct_when_no_proxy_is_configured(transcript_api) -> None:
    data = fetch_transcript("https://youtu.be/dQw4w9WgXcQ")

    assert json.loads(data)[0]["text"] == "hi"
    assert transcript_api.constructed == [{"proxy_config": None}]


def test_fetch_transcript_routes_through_youtube_proxy_url(transcript_api) -> None:
    """YOUTUBE_PROXY_URL is the documented answer to YouTube blocking cloud IPs."""
    fetch_transcript("https://youtu.be/dQw4w9WgXcQ", proxy_url="http://u:p@proxy.example:8080")

    (kwargs,) = transcript_api.constructed
    proxy = kwargs["proxy_config"]
    assert proxy is not None
    assert proxy.to_requests_dict() == {
        "http": "http://u:p@proxy.example:8080",
        "https": "http://u:p@proxy.example:8080",
    }


def test_a_youtube_ip_block_is_a_one_line_error_that_names_the_fix(transcript_api) -> None:
    """The library's RequestBlocked text is ~20 lines; the sender sees one that says what to set."""
    from youtube_transcript_api._errors import RequestBlocked

    transcript_api.raise_ = RequestBlocked

    with pytest.raises(ExtractionError) as excinfo:
        fetch_transcript("https://youtu.be/dQw4w9WgXcQ")

    message = str(excinfo.value)
    assert "\n" not in message
    assert "dQw4w9WgXcQ" in message
    assert "YOUTUBE_PROXY_URL" in message
    assert isinstance(excinfo.value.__cause__, RequestBlocked)


def test_other_transcript_failures_keep_only_the_first_line(transcript_api) -> None:
    class _NoCaptions(Exception):
        def __str__(self) -> str:
            return "no captions for this video\n\nlong explanation follows\nand follows"

    transcript_api.raise_ = _NoCaptions

    expected = r"^no transcript available for dQw4w9WgXcQ: no captions for this video$"
    with pytest.raises(ExtractionError, match=expected):
        fetch_transcript("https://youtu.be/dQw4w9WgXcQ")


# --- fetch_transcript via yt-dlp + cookies (YOUTUBE_COOKIES_PATH) ---------------

_JSON3 = {
    "events": [
        {"tStartMs": 0, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},  # window def, dropped
        {"tStartMs": 1200, "dDurationMs": 1800, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
        {"tStartMs": 3000, "dDurationMs": 900, "segs": [{"utf8": "  again  "}]},
    ]
}


def _info(*, subtitles=None, automatic_captions=None) -> dict:
    return {
        "id": "dQw4w9WgXcQ",
        "subtitles": subtitles or {},
        "automatic_captions": automatic_captions or {},
    }


def _track(lang_url: str, *exts: str) -> list[dict]:
    return [{"ext": ext, "url": f"https://yt/{lang_url}.{ext}"} for ext in exts]


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL: records opts, serves a canned info dict + caption body."""

    constructed: list[dict] = []
    info: dict = {}
    body: bytes = b""
    raise_: Exception | None = None
    fetched_urls: list[str] = []

    def __init__(self, opts: dict) -> None:
        type(self).constructed.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def extract_info(self, url: str, download: bool):
        assert download is False, "captions only - the video must never be downloaded"
        if self.raise_ is not None:
            raise self.raise_
        return self.info

    def urlopen(self, url: str):
        type(self).fetched_urls.append(url)
        body = self.body

        class _Resp:
            @staticmethod
            def read() -> bytes:
                return body

        return _Resp()


@pytest.fixture
def ytdlp(monkeypatch, tmp_path):
    pytest.importorskip("yt_dlp")
    _FakeYoutubeDL.constructed = []
    _FakeYoutubeDL.fetched_urls = []
    _FakeYoutubeDL.info = _info(subtitles={"en": _track("en-manual", "vtt", "json3")})
    _FakeYoutubeDL.body = json.dumps(_JSON3).encode()
    _FakeYoutubeDL.raise_ = None
    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYoutubeDL)
    cookies = tmp_path / "youtube_cookies.txt"
    cookies.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    )
    _FakeYoutubeDL.cookies = cookies
    return _FakeYoutubeDL


def test_cookies_path_switches_the_fetch_to_yt_dlp(ytdlp, transcript_api) -> None:
    """YOUTUBE_COOKIES_PATH set -> yt-dlp with that session; transcript-api is not touched."""
    data = fetch_transcript("https://youtu.be/dQw4w9WgXcQ", cookies_path=str(ytdlp.cookies))

    assert transcript_api.constructed == []
    (opts,) = ytdlp.constructed
    assert opts["skip_download"] is True
    assert "proxy" not in opts
    assert json.loads(data) == [
        {"text": "hello world", "start": 1.2, "duration": 1.8},
        {"text": "again", "start": 3.0, "duration": 0.9},
    ]


def test_yt_dlp_gets_a_temp_copy_of_the_cookie_file_and_removes_it(ytdlp) -> None:
    """yt-dlp writes refreshed cookies back; the configured file may be a read-only mount."""
    fetch_transcript("https://youtu.be/dQw4w9WgXcQ", cookies_path=str(ytdlp.cookies))

    (opts,) = ytdlp.constructed
    assert opts["cookiefile"] != str(ytdlp.cookies)
    assert not Path(opts["cookiefile"]).exists(), "temp copy must not be left behind"
    assert ytdlp.cookies.exists()


def test_yt_dlp_also_uses_the_proxy_when_both_are_configured(ytdlp) -> None:
    fetch_transcript(
        "https://youtu.be/dQw4w9WgXcQ",
        proxy_url="http://u:p@proxy.example:8080",
        cookies_path=str(ytdlp.cookies),
    )

    assert ytdlp.constructed[0]["proxy"] == "http://u:p@proxy.example:8080"


def test_yt_dlp_route_downloads_the_chosen_json3_track_only(ytdlp) -> None:
    fetch_transcript("https://youtu.be/dQw4w9WgXcQ", cookies_path=str(ytdlp.cookies))

    assert ytdlp.fetched_urls == ["https://yt/en-manual.json3"]


def test_a_missing_cookie_file_is_a_clear_error_not_a_yt_dlp_call(ytdlp, tmp_path) -> None:
    with pytest.raises(ExtractionError, match="YOUTUBE_COOKIES_PATH=.* is not a file"):
        fetch_transcript("https://youtu.be/dQw4w9WgXcQ", cookies_path=str(tmp_path / "nope.txt"))

    assert ytdlp.constructed == []


def test_a_video_without_captions_is_a_one_line_error(ytdlp) -> None:
    ytdlp.info = _info()

    with pytest.raises(ExtractionError, match=r"^no captions .* dQw4w9WgXcQ") as excinfo:
        fetch_transcript("https://youtu.be/dQw4w9WgXcQ", cookies_path=str(ytdlp.cookies))

    assert "\n" not in str(excinfo.value)


def test_a_rejected_session_names_the_cookie_file_as_the_fix(ytdlp) -> None:
    from yt_dlp.utils import DownloadError

    ytdlp.raise_ = DownloadError(
        "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot.\nUse --cookies ..."
    )

    with pytest.raises(ExtractionError) as excinfo:
        fetch_transcript("https://youtu.be/dQw4w9WgXcQ", cookies_path=str(ytdlp.cookies))

    message = str(excinfo.value)
    assert "\n" not in message
    assert "YOUTUBE_COOKIES_PATH" in message and "expired" in message
    assert isinstance(excinfo.value.__cause__, DownloadError)


def test_pick_caption_track_prefers_manual_then_english_then_json3() -> None:
    info = _info(
        subtitles={
            "de": _track("de-manual", "vtt", "json3"),
            "en-GB": _track("engb-manual", "json3"),
        },
        automatic_captions={"en": _track("en-auto", "json3")},
    )
    assert pick_caption_track(info) == {
        "kind": "subtitles", "lang": "en-GB", "url": "https://yt/engb-manual.json3"
    }

    # No manual track -> automatic; exact "en" beats "en-orig".
    info = _info(
        automatic_captions={"en-orig": _track("orig", "json3"), "en": _track("en", "json3")}
    )
    assert pick_caption_track(info)["url"] == "https://yt/en.json3"

    # Only a vtt listed -> nothing usable rather than a wrong format.
    assert pick_caption_track(_info(subtitles={"en": _track("en", "vtt")})) is None
    assert pick_caption_track({}) is None


def test_segments_from_json3_matches_the_transcript_api_shape() -> None:
    segments = segments_from_json3(_JSON3)

    assert segments == [
        {"text": "hello world", "start": 1.2, "duration": 1.8},
        {"text": "again", "start": 3.0, "duration": 0.9},
    ]
    assert set(segments[0]) == {"text", "start", "duration"}
    assert segments_from_json3({}) == []
