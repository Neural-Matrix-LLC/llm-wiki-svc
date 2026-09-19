"""Scheduled per-domain synthesis (Phase 2, plan §21.2 A8): bounded, one call, never on ingest."""

from __future__ import annotations

from tests.doubles import SpyObjectStore
from tests.factories import make_extracted_doc

from llmwiki import tools
from llmwiki.config import Settings
from llmwiki.lexical.memory import MemoryLexicalIndex
from llmwiki.llm.fake import FakeLLM
from llmwiki.models.page import PageGist
from llmwiki.storage.layout import GENERAL, index_key, overview_key
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.compiler import Compiler
from llmwiki.wiki.domains import upsert_domain
from llmwiki.wiki.ledger import CostLedger
from llmwiki.wiki.pages import read_page
from llmwiki.wiki.synthesis import OVERVIEW_SLUG, rank_pages, synthesize_domain


def _populate(store, vectors, embedder, llm, settings, domain=GENERAL, n=3):
    for i in range(n):
        doc = make_extracted_doc(source_id=chr(ord("a") + i) * 16, title=f"Topic {i} Notes")
        Compiler(store, vectors, embedder, llm, settings).compile_source(doc, domain=domain)


def test_rank_pages_prefers_most_sourced_then_newest_and_skips_sources() -> None:
    from datetime import date

    manifest = {
        "a": PageGist(slug="a", title="A", gist="g", sources=["s1"], updated=date(2026, 1, 1)),
        "b": PageGist(slug="b", title="B", gist="g", sources=["s1", "s2"],
                      updated=date(2026, 1, 1)),
        "c": PageGist(slug="c", title="C", gist="g", sources=["s1"], updated=date(2026, 2, 1)),
        "s1": PageGist(slug="s1", title="S", type="source", gist="g", sources=["s1"]),
        "overview": PageGist(slug="overview", title="O", type="overview", gist="g", sources=[]),
    }
    assert [g.slug for g in rank_pages(manifest)] == ["b", "c", "a"]


def test_synthesis_writes_the_overview_page_and_registers_it(store, vectors, embedder, llm,
                                                              settings) -> None:
    _populate(store, vectors, embedder, llm, settings)
    lexical = MemoryLexicalIndex()

    result = synthesize_domain(store, vectors, embedder, llm, settings, lexical=lexical)

    assert result.written and result.pages_read >= 1 and result.slug == OVERVIEW_SLUG
    page = read_page(store, OVERVIEW_SLUG, "overview")
    assert page is not None and page.front_matter.type == "overview"
    assert page.front_matter.version == 1 and page.front_matter.sources
    assert store.exists(overview_key(GENERAL))
    manifest = gists_mod.load_gists(store)
    assert manifest[OVERVIEW_SLUG].type == "overview"
    assert "## Overview" in store.get(index_key(GENERAL)).decode()
    assert lexical.count(settings.vectorize_gists_index) == 1
    records = [r for r in CostLedger(store).read() if r.kind == "synthesis"]
    assert records and records[0].op == "synthesize_domain"

    again = synthesize_domain(store, vectors, embedder, llm, settings)
    assert again.written
    assert read_page(store, OVERVIEW_SLUG, "overview").front_matter.version == 2


def test_synthesis_page_reads_are_bounded_by_setting(store, vectors, embedder, llm, settings):
    """Load-bearing scale guard (plan §21.10): at most SYNTHESIS_MAX_PAGES bodies, one call."""
    _populate(store, vectors, embedder, llm, settings, n=6)
    spy = SpyObjectStore(store)
    cfg = settings.model_copy(update={"synthesis_max_pages": 2})
    llm.calls.clear()

    result = synthesize_domain(spy, vectors, embedder, llm, cfg)

    assert result.pages_read == 2
    bodies = {k for k in spy.get_keys if "/concepts/" in k or "/entities/" in k}
    assert len(bodies) <= 2, bodies
    assert [c["op"] for c in llm.calls] == ["synthesize_domain"]
    assert not spy.lists("wiki/")


def test_empty_domain_is_skipped_without_a_call(store, vectors, embedder, llm, settings) -> None:
    upsert_domain(store, "ml")
    result = synthesize_domain(store, vectors, embedder, llm, settings, "ml")
    assert not result.written and "no concept" in result.reason
    assert llm.calls == [] and not store.exists(overview_key("ml"))


def test_synthesis_into_a_domain_stays_under_its_prefix(store, vectors, embedder, llm, settings):
    upsert_domain(store, "ml", "Machine learning")
    _populate(store, vectors, embedder, llm, settings, domain="ml")
    result = synthesize_domain(store, vectors, embedder, llm, settings, "ml")
    assert result.written
    page = read_page(store, OVERVIEW_SLUG, "overview", "ml")
    assert page is not None and page.front_matter.domain == "ml"
    assert "domain: ml" in store.get(overview_key("ml")).decode()
    assert not store.exists(overview_key(GENERAL))
    assert f"{settings.vectorize_gists_index}-ml" in vectors.index_names()


def test_synthesis_is_never_called_from_ingest_and_the_compiler_never_patches_it(
    store, vectors, embedder, settings,
) -> None:
    llm = FakeLLM()
    _populate(store, vectors, embedder, llm, settings)
    synthesize_domain(store, vectors, embedder, llm, settings)
    llm.calls.clear()

    from tests.factories import make_source_meta

    from llmwiki.pipeline.ingest import IngestPipeline
    from llmwiki.storage.layout import raw_meta, raw_original

    meta = make_source_meta("f" * 16).model_copy(update={"modality": "text", "mime": "text/plain",
                                                          "url": None})
    store.put(raw_original("f" * 16, "txt"), b"# Overview of topics\n\nAn overview.\n",
              "text/plain")
    store.put(raw_meta("f" * 16), meta.model_dump_json().encode(), "application/json")
    status = IngestPipeline(store, vectors, embedder, llm, settings).process("f" * 16)

    assert status.state == "done"
    ops = [c["op"] for c in llm.calls]
    assert "synthesize_domain" not in ops
    assert read_page(store, OVERVIEW_SLUG, "overview").front_matter.version == 1, (
        "the per-source compiler must never patch the overview page")


def test_tools_synthesize_all_and_one_and_unknown(tmp_path) -> None:
    from llmwiki import factory

    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake",
                   local_storage_path=tmp_path / "data", embedding_dim=64, worker_mode="inline")
    factory.reset()
    try:
        tools.upsert_domain("ml", "ML", cfg=cfg)
        tools.ingest_now(text="# Chunking\n\nWindows over text.\n", cfg=cfg)
        tools.ingest_now(text="# Paged attention\n\nKV blocks.\n", domain="ml", cfg=cfg)
        results = tools.synthesize(cfg=cfg)
        assert [r.domain for r in results] == ["general", "ml"] and all(r.written for r in results)
        (one,) = tools.synthesize("ml", cfg=cfg)
        assert one.domain == "ml"
        import pytest

        with pytest.raises(KeyError):
            tools.synthesize("nope", cfg=cfg)
        assert tools.usage_summary(cfg=cfg).by_kind.get("synthesis", 0.0) == 0.0  # fake cost
        assert "synthesis" in tools.usage_summary(cfg=cfg).by_kind
    finally:
        factory.reset()
