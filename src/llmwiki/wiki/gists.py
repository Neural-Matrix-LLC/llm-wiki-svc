"""The gist manifest: ``wiki/_meta/gists.json`` plus the rendered ``wiki/index.md``.

This file is the progressive-disclosure layer from the design doc (4.4).  One
line per page means the compiler can consider the whole wiki without reading any
of it, and ``list_concepts`` costs one object read regardless of wiki size.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from llmwiki.models.page import PageGist
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import GISTS_KEY, INDEX_KEY


def load_gists(store: ObjectStore) -> dict[str, PageGist]:
    """Load the manifest. An absent manifest is an empty wiki, not an error."""
    try:
        raw = store.get(GISTS_KEY)
    except ObjectNotFound:
        return {}
    data = json.loads(raw.decode("utf-8"))
    return {slug: PageGist(**row) for slug, row in data.items()}


def save_gists(store: ObjectStore, gists: dict[str, PageGist]) -> None:
    """Persist the manifest, sorted so diffs stay readable."""
    payload = {
        slug: json.loads(gist.model_dump_json())
        for slug, gist in sorted(gists.items())
    }
    store.put(GISTS_KEY, json.dumps(payload, indent=2).encode("utf-8"), "application/json")


def upsert_gist(gists: dict[str, PageGist], gist: PageGist) -> dict[str, PageGist]:
    """Insert or replace one row, in place, returning the manifest for chaining."""
    gists[gist.slug] = gist
    return gists


def render_index(gists: dict[str, PageGist]) -> str:
    """Render ``wiki/index.md`` from the manifest.

    Deliberately mechanical - no LLM call.  The index is regenerated on every
    compile, so it must cost nothing.
    """
    by_type: dict[str, list[PageGist]] = defaultdict(list)
    for gist in gists.values():
        by_type[gist.type].append(gist)

    lines = [
        "---",
        "title: Index",
        "slug: index",
        "type: index",
        "gist: Hierarchical entry point to the compiled wiki.",
        "sources: []",
        f"updated: {date.today().isoformat()}",
        "version: 1",
        "---",
        "",
        "# Index",
        "",
        f"{len(gists)} pages.",
        "",
    ]
    headings = {"concept": "Concepts", "entity": "Entities", "source": "Sources"}
    for page_type in ("concept", "entity", "source"):
        rows = sorted(by_type.get(page_type, []), key=lambda g: g.title.lower())
        if not rows:
            continue
        lines.append(f"## {headings[page_type]}")
        lines.append("")
        for gist in rows:
            summary = f" - {gist.gist}" if gist.gist else ""
            lines.append(f"- [[{gist.slug}]]{summary}")
        lines.append("")
    return "\n".join(lines)


def write_index(store: ObjectStore, gists: dict[str, PageGist]) -> None:
    """Regenerate and store ``wiki/index.md``."""
    store.put(INDEX_KEY, render_index(gists).encode("utf-8"), "text/markdown")


def gist_vector_metadata(gist: PageGist) -> dict:
    """Metadata stored alongside a page's gist vector, for the compiler's lookup."""
    return {
        "slug": gist.slug,
        "title": gist.title,
        "type": gist.type,
        "text": gist.gist,
    }
