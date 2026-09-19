"""The gist manifest: ``wiki/_meta/gists.json`` plus the rendered ``wiki/index.md``.

This file is the progressive-disclosure layer from the design doc (4.4).  One
line per page means the compiler can consider the whole wiki without reading any
of it, and ``list_concepts`` costs one object read regardless of wiki size.

Phase 2 (plan §21.2 A1): there is one manifest and one index *per domain* -
``general``'s are the keys above, unchanged; domain ``d``'s live under
``wiki/domains/{d}/``. Nothing here ever loads more than the one manifest it
was asked for; the root index's domain list comes from the registry.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from llmwiki.models.page import DomainRegistry, PageGist
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import GENERAL, gists_key, index_key


def load_gists(store: ObjectStore, domain: str = GENERAL) -> dict[str, PageGist]:
    """Load one domain's manifest. An absent manifest is an empty wiki, not an error."""
    try:
        raw = store.get(gists_key(domain))
    except ObjectNotFound:
        return {}
    data = json.loads(raw.decode("utf-8"))
    return {slug: PageGist(**row) for slug, row in data.items()}


def save_gists(store: ObjectStore, gists: dict[str, PageGist], domain: str = GENERAL) -> None:
    """Persist one domain's manifest, sorted so diffs stay readable."""
    payload = {
        slug: json.loads(gist.model_dump_json())
        for slug, gist in sorted(gists.items())
    }
    store.put(gists_key(domain), json.dumps(payload, indent=2).encode("utf-8"), "application/json")


def upsert_gist(gists: dict[str, PageGist], gist: PageGist) -> dict[str, PageGist]:
    """Insert or replace one row, in place, returning the manifest for chaining."""
    gists[gist.slug] = gist
    return gists


def render_index(
    gists: dict[str, PageGist],
    domain: str = GENERAL,
    registry: DomainRegistry | None = None,
) -> str:
    """Render a domain's ``index.md`` from its manifest.

    Deliberately mechanical - no LLM call.  The index is regenerated on every
    compile, so it must cost nothing. General's index additionally lists the
    registered domains (from ``registry``, never from their manifests); a
    domain's index links back to it. With no registry, or an empty one, the
    output is byte-identical to the Phase 0/1 index.
    """
    by_type: dict[str, list[PageGist]] = defaultdict(list)
    for gist in gists.values():
        by_type[gist.type].append(gist)

    general = domain == GENERAL
    title = "Index" if general else f"Index - {domain}"
    lines = [
        "---",
        f"title: {title}",
        "slug: index",
        "type: index",
        "gist: Hierarchical entry point to the compiled wiki."
        if general else f"gist: Entry point to the {domain} domain of the compiled wiki.",
        "sources: []",
        f"updated: {date.today().isoformat()}",
        "version: 1",
    ]
    if not general:
        lines.append(f"domain: {domain}")
    lines += [
        "---",
        "",
        f"# {title}",
        "",
        f"{len(gists)} pages.",
        "",
    ]
    if not general:
        lines += ["[[index|All domains]]", ""]
    headings = {"concept": "Concepts", "entity": "Entities", "source": "Sources",
                "overview": "Overview"}
    for page_type in ("overview", "concept", "entity", "source"):
        rows = sorted(by_type.get(page_type, []), key=lambda g: g.title.lower())
        if not rows:
            continue
        lines.append(f"## {headings[page_type]}")
        lines.append("")
        for gist in rows:
            lines.append(_index_bullet(gist))
        lines.append("")
    if general and registry is not None:
        from llmwiki.wiki.domains import render_domains_section

        lines += render_domains_section(registry)
    return "\n".join(lines)


def _index_bullet(gist: PageGist) -> str:
    """One index row. Source pages show the title; the slug stays as the link target."""
    summary = f" - {gist.gist}" if gist.gist else ""
    if gist.type != "source":
        return f"- [[{gist.slug}]]{summary}"
    label = _wikilink_label(gist.title or gist.slug)
    return f"- [[{gist.slug}|{label}]] (`{gist.slug}`){summary}"


def _wikilink_label(title: str) -> str:
    """Obsidian aliases cannot contain ``|`` or ``]]``."""
    return title.replace("|", "—").replace("]]", "")


def write_index(
    store: ObjectStore,
    gists: dict[str, PageGist],
    domain: str = GENERAL,
    registry: DomainRegistry | None = None,
) -> None:
    """Regenerate and store one domain's ``index.md``."""
    store.put(index_key(domain), render_index(gists, domain, registry).encode("utf-8"),
              "text/markdown")


def gist_vector_metadata(gist: PageGist) -> dict:
    """Metadata stored alongside a page's gist vector, for the compiler's lookup."""
    return {
        "slug": gist.slug,
        "title": gist.title,
        "type": gist.type,
        "text": gist.gist,
    }
