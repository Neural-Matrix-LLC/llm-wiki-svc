"""Rebuild the lexical index from what object storage already holds (Phase 2, plan §21.2 B2).

The FTS5 files are derived state on the service's local volume: a fresh box,
a wiped volume or a switch from ``LEXICAL_BACKEND=none`` starts with none.
This walks ``raw/`` (the one admin operation that lists it), re-chunks each
source's ``extracted.md`` with the same settings the pipeline used, and
mirrors each domain's gist manifest - so the result equals what incremental
ingest would have written.
"""

from __future__ import annotations

import json
import logging

from llmwiki.config import Settings
from llmwiki.lexical.base import LexicalIndex
from llmwiki.models.source import ExtractedDoc, SourceMeta
from llmwiki.pipeline.chunker import chunk_document
from llmwiki.storage.base import ObjectNotFound, ObjectStore
from llmwiki.storage.layout import (
    GENERAL,
    RAW_PREFIX,
    domain_index_name,
    raw_extracted,
    raw_meta,
    raw_routing,
    source_id_from_key,
)
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.domains import load_registry

logger = logging.getLogger(__name__)


def rebuild(
    store: ObjectStore,
    lexical: LexicalIndex,
    settings: Settings,
    domain: str | None = None,
) -> dict[str, int]:
    """Reset and refill the chunk and gist indexes of ``domain`` (or every domain).

    Returns ``{index_name: document_count}``.
    """
    registry = load_registry(store)
    domains = registry.names() if domain is None else [domain]
    counts: dict[str, int] = {}

    for name in domains:
        for base in (settings.vectorize_chunks_index, settings.vectorize_gists_index):
            lexical.reset(domain_index_name(base, name))

    # Gists: one manifest per domain, non-source pages only (the same rows
    # the compiler mirrors in _sync_gist).
    for name in domains:
        index = domain_index_name(settings.vectorize_gists_index, name)
        manifest = gists_mod.load_gists(store, name)
        rows = [gist for gist in manifest.values() if gist.type != "source"]
        if rows:
            lexical.upsert(
                index,
                [gist.slug for gist in rows],
                [f"{gist.title}. {gist.gist}" for gist in rows],
                [gists_mod.gist_vector_metadata(gist) for gist in rows],
            )
        counts[index] = lexical.count(index)

    # Chunks: every captured source whose routed domain is in scope.
    source_ids = sorted({sid for key in store.list(RAW_PREFIX)
                         if (sid := source_id_from_key(key)) is not None})
    for source_id in source_ids:
        try:
            routing = json.loads(store.get(raw_routing(source_id)))
            routed = str(routing.get("domain") or GENERAL)
        except ObjectNotFound:
            routed = GENERAL
        if routed not in domains:
            continue
        try:
            meta = SourceMeta.model_validate_json(store.get(raw_meta(source_id)))
            text = store.get(raw_extracted(source_id)).decode("utf-8")
        except ObjectNotFound:
            logger.warning("lexical rebuild: %s has no extracted text; skipped", source_id)
            continue
        doc = ExtractedDoc(source_id=source_id, title=meta.title, text=text,
                           modality=meta.modality, url=meta.url)
        chunks = chunk_document(doc, size=settings.chunk_size_chars,
                                overlap=settings.chunk_overlap_chars)
        if not chunks:
            continue
        index = domain_index_name(settings.vectorize_chunks_index, routed)
        lexical.upsert(
            index,
            [chunk.id for chunk in chunks],
            [chunk.text for chunk in chunks],
            [{**json.loads(chunk.metadata.model_dump_json()), "text": chunk.text[:1000]}
             for chunk in chunks],
        )
    for name in domains:
        index = domain_index_name(settings.vectorize_chunks_index, name)
        counts[index] = lexical.count(index)
    logger.info("lexical rebuild: %s", counts)
    return counts
