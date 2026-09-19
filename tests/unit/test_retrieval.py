"""Rank fusion in the retrieval seam (Phase 2, plan §21.2 B3/B4).

``retrieve_layer`` with no lexical index is the pre-Phase-2 call (pinned in
``test_query_scopes.py``); this file pins what changes when the keyword half
is present: fusion by rank, the dense cosine surviving as ``dense_score`` for
the confidence gate, and an exact-term hit dense retrieval misses.
"""

from __future__ import annotations

from tests.factories import make_extracted_doc

from llmwiki.agent.query import QueryAgent
from llmwiki.agent.retrieval import RRF_K, gate_score, retrieve_layer, rrf_fuse
from llmwiki.lexical.memory import MemoryLexicalIndex
from llmwiki.models.chunk import SearchHit
from llmwiki.models.source import SourceMeta
from llmwiki.pipeline.ingest import IngestPipeline
from llmwiki.storage.layout import GENERAL, raw_meta, raw_original
from llmwiki.wiki.compiler import Compiler
from llmwiki.wiki.domains import DomainScope


def _hit(hit_id: str, score: float, **kw) -> SearchHit:
    return SearchHit(id=hit_id, score=score, **kw)


# --- rrf_fuse ------------------------------------------------------------------------------


def test_rrf_scores_are_sums_of_reciprocal_ranks() -> None:
    dense = [_hit("a", 0.9, dense_score=0.9), _hit("b", 0.8, dense_score=0.8)]
    lexical = [_hit("b", 5.0, lexical_score=5.0), _hit("c", 4.0, lexical_score=4.0)]

    fused = rrf_fuse([dense, lexical])

    assert [h.id for h in fused] == ["b", "a", "c"]
    b, a, c = fused
    assert b.score == 1 / (RRF_K + 2) + 1 / (RRF_K + 1)
    assert a.score == 1 / (RRF_K + 1) and c.score == 1 / (RRF_K + 2)
    assert (b.dense_score, b.lexical_score) == (0.8, 5.0), "both scores survive the merge"
    assert a.lexical_score is None and c.dense_score is None


def test_rrf_ties_break_deterministically_by_id() -> None:
    fused = rrf_fuse([[_hit("z", 1.0)], [_hit("a", 1.0)]])
    assert [h.id for h in fused] == ["a", "z"]


def test_gate_score_reads_the_cosine_not_the_fused_score() -> None:
    fused = rrf_fuse([[_hit("a", 0.91, dense_score=0.91)], [_hit("a", 7.0, lexical_score=7.0)]])
    assert fused[0].score < 0.1, "an RRF score is never on the cosine scale"
    assert gate_score(fused[0]) == 0.91
    assert gate_score(_hit("lexical-only", 0.03, lexical_score=3.0)) == 0.03


# --- retrieve_layer with a lexical index ---------------------------------------------------


class _FixedVectors:
    """Returns the same dense list for any query - isolates the fusion logic."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, int]] = []

    def query(self, index, vector, k=5, where=None):
        self.calls.append((index, k))
        return [h.model_copy() for h in self.hits[:k]]


def test_hybrid_layer_pools_fuses_and_keeps_the_cosine(settings) -> None:
    cfg = settings.model_copy(update={"hybrid_pool_k": 7})
    lexical = MemoryLexicalIndex()
    lexical.upsert(cfg.vectorize_chunks_index, ["s2:0", "s3:0"],
                   ["the XK-7781 controller firmware", "unrelated text"],
                   [{"source_id": "s2", "text": "XK"}, {"source_id": "s3", "text": "u"}])
    vectors = _FixedVectors([
        SearchHit(id="s1:0", score=0.80, source_id="s1", metadata={"source_id": "s1"}),
        SearchHit(id="s2:0", score=0.20, source_id="s2", metadata={"source_id": "s2"}),
    ])

    hits = retrieve_layer("chunks", "XK-7781 firmware", [0.0], [DomainScope(GENERAL)], 2,
                          vectors=vectors, settings=cfg, lexical=lexical)

    assert vectors.calls == [(cfg.vectorize_chunks_index, 7)], "pool, not k, goes to the store"
    assert [h.id for h in hits] == ["s2:0", "s1:0"], "the exact-term hit wins on fused rank"
    top = hits[0]
    assert top.dense_score == 0.20 and top.lexical_score is not None
    assert gate_score(top) == 0.20, "the gate still sees the cosine"
    assert all(h.domain == GENERAL for h in hits)


def test_hybrid_layer_over_two_scopes_queries_both_halves_of_each(settings) -> None:
    lexical = MemoryLexicalIndex()
    base = settings.vectorize_gists_index
    lexical.upsert(f"{base}-ml", ["paged"], ["Paged attention"], [{"slug": "paged", "text": "p"}])
    vectors = _FixedVectors([])
    hits = retrieve_layer("gists", "paged attention", [0.0],
                          [DomainScope(GENERAL), DomainScope("ml")], 5,
                          vectors=vectors, settings=settings, lexical=lexical)
    assert [c[0] for c in vectors.calls] == [base, f"{base}-ml"]
    assert [(h.slug, h.domain) for h in hits] == [("paged", "ml")]


def test_lexical_only_hit_does_not_open_the_wiki_gate(settings) -> None:
    """A keyword-only wiki hit has no cosine; it must not count as 'the wiki answers'."""
    lexical = MemoryLexicalIndex()
    lexical.upsert(settings.vectorize_gists_index, ["p"], ["term"], [{"slug": "p", "text": "t"}])
    hits = retrieve_layer("gists", "term", [0.0], [DomainScope(GENERAL)], 5,
                          vectors=_FixedVectors([]), settings=settings, lexical=lexical)
    assert hits and hits[0].dense_score is None
    assert gate_score(hits[0]) < QueryAgent.wiki_confidence


# --- end to end: ingest writes both halves; an exact-term question is found -----------------


def test_ingest_writes_lexical_rows_and_delete_removes_them(store, vectors, embedder, settings):
    from llmwiki.llm.fake import FakeLLM

    cfg = settings.model_copy(update={"lexical_backend": "memory"})
    lexical = MemoryLexicalIndex()
    meta = SourceMeta(source_id="a" * 16, modality="text", title="Firmware", mime="text/plain",
                      sha256="0" * 64)
    text = "# Firmware\n\nThe XK-7781 controller ships with firmware 4.2.\n"
    store.put(raw_original("a" * 16, "txt"), text.encode(), "text/plain")
    store.put(raw_meta("a" * 16), meta.model_dump_json().encode(), "application/json")
    pipeline = IngestPipeline(store, vectors, embedder, FakeLLM(), cfg, lexical=lexical)

    status = pipeline.process("a" * 16)

    assert status.state == "done"
    chunk_index = cfg.vectorize_chunks_index
    assert lexical.count(chunk_index) == status.chunk_count >= 1
    assert lexical.count(cfg.vectorize_gists_index) >= 1, "gist rows mirrored by _sync_gist"
    assert lexical.query(chunk_index, "XK-7781")[0].source_id == "a" * 16

    agent = QueryAgent(store, vectors, embedder, FakeLLM(), cfg, lexical=lexical)
    hits = agent.search("XK-7781")
    assert any(h.source_id == "a" * 16 for h in hits)

    assert lexical.delete_by_source(chunk_index, "a" * 16) == status.chunk_count


def test_compiler_mirrors_gists_into_the_lexical_index(store, vectors, embedder, llm, settings):
    lexical = MemoryLexicalIndex()
    result = Compiler(store, vectors, embedder, llm, settings, lexical=lexical).compile_source(
        make_extracted_doc())
    assert result.created
    slugs = {h.slug for h in lexical.query(settings.vectorize_gists_index, result.created[0])}
    assert result.created[0] in slugs
