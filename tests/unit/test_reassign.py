"""Moving a captured source to another domain, and pruning what it left behind.

``scripts/migrate_domains.py`` files pre-Phase-2 sources (all ``general``) under
registered domains through ``IngestPipeline.reassign`` and
``wiki.reassign.prune_moved_sources``; the script itself is pinned in
``test_phase1_scripts.py``.
"""

from __future__ import annotations

import json

import pytest
from tests.factories import make_source_meta

from llmwiki.lexical.memory import MemoryLexicalIndex
from llmwiki.llm.fake import FakeLLM
from llmwiki.models.page import PageGist
from llmwiki.models.source import DomainAssignment
from llmwiki.pipeline.ingest import IngestPipeline
from llmwiki.storage.layout import (
    GENERAL,
    INDEX_KEY,
    raw_meta,
    raw_original,
    raw_routing,
    wiki_page,
    wiki_source_note,
)
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.domains import UnknownDomain, upsert_domain
from llmwiki.wiki.reassign import prune_moved_sources

A, B = "a" * 16, "b" * 16


def _capture(store, source_id: str, text: str) -> None:
    meta = make_source_meta(source_id).model_copy(
        update={"modality": "text", "mime": "text/plain", "url": None, "domain": None})
    store.put(raw_original(source_id, "txt"), text.encode(), "text/plain")
    store.put(raw_meta(source_id), meta.model_dump_json().encode(), "application/json")


@pytest.fixture
def pipeline(store, vectors, embedder, settings) -> IngestPipeline:
    return IngestPipeline(store, vectors, embedder, FakeLLM(), settings,
                          lexical=MemoryLexicalIndex())


def _chunk_sources(vectors, index: str) -> set[str]:
    return {row["meta"].get("source_id") for row in vectors._index(index).values()}


def test_reassign_moves_chunks_routing_and_compiles_into_the_target(store, vectors, settings,
                                                                    pipeline) -> None:
    _capture(store, A, "# Paged attention\n\nKV cache blocks on the GPU.\n")
    assert pipeline.process(A).state == "done"  # nothing registered yet: general
    upsert_domain(store, "ml")
    chunks, lexical = settings.vectorize_chunks_index, pipeline.lexical
    assert A in _chunk_sources(vectors, chunks)

    result = pipeline.reassign(A, DomainAssignment(domain="ml", reason="test", explicit=True))

    assert not result.aborted and result.domain == "ml"
    assert pipeline.load_routing(A).domain == "ml"
    assert json.loads(store.get(raw_routing(A)))["reason"] == "test"
    assert A not in _chunk_sources(vectors, chunks)
    assert A in _chunk_sources(vectors, f"{chunks}-ml")
    assert lexical.count(chunks) == 0 and lexical.count(f"{chunks}-ml") >= 1
    assert store.exists(wiki_source_note(A, "ml"))
    assert any(A in gist.sources for gist in gists_mod.load_gists(store, "ml").values())
    assert pipeline.get_status(A).domain == "ml"
    assert store.exists(raw_original(A, "txt")), "raw/ never moves"


def test_reassign_refuses_the_same_domain_and_an_unregistered_one(store, pipeline) -> None:
    _capture(store, A, "# Chunking\n\nWindows over text.\n")
    pipeline.process(A)
    with pytest.raises(ValueError, match="already in domain"):
        pipeline.reassign(A, DomainAssignment(domain=GENERAL))
    with pytest.raises(UnknownDomain):
        pipeline.reassign(A, DomainAssignment(domain="nowhere"))


def test_propose_domain_persists_no_routing(store, pipeline) -> None:
    _capture(store, A, "# Robotics\n\nThe robotics arm controller.\n")
    pipeline.process(A)
    upsert_domain(store, "robotics")

    pick = pipeline.propose_domain(A)

    assert pick.domain == "robotics"
    assert pipeline.load_routing(A).domain == GENERAL


def _page(store, slug: str, sources: list[str], page_type: str = "concept") -> PageGist:
    store.put(wiki_page(slug, page_type), f"---\nslug: {slug}\n---\n".encode(), "text/markdown")
    return PageGist(slug=slug, title=slug, type=page_type, sources=sources)


def test_prune_removes_only_pages_every_source_of_which_moved(store, vectors, settings) -> None:
    gists_base = settings.vectorize_gists_index
    rows = [
        _page(store, A, [A], "source"),
        _page(store, "only-a", [A]),
        _page(store, "shared", [A, B]),
        _page(store, "only-b", [B]),
        _page(store, "overview", [A], "overview"),
    ]
    gists_mod.save_gists(store, {row.slug: row for row in rows})
    vectors.upsert(gists_base, ["only-a", "shared"], [[0.1] * 64, [0.2] * 64],
                   [{"slug": "only-a"}, {"slug": "shared"}])

    dry = prune_moved_sources(store, vectors, gists_base, GENERAL, {A}, dry_run=True)
    assert dry.removed == [A, "only-a"] and store.exists(wiki_page("only-a"))

    result = prune_moved_sources(store, vectors, gists_base, GENERAL, {A})

    assert result.removed == [A, "only-a"]
    assert result.mixed == ["shared"]
    assert not store.exists(wiki_page("only-a")) and not store.exists(wiki_source_note(A))
    assert store.exists(wiki_page("shared")) and store.exists(wiki_page("only-b"))
    assert set(gists_mod.load_gists(store)) == {"shared", "only-b", "overview"}
    assert set(vectors._index(gists_base)) == {"shared"}
    assert "only-a" not in store.get(INDEX_KEY).decode()
