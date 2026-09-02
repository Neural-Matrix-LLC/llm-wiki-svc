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
from llmwiki.extractors.web import WebExtractor
from llmwiki.extractors.youtube import YouTubeExtractor, video_id
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


def test_web_extraction_of_contentless_html_fails() -> None:
    with pytest.raises(ExtractionError):
        WebExtractor().extract(meta("web", url="https://x.org"), b"<html><body></body></html>")


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


def test_video_id_extraction() -> None:
    assert video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert video_id("https://youtu.be/dQw4w9WgXcQ?t=30") == "dQw4w9WgXcQ"
    with pytest.raises(ExtractionError):
        video_id("https://example.org/not-a-video")


def test_registry_returns_the_right_extractor() -> None:
    assert isinstance(get_extractor("pdf"), PdfExtractor)
    assert isinstance(get_extractor("web"), WebExtractor)
    assert isinstance(get_extractor("youtube"), YouTubeExtractor)
