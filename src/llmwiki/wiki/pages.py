"""Read, write and parse wiki pages.

Pages are markdown with YAML front matter (plan 5.2) so the wiki is a valid
Obsidian vault with no export step.  Writes go through
:func:`write_page`, which enforces the optimistic version check - two compilers
touching the same page must not silently lose one another's work.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import frontmatter

from llmwiki.models.page import PageFrontMatter, WikiPage
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import wiki_page


class VersionConflict(RuntimeError):
    """Raised when a page changed underneath a patch that was based on an older version."""


def parse_page(raw: str) -> WikiPage:
    """Parse markdown-with-front-matter into a :class:`WikiPage`.

    Front matter is untyped YAML from storage, so validation happens here, once,
    at the boundary - pydantic raises on a malformed page rather than letting a
    bad ``version`` or ``type`` propagate into the compiler.
    """
    post = frontmatter.loads(raw)
    meta: dict[str, Any] = dict(post.metadata)
    meta.setdefault("title", "Untitled")
    meta.setdefault("slug", "untitled")
    return WikiPage(
        front_matter=PageFrontMatter.model_validate(meta), body=post.content.strip() + "\n"
    )


def render_page(page: WikiPage) -> str:
    """Render a page back to markdown. Front-matter key order is stable for clean diffs."""
    fm = page.front_matter
    lines = [
        "---",
        f"title: {fm.title}",
        f"slug: {fm.slug}",
        f"type: {fm.type}",
        f"gist: {fm.gist}",
        f"sources: [{', '.join(fm.sources)}]",
        f"updated: {(fm.updated or date.today()).isoformat()}",
        f"version: {fm.version}",
        "---",
        "",
    ]
    return "\n".join(lines) + page.body.strip() + "\n"


def read_page(store: ObjectStore, slug: str, page_type: str = "concept") -> WikiPage | None:
    """Load one page body. Returns ``None`` if it does not exist.

    Every call to this is a page-body read, which is exactly what
    ``test_compiler_no_full_scan`` counts - keep them deliberate.
    """
    key = wiki_page(slug, page_type)
    try:
        return parse_page(store.get(key).decode("utf-8"))
    except ObjectNotFound:
        return None


def write_page(store: ObjectStore, page: WikiPage, expected_version: int | None = None) -> WikiPage:
    """Write a page, bumping its version.

    ``expected_version`` is the version the caller read.  If the stored page has
    moved on, the write is refused rather than clobbering the other writer.
    """
    key = wiki_page(page.front_matter.slug, page.front_matter.type)
    if expected_version is not None:
        current = read_page(store, page.front_matter.slug, page.front_matter.type)
        stored_version = current.front_matter.version if current else 0
        if stored_version != expected_version:
            raise VersionConflict(
                f"{page.front_matter.slug}: expected version {expected_version}, "
                f"stored version is {stored_version}"
            )
    page.front_matter.version = (expected_version or page.front_matter.version) + 1
    page.front_matter.updated = date.today()
    store.put(key, render_page(page).encode("utf-8"), "text/markdown")
    return page


def append_sources_section(body: str, source_ids: list[str], titles: dict[str, str]) -> str:
    """Rewrite the trailing ``## Sources`` section from the page's source list.

    The compiler owns this section, not the model - a generated source list is a
    citation that can be wrong.
    """
    marker = "\n## Sources\n"
    trimmed = body.split(marker)[0].rstrip()
    lines = [
        f"- [[sources/{source_id}]] - {titles.get(source_id, source_id)}"
        for source_id in source_ids
    ]
    return f"{trimmed}\n{marker}\n" + "\n".join(lines) + "\n"
