"""Compiler behaviour beyond the cost guard: budget aborts, versioning, recording."""

from __future__ import annotations

import pytest
from tests.doubles import ScriptedLLM
from tests.factories import make_extracted_doc, seed_gists

from llmwiki.llm.fake import FakeLLM
from llmwiki.models.plan import CostRecord
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.compiler import Compiler, read_cost_ledger


def test_compile_creates_pages_and_records_the_source(store, vectors, embedder, llm, settings):
    result = Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())

    assert result.created, "an empty wiki should gain pages from the first source"
    manifest = gists_mod.load_gists(store)
    assert manifest, "the manifest must be written"
    for slug in result.created:
        assert manifest[slug].gist, "every page needs a gist or the compiler cannot find it again"
        assert make_extracted_doc().source_id in manifest[slug].sources


def test_compile_writes_a_source_note_linking_back_to_raw(store, vectors, embedder, llm, settings):
    doc = make_extracted_doc()
    Compiler(store, vectors, embedder, llm, settings).compile_source(doc)

    note = store.get(f"wiki/sources/{doc.source_id}.md").decode()
    assert f"raw/{doc.source_id}/" in note
    assert "type: source" in note


def test_compile_regenerates_the_index(store, vectors, embedder, llm, settings):
    Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())
    assert store.exists("wiki/index.md")


def test_cost_is_recorded_per_call(store, vectors, embedder, llm, settings):
    Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())

    ledger = read_cost_ledger(store)
    assert ledger, "every LLM call must append to cost.jsonl - cost is measured, not estimated"
    assert {record.op for record in ledger} >= {"summarize_source", "plan_compile"}
    assert all(record.source_id for record in ledger)


def test_ledger_appends_across_sources(store, vectors, embedder, llm, settings):
    first = make_extracted_doc(source_id="a" * 16)
    second = make_extracted_doc(source_id="b" * 16, title="Chunking Strategies")

    Compiler(store, vectors, embedder, llm, settings).compile_source(first)
    count_after_first = len(read_cost_ledger(store))
    Compiler(store, vectors, embedder, llm, settings).compile_source(second)

    assert len(read_cost_ledger(store)) > count_after_first


def test_budget_abort_marks_the_source_rather_than_running_away(
    store, vectors, embedder, settings, caplog
):
    """INGEST_TOKEN_BUDGET is a hard stop, not a warning."""
    settings.ingest_token_budget = 1

    class Greedy(ScriptedLLM):
        def complete(self, **kwargs):
            response = super().complete(**kwargs)
            response.usage = CostRecord(op=kwargs["op"], model="scripted", input_tokens=10_000)
            return response

    llm = Greedy({
        "summarize_source": {"title": "T", "gist": "g", "summary": "s",
                             "concepts": ["alpha"], "entities": []},
        "plan_compile": {"ops": [{"kind": "create_page", "slug": "alpha", "title": "Alpha"}]},
    })
    with caplog.at_level("WARNING", logger="llmwiki.wiki.compiler"):
        result = Compiler(store, vectors, embedder, llm, settings).compile_source(
            make_extracted_doc()
        )

    assert result.aborted
    assert "INGEST_TOKEN_BUDGET" in result.reason
    assert result.created == []
    assert any(r.levelname == "WARNING" and "aborted" in r.message for r in caplog.records)


def test_planner_creates_are_downgraded_to_patches_for_existing_pages(
    store, vectors, embedder, settings
):
    """A create for an existing slug would silently overwrite a page."""
    seed_gists(store, 1, embedder=embedder, vectors=vectors, index=settings.vectorize_gists_index)
    llm = ScriptedLLM({
        "summarize_source": {"title": "T", "gist": "g", "summary": "s",
                             "concepts": ["topic-0000"], "entities": []},
        "plan_compile": {"ops": [{"kind": "create_page", "slug": "topic-0000",
                                  "title": "Topic 0"}]},
        "patch_page": {"gist": "updated gist", "body": "## Summary\n\nrevised\n"},
    })
    result = Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())

    assert result.created == []
    assert result.patched == ["topic-0000"]


def test_patch_preserves_earlier_sources(store, vectors, embedder, settings):
    seed_gists(store, 1, embedder=embedder, vectors=vectors, index=settings.vectorize_gists_index)
    script = {
        "summarize_source": {"title": "T", "gist": "g", "summary": "s",
                             "concepts": ["topic-0000"], "entities": []},
        "plan_compile": {"ops": [{"kind": "patch_page", "slug": "topic-0000"}]},
        "patch_page": {"gist": "g2", "body": "## Summary\n\nrevised\n"},
    }
    for source_id in ("a" * 16, "b" * 16):
        Compiler(store, vectors, embedder, ScriptedLLM(script), settings).compile_source(
            make_extracted_doc(source_id=source_id)
        )

    manifest = gists_mod.load_gists(store)
    assert manifest["topic-0000"].sources == ["a" * 16, "b" * 16]


def test_empty_plan_is_a_valid_outcome(store, vectors, embedder, settings):
    llm = ScriptedLLM({
        "summarize_source": {"title": "T", "gist": "g", "summary": "s", "concepts": [],
                             "entities": []},
        "plan_compile": {"ops": []},
    })
    result = Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())

    assert result.pages_touched == 0
    assert not result.aborted


@pytest.mark.parametrize("wiki_size", [0, 1, 50])
def test_compile_survives_any_wiki_size(wiki_size, store, vectors, embedder, llm, settings):
    if wiki_size:
        seed_gists(store, wiki_size, embedder=embedder, vectors=vectors,
                   index=settings.vectorize_gists_index)
    result = Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())
    assert not result.aborted


def test_compiler_leaves_model_and_sampling_params_to_the_configured_adapter(
    store, vectors, embedder, settings
):
    """Obsoletes test_llm_max_tokens_and_temperature_reach_every_stage (removed
    2026-09-05, plan §19.3/§19.7.2): every stage now passes only op/system/prompt
    (/schema) - "config fully owns it" means Settings.llm_max_tokens/llm_temperature
    (or a config/ops.py row, in routed mode) are resolved by the adapter
    factory.py built, not read and forwarded by the compiler itself. Also
    covers COMPILE_EXECUTOR_MODEL's removal: create_page/patch_page pass no
    model= either."""
    llm = FakeLLM()
    Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())

    assert llm.calls, "the scripted stages above must have actually run"
    for call in llm.calls:
        assert call["model"] is None
        assert call["max_tokens"] is None
        assert call["temperature"] is None
