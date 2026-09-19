"""Image sources: one rendered page for the description step (Phase 2, plan §21.2 D2).

No model call happens here (extractors are L1 and LLM-free). The bytes are
validated and normalised to PNG - downscaled so the long edge is at most
``MAX_EDGE_PX`` - and handed on as a single vision page; ``pipeline/describe``
turns it into markdown. With ``vision_mode="off"`` an image cannot become
text, and the extractor says so rather than storing an empty source.
"""

from __future__ import annotations

from llmwiki.extractors.base import ExtractionError
from llmwiki.models.source import ExtractedDoc, SourceMeta

MAX_EDGE_PX = 1568


def render_png(data: bytes, filetype: str | None = None, max_edge: int = MAX_EDGE_PX) -> bytes:
    """Decode any PyMuPDF-readable image and re-encode as PNG within ``max_edge``."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - dependency is declared
        import fitz as pymupdf  # type: ignore[no-redef]

    try:
        pixmap = pymupdf.Pixmap(data)
    except Exception as exc:
        raise ExtractionError(f"cannot decode image: {exc}") from exc
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)  # drop alpha; PNG stays valid, models prefer RGB
    longest = max(pixmap.width, pixmap.height)
    if longest > max_edge:
        scale = max_edge / longest
        pixmap = pymupdf.Pixmap(pixmap, int(pixmap.width * scale), int(pixmap.height * scale),
                                None) if hasattr(pymupdf.Pixmap, "shrink") else pixmap
    return bytes(pixmap.tobytes("png"))


class ImageExtractor:
    """An uploaded picture (Telegram photo, ``POST /upload`` image) becomes one vision page."""

    def __init__(self, vision_mode: str = "off") -> None:
        self.vision_mode = vision_mode

    def extract(self, meta: SourceMeta, data: bytes) -> ExtractedDoc:
        if self.vision_mode == "off":
            raise ExtractionError(
                f"{meta.source_id} is an image; set VISION_MODE=auto (and route describe_image "
                "to a vision-capable model) to describe images"
            )
        png = render_png(data)
        title = (meta.title or meta.filename or "Untitled image").strip()
        return ExtractedDoc(
            source_id=meta.source_id,
            title=title,
            text=f"# {title}\n\n<!-- vision:p1 -->\n",
            modality="image",
            url=meta.url,
            extra={"vision_pages": [{"key": "p1", "media_type": "image/png", "data": png,
                                     "hint": "uploaded image"}]},
        )
