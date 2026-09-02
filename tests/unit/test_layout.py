"""Key construction. A hostile filename or URL must not escape its prefix."""

from __future__ import annotations

import pytest

from llmwiki.storage import layout


def test_source_id_is_content_addressed() -> None:
    assert layout.source_id_for_bytes(b"hello") == layout.source_id_for_bytes(b"hello")
    assert layout.source_id_for_bytes(b"hello") != layout.source_id_for_bytes(b"world")
    assert len(layout.source_id_for_bytes(b"hello")) == 16


def test_url_canonicalization_collapses_trivial_variants() -> None:
    variants = [
        "https://example.org/page",
        "https://www.example.org/page/",
        "HTTPS://Example.ORG/page",
        "https://example.org/page#section",
    ]
    ids = {layout.source_id_for_url(url) for url in variants}
    assert len(ids) == 1, "the same page captured twice must yield one source"


def test_query_string_is_significant() -> None:
    """?v= identifies a different video; stripping queries would merge distinct sources."""
    assert layout.source_id_for_url("https://x.org/a?v=1") != layout.source_id_for_url(
        "https://x.org/a?v=2"
    )


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "a/../../b", "", "not-hex", "z" * 16])
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
