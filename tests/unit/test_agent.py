"""Wiki-first retrieval, RAG fallback, and citations that resolve."""

from __future__ import annotations

import pytest
from tests.factories import make_extracted_doc

from llmwiki.agent.query import QueryAgent
from llmwiki.models.source import SourceMeta
from llmwiki.storage.layout import raw_meta
from llmwiki.wiki.compiler import Compiler


class RecordingVectors:
    """Wraps a vector store and records which indexes were queried."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.queried: list[str] = []

    def upsert(self, index, ids, vectors, metadata):
        self.inner.upsert(index, ids, vectors, metadata)

    def query(self, index, vector, k=5, where=None):
        self.queried.append(index)
        return self.inner.query(index, vector, k=k, where=where)

    def delete_by_source(self, index, source_id):
        return self.inner.delete_by_source(index, source_id)


@pytest.fixture
def populated(store, vectors, embedder, llm, settings):
    """A wiki with one compiled source and its raw meta on disk."""
    doc = make_extracted_doc()
    store.put(
        raw_meta(doc.source_id),
        SourceMeta(source_id=doc.source_id, modality="web", sha256="0" * 64,
                   url=doc.url, title=doc.title).model_dump_json().encode(),
        "application/json",
    )
    Compiler(store, vectors, embedder, llm, settings).compile_source(doc)
    return doc


def test_wiki_is_searched_before_the_chunk_index(
    populated, store, vectors, embedder, llm, settings
) -> None:
    recording = RecordingVectors(vectors)
    agent = QueryAgent(store, recording, embedder, llm, settings)

    agent.answer("retrieval augmented generation chunking")

    assert recording.queried[0] == settings.vectorize_gists_index, (
        "the compiled wiki must be consulted first; querying chunks first wastes the compilation"
    )


def test_chunk_index_is_untouched_when_the_wiki_answers(
    populated, store, vectors, embedder, llm, settings
) -> None:
    recording = RecordingVectors(vectors)
    agent = QueryAgent(store, recording, embedder, llm, settings)

    result = agent.answer("retrieval augmented generation")

    if not result.used_rag_fallback:
        assert settings.vectorize_chunks_index not in recording.queried


def test_rag_fallback_engages_when_the_wiki_is_empty(store, vectors, embedder, llm, settings):
    recording = RecordingVectors(vectors)
    agent = QueryAgent(store, recording, embedder, llm, settings)

    result = agent.answer("something the wiki has never heard of")

    assert result.used_rag_fallback is True
    assert settings.vectorize_chunks_index in recording.queried


def test_every_citation_resolves_to_a_real_raw_object(
    populated, store, vectors, embedder, llm, settings
) -> None:
    agent = QueryAgent(store, vectors, embedder, llm, settings)

    result = agent.answer("retrieval augmented generation")

    assert result.citations, "an answer grounded in the wiki must carry citations"
    for citation in result.citations:
        assert agent.source_exists(citation.source_id), f"dangling citation {citation.source_id}"


def test_a_citation_to_a_deleted_source_is_dropped(
    populated, store, vectors, embedder, llm, settings
) -> None:
    """A citation that no longer resolves must not reach the caller."""
    store.delete(raw_meta(populated.source_id))
    agent = QueryAgent(store, vectors, embedder, llm, settings)

    result = agent.answer("retrieval augmented generation")
    assert all(agent.source_exists(c.source_id) for c in result.citations)


def test_empty_knowledge_base_says_so_rather_than_inventing(
    store, vectors, embedder, llm, settings
) -> None:
    agent = QueryAgent(store, vectors, embedder, llm, settings)

    result = agent.answer("what is retrieval augmented generation?")
    assert result.citations == []
    assert "Nothing in the knowledge base" in result.text


def test_source_exists_rejects_a_malformed_id(store, vectors, embedder, llm, settings) -> None:
    agent = QueryAgent(store, vectors, embedder, llm, settings)
    assert agent.source_exists("../../etc/passwd") is False
