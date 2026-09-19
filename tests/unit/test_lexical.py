"""The lexical index contract (Phase 2, plan §21.2 B1/B2), over both backends.

Parametrised like ``test_vector_contract.py``: the same assertions against the
in-memory BM25 and the SQLite FTS5 file - so either can stand in for the
other, and a rebuilt file equals an incrementally written one.
"""

from __future__ import annotations

import pytest

from llmwiki.lexical.base import LexicalIndex, tokenize
from llmwiki.lexical.memory import MemoryLexicalIndex
from llmwiki.lexical.sqlite import SqliteLexicalIndex, fts5_available, fts_query

pytestmark = pytest.mark.skipif(not fts5_available(), reason="sqlite3 built without FTS5")


@pytest.fixture(params=["memory", "sqlite"])
def lexical(request, tmp_path) -> LexicalIndex:
    if request.param == "memory":
        return MemoryLexicalIndex()
    return SqliteLexicalIndex(tmp_path / "lexical")


def _seed(index: LexicalIndex) -> None:
    index.upsert("idx", ["s1:0", "s1:1", "s2:0"], [
        "PagedAttention keeps KV-cache blocks resident on the GPU.",
        "Chunking strategies split documents into overlapping windows.",
        "The bge-m3 embedder handles retrieval augmented generation queries.",
    ], [
        {"source_id": "s1", "chunk_index": 0, "text": "Paged..."},
        {"source_id": "s1", "chunk_index": 1, "text": "Chunk..."},
        {"source_id": "s2", "chunk_index": 0, "text": "bge..."},
    ])


def test_both_backends_satisfy_the_protocol(lexical) -> None:
    assert isinstance(lexical, LexicalIndex)


def test_exact_term_query_finds_the_document(lexical) -> None:
    _seed(lexical)
    hits = lexical.query("idx", "PagedAttention", k=5)
    assert [h.id for h in hits] == ["s1:0"]
    assert hits[0].source_id == "s1" and hits[0].lexical_score is not None
    assert hits[0].score > 0 and hits[0].dense_score is None
    assert hits[0].origin == "chunk" and hits[0].text.startswith("PagedAttention")


def test_identifiers_with_hyphens_and_stemming_work(lexical) -> None:
    _seed(lexical)
    assert [h.id for h in lexical.query("idx", "bge-m3", k=5)] == ["s2:0"]
    assert [h.id for h in lexical.query("idx", "kv-cache", k=5)] == ["s1:0"]
    # "chunking" ~ "chunk" (porter); "documents" ~ "document".
    assert lexical.query("idx", "chunk document", k=5)[0].id == "s1:1"


def test_ranking_prefers_documents_matching_more_terms(lexical) -> None:
    _seed(lexical)
    hits = lexical.query("idx", "retrieval augmented generation embedder", k=5)
    assert hits and hits[0].id == "s2:0"


def test_query_syntax_in_user_input_is_neutralised(lexical) -> None:
    _seed(lexical)
    for hostile in ['"foo" OR bar)(', 'NEAR(a b)', 'text: gpu*', '"', "'; DROP TABLE docs; --"]:
        lexical.query("idx", hostile, k=5)  # must not raise
    assert [h.id for h in lexical.query("idx", '"GPU"', k=5)] == ["s1:0"]


def test_fts_query_quotes_every_term_and_joins_with_or() -> None:
    assert fts_query('kv-cache "GPU" NEAR(x)') == '"kv-cache" OR "gpu" OR "near" OR "x"'
    assert fts_query("   ") == "" and fts_query('""') == ""
    assert tokenize("PagedAttention, bge-m3 v2.1!") == ["pagedattention", "bge-m3", "v2.1"]


def test_upsert_is_idempotent_and_replaces_text(lexical) -> None:
    _seed(lexical)
    lexical.upsert("idx", ["s1:0"], ["Completely different content now."],
                   [{"source_id": "s1", "chunk_index": 0}])
    assert lexical.count("idx") == 3
    assert lexical.query("idx", "PagedAttention", k=5) == []
    assert [h.id for h in lexical.query("idx", "different content", k=5)] == ["s1:0"]


def test_delete_by_source_removes_only_that_source(lexical) -> None:
    _seed(lexical)
    assert lexical.delete_by_source("idx", "s1") == 2
    assert lexical.count("idx") == 1
    assert lexical.query("idx", "PagedAttention", k=5) == []
    assert lexical.delete_by_source("idx", "nope") == 0


def test_reset_empties_the_index(lexical) -> None:
    _seed(lexical)
    lexical.reset("idx")
    assert lexical.count("idx") == 0 and lexical.query("idx", "GPU", k=5) == []


def test_an_index_that_was_never_built_serves_nothing(lexical) -> None:
    assert lexical.query("never-built", "anything", k=5) == []
    assert lexical.count("never-built") == 0
    assert lexical.delete_by_source("never-built", "s1") == 0


def test_empty_and_blank_queries_return_nothing(lexical) -> None:
    _seed(lexical)
    assert lexical.query("idx", "", k=5) == []
    assert lexical.query("idx", "!!! ???", k=5) == []


def test_sqlite_files_are_one_per_index_and_names_are_validated(tmp_path) -> None:
    index = SqliteLexicalIndex(tmp_path / "lex")
    index.upsert("llmwiki-chunks", ["a:0"], ["alpha"], [{"source_id": "a"}])
    index.upsert("llmwiki-chunks-ml", ["b:0"], ["beta"], [{"source_id": "b"}])
    assert {p.name for p in (tmp_path / "lex").iterdir()} == {
        "llmwiki-chunks.sqlite", "llmwiki-chunks-ml.sqlite"}
    assert [h.id for h in index.query("llmwiki-chunks-ml", "beta")] == ["b:0"]
    assert index.query("llmwiki-chunks", "beta") == []
    with pytest.raises(ValueError):
        index.path("../escape")
