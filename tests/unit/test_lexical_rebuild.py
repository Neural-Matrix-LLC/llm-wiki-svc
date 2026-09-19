"""``llmwiki lexical rebuild`` equals what incremental ingest writes (Phase 2, plan §21.2 B2)."""

from __future__ import annotations

from tests.factories import make_source_meta

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.lexical.memory import MemoryLexicalIndex
from llmwiki.llm.fake import FakeLLM
from llmwiki.pipeline.ingest import IngestPipeline
from llmwiki.pipeline.lexical_rebuild import rebuild
from llmwiki.storage.layout import raw_meta, raw_original
from llmwiki.wiki.domains import upsert_domain


def _capture(store, source_id: str, text: str, domain: str | None = None) -> None:
    meta = make_source_meta(source_id).model_copy(
        update={"modality": "text", "mime": "text/plain", "url": None, "domain": domain})
    store.put(raw_original(source_id, "txt"), text.encode(), "text/plain")
    store.put(raw_meta(source_id), meta.model_dump_json().encode(), "application/json")


def _snapshot(lexical: MemoryLexicalIndex) -> dict[str, dict[str, str]]:
    return {index: {doc_id: doc["text"] for doc_id, doc in lexical._data[index].items()}
            for index in lexical.index_names() if lexical._data[index]}


def test_rebuild_reproduces_incremental_rows_per_domain(store, vectors, embedder, settings):
    upsert_domain(store, "ml")
    incremental = MemoryLexicalIndex()
    pipeline = IngestPipeline(store, vectors, embedder, FakeLLM(), settings, lexical=incremental)
    _capture(store, "a" * 16, "# Chunking\n\nWindows over text for retrieval.\n")
    _capture(store, "b" * 16, "# Paged attention\n\nKV cache blocks on the GPU.\n", domain="ml")
    assert pipeline.process("a" * 16).state == "done"
    assert pipeline.process("b" * 16).state == "done"

    rebuilt = MemoryLexicalIndex()
    counts = rebuild(store, rebuilt, settings)

    assert _snapshot(rebuilt) == _snapshot(incremental)
    assert counts[settings.vectorize_chunks_index] >= 1
    assert counts[f"{settings.vectorize_chunks_index}-ml"] >= 1
    assert counts[f"{settings.vectorize_gists_index}-ml"] >= 1


def test_rebuild_of_one_domain_resets_only_that_domain(store, vectors, embedder, settings):
    upsert_domain(store, "ml")
    lexical = MemoryLexicalIndex()
    pipeline = IngestPipeline(store, vectors, embedder, FakeLLM(), settings, lexical=lexical)
    _capture(store, "a" * 16, "# Chunking\n\nWindows over text.\n")
    _capture(store, "b" * 16, "# Paged attention\n\nKV cache blocks.\n", domain="ml")
    pipeline.process("a" * 16)
    pipeline.process("b" * 16)
    lexical.upsert(settings.vectorize_chunks_index, ["stale:0"], ["stale"], [{"source_id": "x"}])

    rebuild(store, lexical, settings, domain="ml")

    assert lexical.count(settings.vectorize_chunks_index) >= 2, "general untouched (stale row kept)"
    assert lexical.query(f"{settings.vectorize_chunks_index}-ml", "kv cache")


def test_tools_rebuild_refuses_when_no_lexical_backend(tmp_path) -> None:
    from llmwiki import factory

    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake", local_storage_path=tmp_path,
                   embedding_dim=64, lexical_backend="none")
    factory.reset()
    try:
        import pytest

        with pytest.raises(RuntimeError):
            tools.rebuild_lexical(cfg=cfg)
    finally:
        factory.reset()


def test_tools_rebuild_with_sqlite_writes_files_under_the_lexical_root(tmp_path) -> None:
    from llmwiki import factory
    from llmwiki.lexical.sqlite import fts5_available

    if not fts5_available():
        import pytest

        pytest.skip("sqlite3 built without FTS5")
    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake",
                   local_storage_path=tmp_path / "data", embedding_dim=64,
                   lexical_backend="sqlite", worker_mode="inline")
    factory.reset()
    try:
        tools.ingest_now(text="# Firmware\n\nThe XK-7781 controller.\n", cfg=cfg)
        counts = tools.rebuild_lexical(cfg=cfg)
        assert counts[cfg.vectorize_chunks_index] == 1
        assert (tmp_path / "data" / "lexical" / f"{cfg.vectorize_chunks_index}.sqlite").exists()
        assert tools.health(cfg)["lexical"]["backend"] == "sqlite"
        assert tools.search_wiki("XK-7781", cfg=cfg), "hybrid search still answers"
        lexical = factory.lexical_index(cfg)
        assert lexical.query(cfg.vectorize_chunks_index, "XK-7781")[0].source_id
    finally:
        factory.reset()
