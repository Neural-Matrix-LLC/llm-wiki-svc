"""Extractor protocol and registry: bytes or URL to an :class:`ExtractedDoc`."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llmwiki.models.source import ExtractedDoc, Modality, SourceMeta

_HTML_MIMES = frozenset({"text/html", "application/xhtml+xml"})


class ExtractionError(RuntimeError):
    """Raised when a source cannot be extracted.

    The pipeline catches this and marks the source ``failed`` with the message -
    a bad input must never take the worker down (plan 12.3, M3).
    """


@runtime_checkable
class Extractor(Protocol):
    """One modality's path from stored bytes to normalized markdown."""

    def extract(self, meta: SourceMeta, data: bytes) -> ExtractedDoc:
        """Turn the captured bytes into normalized text."""
        ...


def detect_modality(mime: str, filename: str | None, url: str | None) -> Modality:
    """Pick a modality from what capture recorded. Order matters: URL shape beats mime.

    ``mime`` may be either what the caller declared or - for a URL the pipeline
    has already fetched - what the server actually served.  Passing the served
    content type is what lets a link to a PDF be ingested as a PDF: a blog link
    and an arXiv link are the same input shape and differ only in the response.
    """
    if url:
        from llmwiki.extractors.youtube import is_youtube_url

        if is_youtube_url(url):
            return "youtube"
    base = mime.split(";")[0].strip().lower()
    if base == "application/pdf" or (filename or "").lower().endswith(".pdf"):
        return "pdf"
    if base.startswith("image/"):
        return "image"
    if base in _HTML_MIMES:
        return "web"
    if base.startswith("text/"):
        # text/plain, text/markdown, text/csv: a text file is a text source even
        # when it arrived over HTTP - running boilerplate removal over it would
        # only throw the content away.
        return "text"
    return "web" if url else "text"


def get_extractor(
    modality: Modality, *, vision_mode: str = "off", vision_max_pages: int = 8,
    vision_min_chars: int = 200, vision_image_area: float = 0.25,
) -> Extractor:
    """Return the extractor for a modality. Imports are local so unused deps stay unloaded.

    The vision keywords (Phase 2, plan §21.2 D4) only matter to the PDF and
    image extractors; ``vision_mode="off"`` is the pre-Phase-2 behaviour.
    """
    if modality == "pdf":
        from llmwiki.extractors.pdf import PdfExtractor

        return PdfExtractor(vision_mode=vision_mode, max_pages=vision_max_pages,
                            min_chars=vision_min_chars, image_area=vision_image_area)
    if modality == "image":
        from llmwiki.extractors.image import ImageExtractor

        return ImageExtractor(vision_mode=vision_mode)
    if modality == "youtube":
        from llmwiki.extractors.youtube import YouTubeExtractor

        return YouTubeExtractor()
    if modality == "web":
        from llmwiki.extractors.web import WebExtractor

        return WebExtractor()
    from llmwiki.extractors.text import TextExtractor

    return TextExtractor()
