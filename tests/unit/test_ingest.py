"""Ingest: dedup, status transitions, and failure handling that does not crash the worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmwiki.models.source import SourceMeta
from llmwiki.pipeline.ingest import IngestPipeline
from llmwiki.storage.layout import raw_extracted, raw_meta

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def pipeline(store, vectors, embedder, llm, settings) -> IngestPipeline:
    return IngestPipeline(store, vectors, embedder, llm, settings)


def test_capture_writes_immutable_raw_objects(pipeline, store) -> None:
    data = (FIXTURES / "sample.pdf").read_bytes()
    ref = pipeline.capture(file=data, filename="sample.pdf", mime="application/pdf")

    assert store.exists(raw_meta(ref.source_id))
    assert store.get(f"raw/{ref.source_id}/original.pdf") == data


def test_identical_bytes_are_deduplicated(pipeline) -> None:
    data = (FIXTURES / "sample.pdf").read_bytes()
    first = pipeline.capture(file=data, filename="sample.pdf", mime="application/pdf")
    second = pipeline.capture(file=data, filename="copy.pdf", mime="application/pdf")

    assert second.source_id == first.source_id
    assert second.duplicate is True


def test_capture_requires_a_url_or_a_file(pipeline) -> None:
    with pytest.raises(ValueError):
        pipeline.capture()


def test_process_runs_the_full_flow_and_reaches_done(pipeline, store) -> None:
    ref = pipeline.capture(
        file=(FIXTURES / "sample.pdf").read_bytes(), filename="sample.pdf", mime="application/pdf"
    )
    status = pipeline.process(ref.source_id)

    assert status.state == "done", status.error
    assert status.chunk_count > 0
    assert status.pages_touched > 0
    assert store.exists(raw_extracted(ref.source_id))


def test_chunks_are_embedded_into_the_chunk_index(pipeline, vectors, settings) -> None:
    ref = pipeline.capture(
        file=(FIXTURES / "sample.pdf").read_bytes(), filename="sample.pdf", mime="application/pdf"
    )
    pipeline.process(ref.source_id)

    hits = vectors.query(settings.vectorize_chunks_index, [0.1] * settings.embedding_dim, k=10)
    assert hits, "no chunk vectors were written"
    assert all(hit.metadata["source_id"] == ref.source_id for hit in hits)


def test_a_corrupt_source_is_marked_failed_and_does_not_raise(pipeline, store) -> None:
    ref = pipeline.capture(file=b"%PDF-broken", filename="broken.pdf", mime="application/pdf")
    status = pipeline.process(ref.source_id)

    assert status.state == "failed"
    assert status.error
    # The failure is visible, not silent - the success criteria depend on this.
    assert pipeline.get_status(ref.source_id).state == "failed"


def test_status_of_an_unknown_source(pipeline) -> None:
    assert pipeline.get_status("f" * 16).state == "failed"


def test_meta_is_never_rewritten_on_recapture(pipeline, store) -> None:
    data = b"# Notes\n\nSome text about retrieval and embeddings.\n"
    ref = pipeline.capture(file=data, filename="notes.md", mime="text/markdown", title="First")
    original = store.get(raw_meta(ref.source_id))

    pipeline.capture(file=data, filename="notes.md", mime="text/markdown", title="Second")
    assert store.get(raw_meta(ref.source_id)) == original, "raw/ must be append-only"


def test_extraction_is_a_pure_function_of_stored_bytes(pipeline, store) -> None:
    """Re-extraction must not need the network - the bytes are the source."""
    data = (FIXTURES / "sample.html").read_bytes()
    ref = pipeline.capture(file=data, filename="page.html", mime="text/html")
    meta = SourceMeta(**__import__("json").loads(store.get(raw_meta(ref.source_id)).decode()))

    doc = pipeline.extract(meta)
    assert "Structural splitting" in doc.text
