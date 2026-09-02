"""Plain text and markdown: decode, normalize whitespace, done."""

from __future__ import annotations

from llmwiki.extractors.base import ExtractionError
from llmwiki.models.source import ExtractedDoc, SourceMeta


class TextExtractor:
    """Fallback extractor for text-like bytes and unknown modalities."""

    def extract(self, meta: SourceMeta, data: bytes) -> ExtractedDoc:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("latin-1")
            except Exception as exc:  # pragma: no cover - latin-1 decodes anything
                raise ExtractionError(f"undecodable bytes for {meta.source_id}") from exc
        text = normalize(text)
        if not text.strip():
            raise ExtractionError(f"no text content in {meta.source_id}")
        title = meta.title or _first_line(text)
        return ExtractedDoc(
            source_id=meta.source_id, title=title, text=text, modality=meta.modality, url=meta.url
        )


def normalize(text: str) -> str:
    """Collapse runs of blank lines and strip trailing whitespace, preserving structure."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blanks = 0
    for line in lines:
        if line:
            blanks = 0
            out.append(line)
        else:
            blanks += 1
            if blanks <= 2:
                out.append("")
    return "\n".join(out).strip() + "\n"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return "Untitled"
