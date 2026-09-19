"""Domain-aware keys (Phase 2, plan §21.2 A1, §21.4).

The organising claim: ``general`` *is* the Phase 0/1 layout. Every key and
index name for general must equal the pre-Phase-2 constant; every other domain
nests under ``wiki/domains/{d}/`` with the same shape inside.
"""

from __future__ import annotations

import pytest

from llmwiki.storage.layout import (
    GENERAL,
    GISTS_KEY,
    INDEX_KEY,
    check_domain,
    domain_index_name,
    domain_of_key,
    domain_prefix,
    gists_key,
    index_key,
    overview_key,
    pending_key,
    raw_routing,
    raw_vision,
    wiki_page,
    wiki_source_note,
)

SID = "a1b2c3d4e5f60718"


def test_general_is_the_phase1_layout_verbatim() -> None:
    assert domain_prefix(GENERAL) == ""
    assert gists_key(GENERAL) == GISTS_KEY == gists_key()
    assert index_key(GENERAL) == INDEX_KEY == index_key()
    assert wiki_page("rag", "concept", GENERAL) == wiki_page("rag", "concept")
    assert wiki_page("rag", "concept") == "wiki/concepts/rag.md"
    assert wiki_page("acme", "entity", GENERAL) == "wiki/entities/acme.md"
    assert wiki_page(SID, "source", GENERAL) == wiki_source_note(SID) == f"wiki/sources/{SID}.md"
    assert wiki_page("x", "index", GENERAL) == INDEX_KEY
    assert domain_index_name("llmwiki-gists", GENERAL) == "llmwiki-gists"
    assert domain_index_name("llmwiki-gists") == "llmwiki-gists"


def test_other_domains_nest_under_wiki_domains_with_the_same_shape() -> None:
    assert domain_prefix("ml-systems") == "wiki/domains/ml-systems/"
    assert gists_key("ml-systems") == "wiki/domains/ml-systems/_meta/gists.json"
    assert index_key("ml-systems") == "wiki/domains/ml-systems/index.md"
    assert overview_key("ml-systems") == "wiki/domains/ml-systems/overview.md"
    assert wiki_page("rag", "concept", "ml-systems") == "wiki/domains/ml-systems/concepts/rag.md"
    assert wiki_page("acme", "entity", "ml-systems") == "wiki/domains/ml-systems/entities/acme.md"
    assert wiki_source_note(SID, "ml-systems") == f"wiki/domains/ml-systems/sources/{SID}.md"
    assert wiki_page("x", "index", "ml-systems") == index_key("ml-systems")
    assert wiki_page("x", "overview", "ml-systems") == overview_key("ml-systems")
    assert domain_index_name("llmwiki-chunks", "ml-systems") == "llmwiki-chunks-ml-systems"


def test_domain_of_key_attributes_every_wiki_key() -> None:
    assert domain_of_key("wiki/concepts/rag.md") == GENERAL
    assert domain_of_key(GISTS_KEY) == GENERAL
    assert domain_of_key(INDEX_KEY) == GENERAL
    assert domain_of_key("wiki/domains/ml-systems/concepts/rag.md") == "ml-systems"
    assert domain_of_key("wiki/domains/ml-systems/_meta/gists.json") == "ml-systems"
    assert domain_of_key("raw/abc/meta.json") is None
    assert domain_of_key("wiki/domains/") is None


@pytest.mark.parametrize("name", ["ml-systems", "bio", "a1", "x" * 32])
def test_valid_domain_names_pass(name: str) -> None:
    assert check_domain(name) == name


@pytest.mark.parametrize("name", [
    "", "ML", "ml systems", "-ml", "x" * 33, "../x", "wiki/x",
    "concepts", "entities", "sources", "index", "_meta", "domains", "overview", "raw", "status",
])
def test_invalid_or_reserved_domain_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        check_domain(name)


def test_general_is_a_valid_scope_but_cannot_be_registered() -> None:
    assert check_domain(GENERAL) == GENERAL
    with pytest.raises(ValueError):
        check_domain(GENERAL, allow_general=False)


def test_index_name_never_exceeds_vectorize_cap() -> None:
    """32-char domain + the longest shipped base must fit Vectorize's 64-char index name."""
    assert len(domain_index_name("llmwiki-chunks", "x" * 32)) <= 64


def test_derived_raw_objects_and_pending_marker_keys() -> None:
    assert raw_routing(SID) == f"raw/{SID}/routing.json"
    assert raw_vision(SID) == f"raw/{SID}/vision.json"
    assert pending_key(SID) == f"status/_pending/{SID}"
    with pytest.raises(ValueError):
        raw_routing("../escape")
