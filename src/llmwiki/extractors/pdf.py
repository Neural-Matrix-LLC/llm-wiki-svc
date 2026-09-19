"""PDF extraction via PyMuPDF, preserving page boundaries as section markers.

Phase 2 (design §4.10.4, plan §21.2 D4 / §21.6.5): with ``vision_mode`` on,
pages with (almost) no text layer - a scan - or, in ``auto``, pages whose
images cover a large share of the page or that are dense with vector
drawings, are rendered to PNG and handed to the description step as
``extra["vision_pages"]``; the text keeps a placeholder where the description
goes. At most ``max_pages`` per document; over-cap candidates get a note.
"""

from __future__ import annotations

from llmwiki.extractors.base import ExtractionError
from llmwiki.extractors.text import normalize
from llmwiki.models.source import ExtractedDoc, SourceMeta

PLACEHOLDER = "<!-- vision:{key} -->"
SKIPPED_NOTE = "> [page {page}: figure-heavy, not described - VISION_MAX_PAGES_PER_SOURCE reached]"
#: A document whose pages are this often text-less is treated as a scan.
SCAN_RATIO = 0.8
#: Vector-drawing density that marks a figure page (only measured on light-text pages).
DRAWINGS_MIN = 200
DRAWINGS_TEXT_MAX = 1_500
RENDER_DPI = 110
MAX_EDGE_PX = 1568


class PdfExtractor:
    """Text layer first. Scanned PDFs with no text layer fail loudly rather than silently.

    Phase 2 (plan §21.2 D4): with ``vision_mode`` on, pages with little or no
    text (or, opted in, figure-heavy ones) are rendered for the description
    step; ``off`` is the pre-Phase-2 behaviour exactly.
    """

    def __init__(self, vision_mode: str = "off", max_pages: int = 8, min_chars: int = 200,
                 image_area: float = 0.25) -> None:
        self.vision_mode = vision_mode
        self.max_pages = max_pages
        self.min_chars = min_chars
        self.image_area = image_area

    def extract(self, meta: SourceMeta, data: bytes) -> ExtractedDoc:
        try:
            import pymupdf
        except ImportError:  # pragma: no cover - dependency is declared
            import fitz as pymupdf  # type: ignore[no-redef]

        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ExtractionError(f"cannot open PDF {meta.source_id}: {exc}") from exc

        vision_on = self.vision_mode != "off"
        parts: list[str] = []
        vision_pages: list[dict] = []
        candidates = 0
        with document:
            title = (document.metadata or {}).get("title") or ""
            pages = [_PageInfo.read(document[n], n, measure=vision_on)
                     for n in range(document.page_count)]
            selected = self._select(pages) if vision_on else []
            chosen = set(selected)
            candidates = sum(1 for info in pages if self._is_candidate(info, pages))
            for info in pages:
                block = [f"### Page {info.number + 1}"]
                if info.text:
                    block.append(info.text)
                if info.number in chosen:
                    key = f"p{info.number + 1}"
                    block.append(PLACEHOLDER.format(key=key))
                    vision_pages.append({
                        "key": key, "media_type": "image/png",
                        "data": _render(document[info.number], pymupdf),
                        "hint": f"page {info.number + 1}",
                    })
                elif vision_on and self._is_candidate(info, pages):
                    block.append(SKIPPED_NOTE.format(page=info.number + 1))
                if len(block) > 1:
                    parts.append("\n\n".join(block))

        if not parts or (not any(info.text for info in pages) and not vision_pages):
            raise ExtractionError(
                f"no text layer in PDF {meta.source_id} - likely a scan; "
                + ("set VISION_MODE=auto to describe its pages" if not vision_on
                   else "and no page could be selected for description")
            )
        extra: dict = {"page_count": document.page_count if not document.is_closed else len(pages)}
        if vision_on:
            extra["vision_candidates"] = candidates
            if vision_pages:
                extra["vision_pages"] = vision_pages
        return ExtractedDoc(
            source_id=meta.source_id,
            title=(meta.title or title or meta.filename or "Untitled PDF").strip(),
            text=normalize("\n\n".join(parts)),
            modality="pdf",
            url=meta.url,
            extra=extra,
        )

    # -- page selection (plan §21.6.5) ---------------------------------------------

    def _is_candidate(self, info: _PageInfo, pages: list[_PageInfo]) -> bool:
        if self.vision_mode == "always":
            return True
        if self.vision_mode != "auto":
            return False
        return (info.chars < self.min_chars or info.image_area >= self.image_area
                or info.drawings >= DRAWINGS_MIN)

    def _select(self, pages: list[_PageInfo]) -> list[int]:
        """Which pages to render, at most ``max_pages``.

        A document where most pages have (almost) no text layer is a scan:
        take its pages in reading order. Otherwise take candidates by image
        area, biggest first - the figure pages - and blank-ish pages after.
        """
        if self.max_pages <= 0:
            return []
        candidates = [info for info in pages if self._is_candidate(info, pages)]
        if not candidates:
            return []
        scanned = sum(1 for info in pages if info.chars < self.min_chars)
        if self.vision_mode == "always" or scanned >= SCAN_RATIO * len(pages):
            ordered = sorted(candidates, key=lambda info: info.number)
        else:
            ordered = sorted(candidates, key=lambda info: (-info.image_area, info.number))
        return [info.number for info in ordered[: self.max_pages]]


class _PageInfo:
    """What page selection needs: text, its length, image coverage, vector density."""

    __slots__ = ("number", "text", "chars", "image_area", "drawings")

    def __init__(self, number: int, text: str, chars: int, image_area: float,
                 drawings: int) -> None:
        self.number, self.text, self.chars = number, text, chars
        self.image_area, self.drawings = image_area, drawings

    @classmethod
    def read(cls, page: object, number: int, *, measure: bool) -> _PageInfo:
        text = str(page.get_text("text")).strip()  # type: ignore[attr-defined]
        if not measure:
            return cls(number, text, len(text), 0.0, 0)
        rect = page.rect  # type: ignore[attr-defined]
        page_area = float(rect.width * rect.height) or 1.0
        covered = 0.0
        try:
            for image in page.get_images(full=True):  # type: ignore[attr-defined]
                for box in page.get_image_rects(image[0]):  # type: ignore[attr-defined]
                    covered += float(box.width * box.height)
        except Exception:  # pragma: no cover - a corrupt xref must not fail extraction
            pass
        drawings = 0
        if len(text) < DRAWINGS_TEXT_MAX:
            try:
                drawings = len(page.get_cdrawings())  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                drawings = 0
        return cls(number, text, len(text), min(covered / page_area, 1.0), drawings)


def _render(page: object, pymupdf: object) -> bytes:
    """Render a page to PNG at RENDER_DPI, capped so the long edge is at most MAX_EDGE_PX."""
    rect = page.rect  # type: ignore[attr-defined]
    zoom = RENDER_DPI / 72.0
    longest_pt = max(float(rect.width), float(rect.height)) or 1.0
    zoom = min(zoom, MAX_EDGE_PX / longest_pt)
    matrix = pymupdf.Matrix(zoom, zoom)  # type: ignore[attr-defined]
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)  # type: ignore[attr-defined]
    return bytes(pixmap.tobytes("png"))
