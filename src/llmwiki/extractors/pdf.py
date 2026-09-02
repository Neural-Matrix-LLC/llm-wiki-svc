"""PDF extraction via PyMuPDF, preserving page boundaries as section markers."""

from __future__ import annotations

from llmwiki.extractors.base import ExtractionError
from llmwiki.extractors.text import normalize
from llmwiki.models.source import ExtractedDoc, SourceMeta


class PdfExtractor:
    """Text layer only. Scanned PDFs with no text layer fail loudly rather than silently."""

    def extract(self, meta: SourceMeta, data: bytes) -> ExtractedDoc:
        try:
            import pymupdf
        except ImportError:  # pragma: no cover - dependency is declared
            import fitz as pymupdf  # type: ignore[no-redef]

        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ExtractionError(f"cannot open PDF {meta.source_id}: {exc}") from exc

        parts: list[str] = []
        with document:
            title = (document.metadata or {}).get("title") or ""
            for number in range(document.page_count):
                page_text = str(document[number].get_text("text")).strip()
                if page_text:
                    parts.append(f"### Page {number + 1}\n\n{page_text}")

        if not parts:
            raise ExtractionError(
                f"no text layer in PDF {meta.source_id} - likely a scan; "
                "OCR is out of Phase 0 scope"
            )
        return ExtractedDoc(
            source_id=meta.source_id,
            title=(meta.title or title or meta.filename or "Untitled PDF").strip(),
            text=normalize("\n\n".join(parts)),
            modality="pdf",
            url=meta.url,
            extra={"page_count": len(parts)},
        )
