"""The domain registry, per-domain compilation and the scale guards (Phase 2, plan §21.2 A1-A3).

Two load-bearing properties are pinned here and belong on ``CLAUDE.md``'s list:

* ``test_general_only_wiki_is_byte_identical_to_phase1`` - with nothing
  registered, every object the compiler writes equals what the Phase 0/1 code
  wrote for the same input (fixture generated from the pre-Phase-2 compiler).
* ``test_compile_never_loads_another_domains_manifest`` - compiling into one
  domain reads that domain's manifest and no other; per-ingest cost is
  proportional to the domain, never the corpus.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
from tests.doubles import SpyObjectStore
from tests.factories import make_extracted_doc

from llmwiki.models.page import Domain, DomainRegistry, PageGist
from llmwiki.storage.layout import (
    DOMAINS_KEY,
    GENERAL,
    GISTS_KEY,
    INDEX_KEY,
    WIKI_PREFIX,
    domain_prefix,
    gists_key,
    index_key,
)
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.compiler import Compiler
from llmwiki.wiki.domains import (
    DomainScope,
    UnknownDomain,
    load_registry,
    remove_domain,
    render_domains_section,
    require_domain,
    save_registry,
    upsert_domain,
)
from llmwiki.wiki.lint import lint_wiki
from llmwiki.wiki.pages import read_page

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "phase1_general_wiki" / "objects.json"


# --- registry --------------------------------------------------------------------------


def test_absent_registry_is_general_only(store) -> None:
    registry = load_registry(store)
    assert registry.is_general_only
    assert registry.names() == [GENERAL]
    assert registry.has(GENERAL) and not registry.has("ml")


def test_upsert_registers_creates_and_updates_without_touching_created(store) -> None:
    first = upsert_domain(store, "ml-systems", "Training and serving infra")
    assert first.created == datetime.date.today()
    again = upsert_domain(store, "ml-systems", "Serving infra, GPU scheduling")
    assert again.created == first.created and again.description.startswith("Serving")
    kept = upsert_domain(store, "ml-systems")  # empty description keeps the old one
    assert kept.description == again.description

    registry = load_registry(store)
    assert registry.names() == [GENERAL, "ml-systems"]
    assert json.loads(store.get(DOMAINS_KEY))["version"] == 1


def test_general_cannot_be_registered_and_reserved_names_are_refused(store) -> None:
    with pytest.raises(ValueError):
        upsert_domain(store, GENERAL)
    with pytest.raises(ValueError):
        upsert_domain(store, "concepts")
    assert not store.exists(DOMAINS_KEY)


def test_require_domain_resolves_none_to_general_and_rejects_unknown(store) -> None:
    upsert_domain(store, "ml")
    registry = load_registry(store)
    assert require_domain(registry, None) == GENERAL
    assert require_domain(registry, GENERAL) == GENERAL
    assert require_domain(registry, "ml") == "ml"
    with pytest.raises(UnknownDomain):
        require_domain(registry, "bio")
    with pytest.raises(ValueError):
        require_domain(registry, "Not Valid")


def test_remove_refuses_a_domain_with_pages_unless_forced(store) -> None:
    upsert_domain(store, "ml")
    gists_mod.save_gists(store, {"p": PageGist(slug="p", title="P", gist="g")}, "ml")
    with pytest.raises(ValueError):
        remove_domain(store, "ml")
    assert remove_domain(store, "ml", force=True) is True
    assert remove_domain(store, "ml") is False, "already gone"
    assert store.exists(gists_key("ml")), "removal never deletes pages or manifests"


def test_domains_section_renders_from_the_registry_alone() -> None:
    assert render_domains_section(DomainRegistry()) == []
    registry = DomainRegistry(domains={"ml": Domain(name="ml", description="Machine learning")})
    lines = render_domains_section(registry)
    assert lines[0] == "## Domains"
    assert "- [[domains/ml/index|ml]] - Machine learning" in lines


def test_domain_scope_derives_every_key_and_index_name() -> None:
    scope = DomainScope("ml")
    assert not scope.is_general
    assert scope.prefix == domain_prefix("ml")
    assert scope.gists_key == gists_key("ml") and scope.index_key == index_key("ml")
    assert scope.page_key("rag") == "wiki/domains/ml/concepts/rag.md"
    assert scope.index_name("llmwiki-gists") == "llmwiki-gists-ml"
    assert DomainScope(GENERAL).is_general and DomainScope(GENERAL).gists_key == GISTS_KEY


# --- byte identity -----------------------------------------------------------------------


def _normalise(text: str) -> str:
    return text.replace(datetime.date.today().isoformat(), "<TODAY>")


def test_general_only_wiki_is_byte_identical_to_phase1(store, vectors, embedder, llm, settings):
    """Load-bearing. Same two fixture sources, same doubles, same settings as the
    pre-Phase-2 compiler that generated the fixture: every wiki object matches."""
    expected = json.loads(FIXTURE.read_text())
    compiler = Compiler(store, vectors, embedder, llm, settings)
    compiler.compile_source(make_extracted_doc())
    compiler.compile_source(make_extracted_doc(source_id="b" * 16, title="Chunking Strategies"))

    actual = {
        key: _normalise(store.get(key).decode())
        for key in store.list(WIKI_PREFIX)
        if not key.startswith("wiki/_meta/cost")
    }
    assert set(actual) == set(expected), "a general-only compile wrote a different set of objects"
    for key in expected:
        assert actual[key] == expected[key], f"{key} differs from the Phase 1 output"


def test_general_index_gains_a_domains_section_only_when_domains_exist(store, vectors, embedder,
                                                                         llm, settings):
    Compiler(store, vectors, embedder, llm, settings).compile_source(make_extracted_doc())
    assert "## Domains" not in store.get(INDEX_KEY).decode()

    upsert_domain(store, "ml", "Machine learning")
    Compiler(store, vectors, embedder, llm, settings).compile_source(
        make_extracted_doc(source_id="b" * 16, title="Chunking Strategies"))
    index = store.get(INDEX_KEY).decode()
    assert "## Domains" in index and "[[domains/ml/index|ml]] - Machine learning" in index


# --- compiling into a domain ------------------------------------------------------------


def test_compile_into_a_domain_writes_only_under_its_prefix(store, vectors, embedder, llm,
                                                           settings):
    upsert_domain(store, "ml", "Machine learning")
    doc = make_extracted_doc()
    result = Compiler(store, vectors, embedder, llm, settings).compile_source(doc, domain="ml")

    assert result.domain == "ml" and result.created
    written = [k for k in store.list(WIKI_PREFIX)
               if not k.startswith("wiki/_meta/")]  # the registry and ledger are not pages
    assert written and all(k.startswith(domain_prefix("ml")) for k in written), written
    assert not store.exists(GISTS_KEY) and not store.exists(INDEX_KEY)

    manifest = gists_mod.load_gists(store, "ml")
    assert set(result.created) <= set(manifest)
    page = read_page(store, result.created[0], "concept", "ml")
    assert page is not None and page.front_matter.domain == "ml"
    raw_page = store.get(f"wiki/domains/ml/concepts/{result.created[0]}.md").decode()
    assert "domain: ml" in raw_page.split("---")[1]
    note = store.get(f"wiki/domains/ml/sources/{doc.source_id}.md").decode()
    assert "domain: ml" in note

    index = store.get(index_key("ml")).decode()
    assert "title: Index - ml" in index and "[[index|All domains]]" in index
    base = settings.vectorize_gists_index
    assert f"{base}-ml" in vectors.index_names()
    assert base not in vectors.index_names(), "general's index was never touched"


def test_compile_never_loads_another_domains_manifest(store, vectors, embedder, llm, settings):
    """Load-bearing scale guard (plan §21.10): the compile reads one manifest."""
    upsert_domain(store, "ml")
    upsert_domain(store, "bio")
    gists_mod.save_gists(store, {"g": PageGist(slug="g", title="G", gist="general page")})
    gists_mod.save_gists(store, {"b": PageGist(slug="b", title="B", gist="bio page")}, "bio")
    spy = SpyObjectStore(store)

    compiler = Compiler(spy, vectors, embedder, llm, settings)
    compiler.compile_source(make_extracted_doc(), domain="ml")

    manifests_read = {k for k in spy.get_keys if k.endswith("gists.json")}
    assert manifests_read == {gists_key("ml")}, manifests_read
    assert not any(k.startswith("wiki/domains/bio/") for k in spy.get_keys)
    assert INDEX_KEY not in spy.get_keys and INDEX_KEY not in spy.put_keys
    assert not spy.lists(WIKI_PREFIX)


def test_compile_into_general_reads_at_most_the_registry_extra(store, vectors, embedder, llm,
                                                                settings):
    upsert_domain(store, "ml")
    gists_mod.save_gists(store, {"m": PageGist(slug="m", title="M", gist="ml page")}, "ml")
    spy = SpyObjectStore(store)

    Compiler(spy, vectors, embedder, llm, settings).compile_source(make_extracted_doc())

    extra = {k for k in spy.get_keys if k.startswith("wiki/") and "/concepts/" not in k
             and "/entities/" not in k and not k.startswith("wiki/_meta/cost")}
    assert extra <= {GISTS_KEY, DOMAINS_KEY}, extra
    assert not any(k.startswith("wiki/domains/") for k in spy.get_keys)


def test_save_registry_sorts_domains(store) -> None:
    registry = DomainRegistry(domains={name: Domain(name=name) for name in ("zeta", "alpha")})
    save_registry(store, registry)
    assert list(json.loads(store.get(DOMAINS_KEY))["domains"]) == ["alpha", "zeta"]


# --- lint per domain ---------------------------------------------------------------------


def test_lint_covers_every_domain_and_attributes_findings(store) -> None:
    upsert_domain(store, "ml")
    gists_mod.save_gists(store, {"ghost": PageGist(slug="ghost", title="Ghost", gist="g")}, "ml")
    gists_mod.save_gists(store, {})
    store.put("wiki/domains/rogue/concepts/x.md", b"---\ntitle: X\nslug: x\n---\nbody\n")

    report = lint_wiki(store)

    assert report.domains == [GENERAL, "ml"]
    kinds = {(f.kind, f.domain, f.slug) for f in report.findings}
    assert ("stale_source", "ml", "ghost") in kinds
    assert ("unknown_domain", "rogue", "rogue") in kinds
    assert not any(f.domain == GENERAL and f.kind != "unknown_domain" for f in report.findings)


def test_lint_one_domain_repairs_only_that_domain(store) -> None:
    upsert_domain(store, "ml")
    gists_mod.save_gists(store, {"ghost": PageGist(slug="ghost", title="Ghost", gist="g")}, "ml")
    gists_mod.save_gists(store, {"phantom": PageGist(slug="phantom", title="P", gist="g")})

    report = lint_wiki(store, dry_run=False, domain="ml")

    assert report.domains == ["ml"]
    assert "ghost" not in gists_mod.load_gists(store, "ml")
    assert "phantom" in gists_mod.load_gists(store), "general was not linted, so not repaired"
    assert any(index_key("ml") in line for line in report.repaired)
    with pytest.raises(UnknownDomain):
        lint_wiki(store, domain="nope")
