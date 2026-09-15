"""Ingest: dedup, status transitions, and failure handling that does not crash the worker."""

from __future__ import annotations

import json
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


def test_capture_requires_exactly_one_input(pipeline) -> None:
    with pytest.raises(ValueError, match="exactly one"):
def test_capture_requires_exactly_one_input(pipeline) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        pipeline.capture()
    with pytest.raises(ValueError, match="exactly one"):
        pipeline.capture(url="https://example.org/a", text="pasted")
    with pytest.raises(ValueError, match="exactly one"):
        pipeline.capture(file=b"bytes", text="pasted")
    with pytest.raises(ValueError, match="exactly one"):
        pipeline.capture(url="https://example.org/a", text="pasted")
    with pytest.raises(ValueError, match="exactly one"):
        pipeline.capture(file=b"bytes", text="pasted")


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


def test_capture_fills_web_title_from_html(pipeline, store) -> None:
    data = (FIXTURES / "sample.html").read_bytes()
    ref = pipeline.capture(file=data, filename="page.html", mime="text/html")
    meta = SourceMeta(**__import__("json").loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.title == "Chunking Strategies for Retrieval"


def test_capture_fills_youtube_title_from_oembed(pipeline, store, monkeypatch) -> None:
    monkeypatch.setattr(
        "llmwiki.extractors.youtube.fetch_transcript",
        lambda url: (FIXTURES / "transcript.json").read_bytes(),
    )
    monkeypatch.setattr(
        "llmwiki.extractors.youtube.fetch_video_title",
        lambda url: "Never Gonna Give You Up",
    )
    ref = pipeline.capture(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    meta = SourceMeta(**__import__("json").loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.title == "Never Gonna Give You Up"
    assert meta.modality == "youtube"


def test_caller_title_wins_over_inferred_html_title(pipeline, store) -> None:
    data = (FIXTURES / "sample.html").read_bytes()
    ref = pipeline.capture(
        file=data, filename="page.html", mime="text/html", title="Explicit Title"
    )
    meta = SourceMeta(**__import__("json").loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.title == "Explicit Title"


def test_extraction_is_a_pure_function_of_stored_bytes(pipeline, store) -> None:
    """Re-extraction must not need the network - the bytes are the source."""
    data = (FIXTURES / "sample.html").read_bytes()
    ref = pipeline.capture(file=data, filename="page.html", mime="text/html")
    meta = SourceMeta(**__import__("json").loads(store.get(raw_meta(ref.source_id)).decode()))

    doc = pipeline.extract(meta)
    assert "Structural splitting" in doc.text


# --- pure text: a source with no file and no URL ---------------------------


TEXT = "Retrieval augmented generation\n\nGrounding an answer in retrieved documents.\n"


def test_capture_stores_pasted_text_as_its_own_source(pipeline, store) -> None:
    ref = pipeline.capture(text=TEXT)

    meta = SourceMeta(**json.loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.modality == "text"
    assert meta.url is None and meta.filename is None
    assert store.get(f"raw/{ref.source_id}/original.txt").decode() == TEXT


def test_pasted_text_takes_its_title_from_the_first_line(pipeline, store) -> None:
    ref = pipeline.capture(text=TEXT)

    meta = SourceMeta(**json.loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.title == "Retrieval augmented generation"


def test_identical_text_is_deduplicated(pipeline) -> None:
    first = pipeline.capture(text=TEXT)
    second = pipeline.capture(text=TEXT, title="different title")

    assert second.source_id == first.source_id
    assert second.duplicate is True


def test_empty_text_is_rejected_at_capture(pipeline) -> None:
    with pytest.raises(ValueError, match="empty"):
        pipeline.capture(text="   \n  ")


def test_pasted_text_runs_the_full_flow_and_reaches_done(pipeline, store) -> None:
    ref = pipeline.capture(text=TEXT)
    status = pipeline.process(ref.source_id)

    assert status.state == "done", status.error
    assert status.chunk_count > 0
    assert store.get(raw_extracted(ref.source_id)).decode().startswith("Retrieval")


def test_a_text_file_upload_is_extracted_as_text(pipeline, store) -> None:
    ref = pipeline.capture(
        file=TEXT.encode(), filename="notes.txt", mime="text/plain"
    )
    status = pipeline.process(ref.source_id)

    meta = SourceMeta(**json.loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.modality == "text"
    assert status.state == "done", status.error


# --- URL sources: the served content type decides the modality -------------


def test_a_url_that_serves_a_pdf_is_captured_as_a_pdf(pipeline, store, monkeypatch) -> None:
    """A link to a paper and a link to a blog post are the same input shape.

    Trusting the URL alone sent every arXiv-style link through the HTML
    extractor, where trafilatura found no readable content and the source
    failed - the bytes were a PDF all along.
    """
    data = (FIXTURES / "sample.pdf").read_bytes()
    monkeypatch.setattr(
        "llmwiki.extractors.web.fetch", lambda url: (data, "application/pdf")
    )

    ref = pipeline.capture(url="https://arxiv.org/pdf/2401.00001")
    status = pipeline.process(ref.source_id)

    meta = SourceMeta(**json.loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.modality == "pdf"
    assert status.state == "done", status.error
    assert "### Page 1" in store.get(raw_extracted(ref.source_id)).decode()


def test_a_blog_url_is_still_captured_as_a_web_page(pipeline, store, monkeypatch) -> None:
    data = (FIXTURES / "sample.html").read_bytes()
    monkeypatch.setattr("llmwiki.extractors.web.fetch", lambda url: (data, "text/html"))

    ref = pipeline.capture(url="https://blog.example.org/chunking")

    meta = SourceMeta(**json.loads(store.get(raw_meta(ref.source_id)).decode()))
    assert meta.modality == "web"
    assert meta.title == "Chunking Strategies for Retrieval"


def test_a_duplicate_url_is_not_fetched_again(pipeline, monkeypatch) -> None:
    """The duplicate check runs before the fetch, or every recapture costs a request."""
    data = (FIXTURES / "sample.html").read_bytes()
    calls = []

    def counting_fetch(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return data, "text/html"

    monkeypatch.setattr("llmwiki.extractors.web.fetch", counting_fetch)
    pipeline.capture(url="https://blog.example.org/chunking")
    second = pipeline.capture(url="https://blog.example.org/chunking")

    assert second.duplicate is True
    assert len(calls) == 1, "a duplicate URL was fetched a second time"


# --- readable source ids: {hash}-{slug} (2026-09-13) --------------------------


def test_source_id_carries_the_title_slug_after_the_content_hash(
    pipeline, store, monkeypatch
) -> None:
    """``raw/`` and ``wiki/sources/`` are browsed by humans; the id must say what it is."""
    data = (FIXTURES / "sample.html").read_bytes()
    monkeypatch.setattr("llmwiki.extractors.web.fetch", lambda url: (data, "text/html"))

    ref = pipeline.capture(url="https://blog.example.org/chunking")

    digest, _, slug = ref.source_id.partition("-")
    assert len(digest) == 16
    assert slug == "chunking-strategies-for-retrieval"
    assert store.exists(f"raw/{ref.source_id}/meta.json")


def test_a_pdf_upload_takes_its_slug_from_the_filename(pipeline) -> None:
    """A PDF has no title until extraction runs, so the filename stem is the readable half."""
    data = (FIXTURES / "sample.pdf").read_bytes()
    ref = pipeline.capture(file=data, filename="Attention Is All You Need.pdf",
                           mime="application/pdf")
    assert ref.source_id.endswith("-attention-is-all-you-need")


def test_a_url_only_pdf_takes_its_slug_from_the_url_tail(pipeline, monkeypatch) -> None:
    data = (FIXTURES / "sample.pdf").read_bytes()
    monkeypatch.setattr("llmwiki.extractors.web.fetch", lambda url: (data, "application/pdf"))
    ref = pipeline.capture(url="https://arxiv.org/pdf/2401.00001")
    assert ref.source_id.endswith("-2401-00001")


def test_dedup_ignores_the_slug(pipeline) -> None:
    """Same bytes, different filename: one source, and the first id wins."""
    data = (FIXTURES / "sample.pdf").read_bytes()
    first = pipeline.capture(file=data, filename="draft-v1.pdf", mime="application/pdf")
    second = pipeline.capture(file=data, filename="Final Version.pdf", mime="application/pdf")

    assert first.source_id.endswith("-draft-v1")
    assert second.source_id == first.source_id
    assert second.duplicate is True


def test_a_source_captured_under_the_bare_hash_id_is_still_a_duplicate(
    pipeline, store
) -> None:
    """Corpora from before the slug: raw/{hash}/ with no slug must still short-circuit."""
    from llmwiki.storage.layout import content_hash_for_bytes

    data = (FIXTURES / "sample.pdf").read_bytes()
    legacy_id = content_hash_for_bytes(data)
    store.put(f"raw/{legacy_id}/original.pdf", data, "application/pdf")
    store.put(f"raw/{legacy_id}/meta.json", b"{}", "application/json")

    ref = pipeline.capture(file=data, filename="sample.pdf", mime="application/pdf")

    assert ref.duplicate is True
    assert ref.source_id == legacy_id
    assert store.list("raw/") == [
        f"raw/{legacy_id}/meta.json", f"raw/{legacy_id}/original.pdf"
    ], "a second folder for the same bytes"
