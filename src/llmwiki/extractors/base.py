"""Extractor protocol and registry: bytes or URL to an :class:`ExtractedDoc`."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llmwiki.models.source import ExtractedDoc, Modality, SourceMeta


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
    """Pick a modality from what capture recorded. Order matters: URL shape beats mime."""
    if url:
        lowered = url.lower()
        if "youtube.com/watch" in lowered or "youtu.be/" in lowered:
            return "youtube"
    if mime == "application/pdf" or (filename or "").lower().endswith(".pdf"):
        return "pdf"
    if mime.startswith("image/"):
        return "image"
    if url or mime in ("text/html", "application/xhtml+xml"):
        return "web"
    return "text"


def get_extractor(modality: Modality) -> Extractor:
    """Return the extractor for a modality. Imports are local so unused deps stay unloaded."""
    if modality == "pdf":
        from llmwiki.extractors.pdf import PdfExtractor

        return PdfExtractor()
    if modality == "youtube":
        from llmwiki.extractors.youtube import YouTubeExtractor

        return YouTubeExtractor()
    if modality == "web":
        from llmwiki.extractors.web import WebExtractor

        return WebExtractor()
    from llmwiki.extractors.text import TextExtractor

    return TextExtractor()
