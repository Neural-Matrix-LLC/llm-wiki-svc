"""Split extracted text into embeddable chunks.

Headings first, length second: a chunk that stops at a section boundary retrieves
better than one that stops at a character count.  Overlap only applies when a
section had to be split on length, since a heading boundary is a real boundary
and overlapping across it just duplicates tokens.
"""

from __future__ import annotations

import re

from llmwiki.models.chunk import Chunk, ChunkMetadata
from llmwiki.models.source import ExtractedDoc

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def split_sections(text: str) -> list[tuple[str, str, int]]:
    """Split on markdown headings. Returns ``(heading, body_including_heading, offset)``."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text, 0)]

    sections: list[tuple[str, str, int]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()]
        if preamble.strip():
            sections.append(("", preamble, 0))
    for index, match in enumerate(matches):
        start = match.start()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(2).strip(), text[start:stop], start))
    return sections


def chunk_document(
    doc: ExtractedDoc,
    size: int = 3200,
    overlap: int = 400,
) -> list[Chunk]:
    """Chunk an extracted document, preserving offsets back into the extracted text."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    if overlap >= size:
        raise ValueError("chunk overlap must be smaller than chunk size")

    chunks: list[Chunk] = []
    for heading, body, offset in split_sections(doc.text):
        for start, stop in _windows(body, size, overlap):
            text = body[start:stop]
            if not text.strip():
                continue
            index = len(chunks)
            chunks.append(
                Chunk(
                    id=Chunk.make_id(doc.source_id, index),
                    text=text,
                    metadata=ChunkMetadata(
                        source_id=doc.source_id,
                        chunk_index=index,
                        title=doc.title,
                        url=doc.url,
                        section=heading,
                        char_start=offset + start,
                        char_end=offset + stop,
                    ),
                )
            )
    return chunks


def _windows(body: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """Character windows over one section: whole if it fits, overlapping slices if not."""
    if len(body) <= size:
        return [(0, len(body))]
    step = size - overlap
    spans = []
    start = 0
    while start < len(body):
        spans.append((start, min(start + size, len(body))))
        if start + size >= len(body):
            break
        start += step
    return spans
