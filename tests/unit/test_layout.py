"""Key construction. A hostile filename or URL must not escape its prefix."""

from __future__ import annotations

import pytest

from llmwiki.storage import layout


def test_content_hash_is_content_addressed() -> None:
    assert layout.content_hash_for_bytes(b"hello") == layout.content_hash_for_bytes(b"hello")
    assert layout.content_hash_for_bytes(b"hello") != layout.content_hash_for_bytes(b"world")
    assert len(layout.content_hash_for_bytes(b"hello")) == 16


def test_source_id_is_hash_then_slug_of_the_title() -> None:
    digest = layout.content_hash_for_bytes(b"hello")
    source_id = layout.source_id_for(digest, "Attention Is All You Need!")
    assert source_id == f"{digest}-attention-is-all-you-need"
    assert layout.content_hash_of(source_id) == digest
    assert layout.raw_meta(source_id) == f"raw/{source_id}/meta.json"
    assert layout.wiki_source_note(source_id) == f"wiki/sources/{source_id}.md"


def test_source_slug_is_short_enough_for_a_vectorize_chunk_id() -> None:
    """Vectorize caps a vector id at 64 bytes and chunk ids are ``{source_id}:{n}``."""
    source_id = layout.source_id_for("a" * 16, "word " * 40)
    assert len(source_id) <= 16 + 1 + layout.SOURCE_SLUG_MAX
    assert len(f"{source_id}:99999") <= 64
    assert not source_id.endswith("-")


def test_empty_title_still_mints_a_valid_id() -> None:
    assert layout.source_id_for("a" * 16, "") == f"{'a' * 16}-untitled"
    assert layout.source_id_for("a" * 16, "!!!") == f"{'a' * 16}-untitled"


def test_pre_slug_ids_stay_valid() -> None:
    """Corpora captured before 2026-09-13 hold bare 16-hex ids; they must keep resolving."""
    legacy = "a1b2c3d4e5f60718"
    assert layout.raw_meta(legacy) == f"raw/{legacy}/meta.json"
    assert layout.content_hash_of(legacy) == legacy
    assert layout.is_source_id(legacy)
    assert layout.is_source_id(f"{legacy}-some-title")
    assert not layout.is_source_id("some-title")


def test_source_id_from_key_reads_either_format() -> None:
    assert layout.source_id_from_key("raw/a1b2c3d4e5f60718/meta.json") == "a1b2c3d4e5f60718"
    assert layout.source_id_from_key("raw/a1b2c3d4e5f60718-paper/original.pdf") == (
        "a1b2c3d4e5f60718-paper"
    )
    assert layout.source_id_from_key("wiki/concepts/rag.md") is None
    assert layout.source_id_from_key("raw/not-an-id/meta.json") is None


def test_source_id_needs_a_real_hash() -> None:
    with pytest.raises(ValueError):
        layout.source_id_for("not-hex", "title")
    with pytest.raises(ValueError):
        layout.raw_prefix_for_hash("../raw")


def test_url_canonicalization_collapses_trivial_variants() -> None:
    variants = [
        "https://example.org/page",
        "https://www.example.org/page/",
        "HTTPS://Example.ORG/page",
        "https://example.org/page#section",
    ]
    ids = {layout.content_hash_for_url(url) for url in variants}
    assert len(ids) == 1, "the same page captured twice must yield one source"


def test_query_string_is_significant() -> None:
    """?v= identifies a different video; stripping queries would merge distinct sources."""
    assert layout.content_hash_for_url("https://x.org/a?v=1") != layout.content_hash_for_url(
        "https://x.org/a?v=2"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd", "a/../../b", "", "not-hex", "z" * 16,
        "a" * 16 + "-../escape", "a" * 16 + "-Upper", "a" * 16 + "-", "a" * 16 + "-" + "s" * 41,
    ],
)
def test_hostile_source_ids_are_rejected(hostile: str) -> None:
    with pytest.raises(ValueError):
        layout.raw_meta(hostile)


@pytest.mark.parametrize("hostile", ["../sh", "pdf/../..", "", "a" * 20])
def test_hostile_extensions_are_rejected(hostile: str) -> None:
    with pytest.raises(ValueError):
        layout.raw_original("a" * 16, hostile)


def test_slug_from_hostile_title_stays_inside_the_prefix() -> None:
    key = layout.wiki_page("../../escape me!", "concept")
    assert key == "wiki/concepts/escape-me.md"
    assert ".." not in key


def test_page_types_map_to_their_folders() -> None:
    assert layout.wiki_page("rag", "concept") == "wiki/concepts/rag.md"
    assert layout.wiki_page("rag", "entity") == "wiki/entities/rag.md"
    assert layout.wiki_page("a" * 16, "source") == f"wiki/sources/{'a' * 16}.md"
    assert layout.wiki_page("anything", "index") == "wiki/index.md"


def test_extension_inference() -> None:
    assert layout.ext_for("application/pdf", None, None) == "pdf"
    assert layout.ext_for("", "notes.MD", None) == "md"
    assert layout.ext_for("", None, "https://x.org/paper.pdf") == "pdf"
    assert layout.ext_for("", None, None) == "bin"
