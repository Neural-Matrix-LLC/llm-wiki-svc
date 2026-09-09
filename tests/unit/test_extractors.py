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
from llmwiki.extractors.youtube import YouTubeExtractor, fetch_video_title, video_id
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
