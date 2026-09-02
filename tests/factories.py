"""Builders for test data. Keeps the arrange step of each test to one line."""

from __future__ import annotations

from datetime import date

from llmwiki.models.page import PageGist
from llmwiki.models.source import ExtractedDoc, SourceMeta
from llmwiki.storage.base import ObjectStore
from llmwiki.wiki import gists as gists_mod


def make_extracted_doc(
    source_id: str = "a1b2c3d4e5f60718",
    title: str = "Retrieval-Augmented Generation",
    text: str | None = None,
) -> ExtractedDoc:
    """An extracted document with enough structure for the chunker to bite on."""
    body = text or (
        "# Retrieval-Augmented Generation\n\n"
        "Retrieval augmented generation grounds language model answers in retrieved "
        "documents rather than parametric memory.\n\n"
        "## Chunking\n\n"
        "Documents are split into chunks before embedding. Chunk size trades recall "
        "against precision.\n\n"
        "## Evaluation\n\n"
        "Retrieval quality is measured separately from generation quality.\n"
    )
    return ExtractedDoc(source_id=source_id, title=title, text=body, modality="web",
                        url="https://example.org/rag")


def make_source_meta(source_id: str = "a1b2c3d4e5f60718") -> SourceMeta:
    return SourceMeta(
        source_id=source_id,
        modality="web",
        title="Retrieval-Augmented Generation",
        url="https://example.org/rag",
        mime="text/html",
        sha256="0" * 64,
        byte_size=1024,
    )


def seed_gists(store: ObjectStore, count: int, embedder=None, vectors=None,
               index: str = "llmwiki-gists") -> dict[str, PageGist]:
    """Populate a wiki of ``count`` pages: manifest, page bodies and gist vectors.

    Used to prove compilation cost does not grow with wiki size, so the pages
    have to exist as real objects, not just manifest rows.
    """
    from llmwiki.models.page import PageFrontMatter, WikiPage
    from llmwiki.storage.layout import wiki_page
    from llmwiki.wiki.pages import render_page

    manifest: dict[str, PageGist] = {}
    for index_n in range(count):
        slug = f"topic-{index_n:04d}"
        gist = PageGist(
            slug=slug,
            title=f"Topic {index_n}",
            type="concept",
            gist=f"Synthetic page {index_n} about topic {index_n} and related retrieval ideas.",
            sources=[],
            updated=date.today(),
            version=1,
        )
        manifest[slug] = gist
        page = WikiPage(
            front_matter=PageFrontMatter(**gist.model_dump()),
            body=f"## Summary\n\nSynthetic body for {slug}.\n",
        )
        store.put(wiki_page(slug, "concept"), render_page(page).encode("utf-8"), "text/markdown")

    gists_mod.save_gists(store, manifest)
    if embedder is not None and vectors is not None:
        slugs = list(manifest)
        embeddings = embedder.embed([f"{manifest[s].title}. {manifest[s].gist}" for s in slugs])
        vectors.upsert(
            index,
            slugs,
            embeddings,
            [gists_mod.gist_vector_metadata(manifest[s]) for s in slugs],
        )
    return manifest
