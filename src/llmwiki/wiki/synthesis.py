"""Scheduled per-domain synthesis: the overview page (Phase 2, design §4.10.1, plan §21.2 A8).

The concrete form of design §4.4's "scheduled global synthesis job" - and the
one place Phase 2 spends a strong model on *reading across pages*. It is
bounded the way the compiler is: it reads the domain's manifest and at most
``SYNTHESIS_MAX_PAGES`` page bodies (the most-sourced concepts and entities),
makes exactly one ``synthesize_domain`` call, and writes one page,
``overview.md``, whose gist joins the manifest and the gist index so the
query agent can find it. It never runs on the ingest path; ``llmwiki
synthesize`` and the compose ``synthesize`` service run it on a schedule.
"""

from __future__ import annotations

import logging
from datetime import date

from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.config import Settings
from llmwiki.embedding.base import Embedder
from llmwiki.lexical.base import LexicalIndex
from llmwiki.llm.base import LLMClient
from llmwiki.models.page import PageFrontMatter, PageGist, WikiPage
from llmwiki.models.plan import CostRecord, SynthesisResult
from llmwiki.storage.base import ObjectStore
from llmwiki.storage.layout import GENERAL, domain_index_name
from llmwiki.vector.base import VectorStore
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.domains import load_registry
from llmwiki.wiki.ledger import CostLedger
from llmwiki.wiki.pages import read_page, write_page

logger = logging.getLogger(__name__)

OVERVIEW_SLUG = "overview"
#: How much of each page body the synthesizer sees.
MAX_BODY_CHARS = 6_000

SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "gist": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["gist", "body"],
}


def rank_pages(manifest: dict[str, PageGist]) -> list[PageGist]:
    """Concept and entity pages, most-sourced first, then most recently updated."""
    rows = [g for g in manifest.values() if g.type in ("concept", "entity")]
    rows.sort(key=lambda g: (-len(g.sources), -(g.updated or date.min).toordinal(), g.slug))
    return rows


def synthesize_domain(
    store: ObjectStore,
    vectors: VectorStore,
    embedder: Embedder,
    llm: LLMClient,
    settings: Settings,
    domain: str = GENERAL,
    *,
    lexical: LexicalIndex | None = None,
    max_pages: int | None = None,
) -> SynthesisResult:
    """Write or refresh ``domain``'s overview page. One LLM call, bounded page reads."""
    cap = settings.synthesis_max_pages if max_pages is None else max_pages
    result = SynthesisResult(domain=domain, slug=OVERVIEW_SLUG)
    manifest = gists_mod.load_gists(store, domain)
    ranked = rank_pages(manifest)
    if not ranked:
        result.reason = "domain has no concept or entity pages yet"
        logger.info("synthesize: domain=%s skipped (%s)", domain, result.reason)
        return result

    listing = "\n".join(f"- {g.slug} ({g.type}, {len(g.sources)} sources): {g.gist}"
                        for g in ranked)
    bodies: list[str] = []
    for gist in ranked[:cap]:
        page = read_page(store, gist.slug, gist.type, domain)
        if page is None:
            continue
        result.pages_read += 1
        bodies.append(f"## [[{gist.slug}]] - {page.front_matter.title}\n\n"
                      f"{page.body[:MAX_BODY_CHARS]}")

    registry = load_registry(store)
    description = (registry.domains[domain].description
                   if domain in registry.domains else "the general knowledge base")
    prompt = (
        f"# Domain\n\n{domain}: {description}\n\n"
        f"# Every page in the domain (gists)\n\n{listing}\n\n"
        f"# The {len(bodies)} most-sourced pages, in full\n\n" + "\n\n".join(bodies) + "\n"
    )
    response = llm.complete(
        op="synthesize_domain",
        system=load_prompt("synthesize_domain"),
        prompt=prompt,
        schema=SYNTHESIS_SCHEMA,
    )
    costs: list[CostRecord] = [response.usage] if response.usage is not None else []
    result.cost_usd = sum(record.cost_usd for record in costs)
    data = response.data or {}
    body = str(data.get("body") or "").strip()
    if not body:
        result.reason = "model returned no body; overview left as it was"
        logger.warning("synthesize: domain=%s %s", domain, result.reason)
        _ledger(store, settings, costs, domain)
        return result

    current = read_page(store, OVERVIEW_SLUG, "overview", domain)
    title = str(data.get("title") or (f"{domain} - overview" if domain != GENERAL else "Overview"))
    page = WikiPage(
        front_matter=PageFrontMatter(
            title=title,
            slug=OVERVIEW_SLUG,
            type="overview",
            gist=str(data.get("gist") or "")[:200],
            sources=sorted({sid for g in ranked for sid in g.sources}),
            updated=date.today(),
            version=current.front_matter.version if current else 0,
            domain=domain,
        ),
        body=body,
    )
    write_page(store, page, expected_version=page.front_matter.version)

    gist = PageGist(slug=OVERVIEW_SLUG, title=title, type="overview",
                    gist=page.front_matter.gist, sources=page.front_matter.sources,
                    updated=date.today(), version=page.front_matter.version)
    gists_mod.upsert_gist(manifest, gist)
    gists_mod.save_gists(store, manifest, domain)
    gists_mod.write_index(store, manifest, domain, registry if domain == GENERAL else None)

    text = f"{gist.title}. {gist.gist}"
    index = domain_index_name(settings.vectorize_gists_index, domain)
    metadata = gists_mod.gist_vector_metadata(gist)
    vectors.upsert(index, [gist.slug], [embedder.embed([text])[0]], [metadata])
    if lexical is not None:
        lexical.upsert(index, [gist.slug], [text], [metadata])

    _ledger(store, settings, costs, domain)
    result.written = True
    logger.info("synthesize: domain=%s wrote %s (%d pages read, $%.4f)",
                domain, OVERVIEW_SLUG, result.pages_read, result.cost_usd)
    return result


def _ledger(store: ObjectStore, settings: Settings, costs: list[CostRecord], domain: str) -> None:
    if costs:
        CostLedger(store, writer=settings.cost_writer).append(costs, kind="synthesis",
                                                              domain=domain)
