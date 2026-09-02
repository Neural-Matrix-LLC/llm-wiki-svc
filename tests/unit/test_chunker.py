"""Chunker behaviour: headings before length, offsets that round-trip."""

from __future__ import annotations

import pytest
from tests.factories import make_extracted_doc

from llmwiki.models.source import ExtractedDoc
from llmwiki.pipeline.chunker import chunk_document, split_sections


def test_splits_on_headings_before_length() -> None:
    doc = make_extracted_doc()
    chunks = chunk_document(doc, size=10_000, overlap=0)

    sections = [chunk.metadata.section for chunk in chunks]
    assert sections == ["Retrieval-Augmented Generation", "Chunking", "Evaluation"]


def test_long_section_is_split_with_overlap() -> None:
    long_body = "word " * 2000
    doc = ExtractedDoc(source_id="a" * 16, title="Long", text=f"# Long\n\n{long_body}",
                       modality="text")
    chunks = chunk_document(doc, size=1000, overlap=200)

    assert len(chunks) > 1
    first, second = chunks[0], chunks[1]
    overlap = first.text[-200:]
    assert second.text.startswith(overlap), "overlap is not carried into the next chunk"


def test_offsets_round_trip_into_the_extracted_text() -> None:
    doc = make_extracted_doc()
    for chunk in chunk_document(doc, size=200, overlap=50):
        start, end = chunk.metadata.char_start, chunk.metadata.char_end
        assert doc.text[start:end] == chunk.text, (
            "char_start/char_end must locate the chunk in the extracted text, "
            "or a citation cannot be verified against the source"
        )


def test_chunk_ids_are_deterministic() -> None:
    doc = make_extracted_doc()
    first = [chunk.id for chunk in chunk_document(doc, size=500, overlap=100)]
    second = [chunk.id for chunk in chunk_document(doc, size=500, overlap=100)]

    assert first == second
    assert first[0] == f"{doc.source_id}:0", "re-ingest must overwrite, not duplicate"


@pytest.mark.parametrize("text", ["", "   ", "x", "#\n", "# Only A Heading\n"])
def test_degenerate_inputs_do_not_raise(text: str) -> None:
    doc = ExtractedDoc(source_id="b" * 16, title="Edge", text=text, modality="text")
    chunks = chunk_document(doc, size=100, overlap=10)
    assert all(chunk.text.strip() for chunk in chunks)


def test_overlap_must_be_smaller_than_size() -> None:
    doc = make_extracted_doc()
    with pytest.raises(ValueError):
        chunk_document(doc, size=100, overlap=100)


def test_split_sections_keeps_preamble() -> None:
    sections = split_sections("intro text\n\n# Heading\n\nbody\n")
    assert sections[0][0] == ""
    assert "intro text" in sections[0][1]
