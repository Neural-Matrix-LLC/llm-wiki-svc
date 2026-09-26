"""The domain router (Phase 2, plan §21.2 A4/A5, §21.6.3) and the pipeline's cheapest-first order.

The two load-bearing properties: a general-only registry never calls the
model, and the model can never file a source under a domain that is not
registered - an unknown name or a low confidence becomes ``general`` with the
pick preserved as ``suggested_domain``.
"""
"""RoutingLLMClient dispatch - implement-plan.md Part II §19.3/§19.7.3."""

from __future__ import annotations

import json

from tests.doubles import ScriptedLLM
from tests.factories import make_extracted_doc, make_source_meta

from llmwiki.llm.fake import FakeLLM
from llmwiki.models.page import Domain, DomainRegistry
from llmwiki.pipeline.ingest import IngestPipeline
from llmwiki.storage.layout import raw_extracted, raw_meta, raw_original, raw_routing
from llmwiki.wiki.domains import upsert_domain
from llmwiki.wiki.ledger import CostLedger
from llmwiki.wiki.router import DomainRouter


def _registry(*names: str) -> DomainRegistry:
    return DomainRegistry(domains={n: Domain(name=n, description=f"About {n}") for n in names})


def _scripted(**decision) -> ScriptedLLM:
    return ScriptedLLM({"route_domain": decision})


# --- DomainRouter.route_source ------------------------------------------------------------


def test_confident_registered_pick_is_taken() -> None:
    llm = _scripted(domain="ml", confidence=0.93, reason="GPU scheduling paper")
    assignment, usage = DomainRouter(llm, _registry("ml", "bio")).route_source(make_extracted_doc())
    assert (assignment.domain, assignment.confidence) == ("ml", 0.93)
    assert assignment.suggested_domain == "" and not assignment.explicit
    assert len(usage) == 1 and usage[0].op == "route_domain"
    call = llm.calls[0]
    assert call["op"] == "route_domain"
    assert set(call["schema"]["properties"]["domain"]["enum"]) == {"general", "ml", "bio"}
    assert "About ml" in call["prompt"] and "Retrieval-Augmented Generation" in call["prompt"]


def test_low_confidence_is_demoted_to_general_and_kept_as_a_suggestion() -> None:
    llm = _scripted(domain="ml", confidence=0.4)
    assignment, _ = DomainRouter(llm, _registry("ml"), min_confidence=0.6).route_source(
        make_extracted_doc())
    assert assignment.domain == "general"
    assert assignment.suggested_domain == "ml"
    assert "0.40" in assignment.reason


def test_an_unregistered_name_can_never_be_a_destination() -> None:
    llm = _scripted(domain="Quantum Chemistry!", confidence=0.99)
    assignment, _ = DomainRouter(llm, _registry("ml")).route_source(make_extracted_doc())
    assert assignment.domain == "general"
    assert assignment.suggested_domain == "quantum-chemistry", "slugified, not invented"


def test_explicit_suggestion_is_normalised_and_dropped_when_it_names_a_registered_domain() -> None:
    llm = _scripted(domain="general", confidence=0.9, suggested_domain="ML")
    assignment, _ = DomainRouter(llm, _registry("ml")).route_source(make_extracted_doc())
    assert assignment.domain == "general" and assignment.suggested_domain == "", (
        "a suggestion that is already registered is noise; the model should have picked it"
    )
    llm = _scripted(domain="general", confidence=0.9, suggested_domain="Power Systems")
    assignment, _ = DomainRouter(llm, _registry("ml")).route_source(make_extracted_doc())
    assert assignment.suggested_domain == "power-systems"


def test_garbage_from_the_model_falls_back_to_general() -> None:
    llm = _scripted(domain=None, confidence="high")
    assignment, _ = DomainRouter(llm, _registry("ml")).route_source(make_extracted_doc())
    assert assignment.domain == "general" and assignment.confidence == 0.0


def test_route_query_returns_registered_names_only_capped() -> None:
    llm = ScriptedLLM({"route_domain": {"domains": ["ml", "nope", "ml", "bio", "general"]}})
    chosen, usage = DomainRouter(llm, _registry("ml", "bio")).route_query("q", max_domains=2)
    assert chosen == ["ml", "bio"] and len(usage) == 1
    assert llm.calls[0]["schema"]["properties"]["domains"]["maxItems"] == 2


def test_route_query_with_nothing_usable_means_search_everything() -> None:
    llm = ScriptedLLM({"route_domain": {"domains": ["nope"]}})
    chosen, _ = DomainRouter(llm, _registry("ml")).route_query("q")
    assert chosen == []


# --- pipeline.route: cheapest first -------------------------------------------------------


def _capture_text(store, source_id: str, text: str, domain: str | None = None):
    meta = make_source_meta(source_id).model_copy(
        update={"modality": "text", "mime": "text/plain", "url": None, "domain": domain})
    store.put(raw_original(source_id, "txt"), text.encode(), "text/plain")
    store.put(raw_meta(source_id), meta.model_dump_json().encode(), "application/json")
    store.put(raw_extracted(source_id), text.encode(), "text/markdown")
    return meta


def _pipeline(store, vectors, embedder, llm, settings) -> IngestPipeline:
    return IngestPipeline(store, vectors, embedder, llm, settings)


def test_general_only_registry_makes_no_model_call(store, vectors, embedder, settings) -> None:
    """Load-bearing (plan §21.10): the single-domain wiki pays nothing for routing."""
    llm = FakeLLM()
    meta = _capture_text(store, "a" * 16, "Some text about GPU scheduling.")
    assignment = _pipeline(store, vectors, embedder, llm, settings).route(
        meta, make_extracted_doc(source_id="a" * 16))
    assert assignment.domain == "general" and assignment.reason == "no domains registered"
    assert llm.calls == []
    assert json.loads(store.get(raw_routing("a" * 16)))["domain"] == "general"


def test_explicit_domain_never_calls_the_router(store, vectors, embedder, settings) -> None:
    upsert_domain(store, "ml")
    llm = FakeLLM()
    meta = _capture_text(store, "a" * 16, "text", domain="ml")
    assignment = _pipeline(store, vectors, embedder, llm, settings).route(
        meta, make_extracted_doc(source_id="a" * 16))
    assert assignment.domain == "ml" and assignment.explicit
    assert llm.calls == []


def test_routing_off_skips_the_router_even_with_domains(store, vectors, embedder, settings):
    upsert_domain(store, "ml")
    llm = FakeLLM()
    cfg = settings.model_copy(update={"domain_routing": "off"})
    meta = _capture_text(store, "a" * 16, "GPU scheduling for ml.")
    assignment = _pipeline(store, vectors, embedder, llm, cfg).route(
        meta, make_extracted_doc(source_id="a" * 16))
    assert assignment.domain == "general" and assignment.reason == "DOMAIN_ROUTING=off"
    assert llm.calls == []


def test_with_domains_registered_the_router_runs_once_and_is_ledgered(
    store, vectors, embedder, settings,
) -> None:
    upsert_domain(store, "ml", "Machine learning systems")
    llm = ScriptedLLM({"route_domain": {"domain": "ml", "confidence": 0.9}})
    meta = _capture_text(store, "a" * 16, "GPU scheduling.")
    assignment = _pipeline(store, vectors, embedder, llm, settings).route(
        meta, make_extracted_doc(source_id="a" * 16))
    assert assignment.domain == "ml"
    assert [c["op"] for c in llm.calls] == ["route_domain"]
    (record,) = CostLedger(store).read()
    assert (record.op, record.kind, record.domain, record.source_id) == (
        "route_domain", "ingest", "ml", "a" * 16)


def test_process_routes_then_embeds_and_compiles_into_that_domain(
    store, vectors, embedder, settings,
) -> None:
    """End to end through process(): routing happens before the chunk index is chosen."""
    upsert_domain(store, "ml", "Machine learning systems")
    llm = FakeLLM({"route_domain": {"domain": "ml", "confidence": 0.95}})
    _capture_text(store, "a" * 16, "# Paged attention\n\nKV cache blocks stay on the GPU.\n")

    status = _pipeline(store, vectors, embedder, llm, settings).process("a" * 16)

    assert status.state == "done" and status.domain == "ml", status
    assert f"{settings.vectorize_chunks_index}-ml" in vectors.index_names()
    assert settings.vectorize_chunks_index not in vectors.index_names()
    assert store.exists("wiki/domains/ml/_meta/gists.json")
    assert not store.exists("wiki/_meta/gists.json")
    assert [c["op"] for c in llm.calls][0] == "route_domain", "routing precedes summarize"


def test_compile_update_reuses_the_persisted_decision_without_re_routing(
    store, vectors, embedder, settings,
) -> None:
    upsert_domain(store, "ml")
    llm = FakeLLM({"route_domain": {"domain": "ml", "confidence": 0.95}})
    _capture_text(store, "a" * 16, "# Paged attention\n\nKV cache blocks stay on the GPU.\n")
    pipeline = _pipeline(store, vectors, embedder, llm, settings)
    pipeline.process("a" * 16)
    routed_calls = sum(1 for c in llm.calls if c["op"] == "route_domain")

    from llmwiki.wiki.compiler import Compiler

    domain = pipeline.load_routing("a" * 16).domain
    result = Compiler(store, vectors, embedder, llm, settings).compile_source(
        pipeline.extract(pipeline.load_meta("a" * 16)), domain=domain)
    assert result.domain == "ml" and result.reason.startswith("source already compiled")
    assert sum(1 for c in llm.calls if c["op"] == "route_domain") == routed_calls
