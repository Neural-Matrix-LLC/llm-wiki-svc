"""One retrieval layer (gists or chunks) across one or more domain scopes.

Phase 2 (design v1.4 §4.10.1-4.10.2, plan §21.6.2). This is the seam every
dense query on the query path goes through - ``QueryAgent.search``, the
graph's ``retrieve`` node and the toolkit's ``search_wiki``/``search_chunks``.
In P2-A3 it is dense-only: one query per scope, hits tagged with their
``domain`` and their raw cosine as ``dense_score``, merged by score across
scopes. A single scope with no lexical index and no reranker is the
pre-Phase-2 call, byte for byte: same index name, same ``k``, same hits, so
the wiki-first tests keep their meaning. The lexical half, reciprocal-rank
fusion and reranking slot in here (P2-B1/B2) without another call site
changing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from llmwiki.config import Settings
from llmwiki.lexical.base import LexicalIndex
from llmwiki.models.chunk import SearchHit
from llmwiki.rerank.base import Reranker
from llmwiki.vector.base import VectorStore
from llmwiki.wiki.domains import DomainScope

logger = logging.getLogger(__name__)

Layer = Literal["gists", "chunks"]


def index_base(settings: Settings, kind: Layer) -> str:
    return settings.vectorize_gists_index if kind == "gists" else settings.vectorize_chunks_index


def _tag(hit: SearchHit, domain: str) -> SearchHit:
    """Stamp the scope and keep the cosine where the confidence gate can find it."""
    hit.domain = domain
    if hit.dense_score is None:
        hit.dense_score = hit.score
    return hit


def gate_score(hit: SearchHit) -> float:
    """What the wiki-confidence gate compares: the dense cosine, or ``score`` if there is none."""
    return hit.score if hit.dense_score is None else hit.dense_score


RRF_K = 60


def rrf_fuse(lists: Sequence[Sequence[SearchHit]], k: int = RRF_K) -> list[SearchHit]:
    """Reciprocal-rank fusion: ``score = Σ 1 / (k + rank)`` over every list a hit appears in.

    Only ranks matter, so a cosine list and a BM25 list fuse without any score
    calibration. The first hit object seen for an id is kept (dense hits are
    listed first, so their metadata wins); ``dense_score`` / ``lexical_score``
    are merged from whichever list carried them.
    """
    fused: dict[str, float] = {}
    keep: dict[str, SearchHit] = {}
    for ranked in lists:
        for position, hit in enumerate(ranked):
            fused[hit.id] = fused.get(hit.id, 0.0) + 1.0 / (k + position + 1)
            if hit.id in keep:
                kept = keep[hit.id]
                if kept.dense_score is None and hit.dense_score is not None:
                    kept.dense_score = hit.dense_score
                if kept.lexical_score is None and hit.lexical_score is not None:
                    kept.lexical_score = hit.lexical_score
            else:
                keep[hit.id] = hit
    ordered = sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))
    result = []
    for hit_id, score in ordered:
        hit = keep[hit_id]
        hit.score = score
        result.append(hit)
    return result


def retrieve_layer(
    kind: Layer,
    query: str,
    vector: list[float],
    scopes: Sequence[DomainScope],
    k: int,
    *,
    vectors: VectorStore,
    settings: Settings,
    lexical: LexicalIndex | None = None,
    reranker: Reranker | None = None,
) -> list[SearchHit]:
    """Top-``k`` hits for the query in ``kind`` across ``scopes``.

    Dense-only with one scope and no reranker is the pre-Phase-2 call, byte
    for byte. With a lexical index each scope contributes a dense list and a
    keyword list of ``HYBRID_POOL_K`` candidates, fused by reciprocal rank;
    with a reranker the fused pool (at most ``RERANK_MAX_CANDIDATES``) is
    reordered by the cross-encoder. ``score`` is then the last stage's score;
    ``dense_score`` keeps the cosine for the confidence gate (plan §21.2 B4).
    """
    base = index_base(settings, kind)
    if not scopes:
        return []
    if lexical is None and reranker is None:
        if len(scopes) == 1:
            scope = scopes[0]
            return [_tag(hit, scope.name)
                    for hit in vectors.query(scope.index_name(base), vector, k=k)]
        merged: list[SearchHit] = []
        for scope in scopes:
            merged.extend(_tag(hit, scope.name)
                          for hit in vectors.query(scope.index_name(base), vector, k=k))
        # Cosine scores from sibling indexes built by the same embedder are
        # directly comparable, so a plain merge is the right fusion here.
        merged.sort(key=lambda hit: hit.score, reverse=True)
        logger.debug("retrieve_layer(%s): %d scopes -> %d hits, top %d kept",
                     kind, len(scopes), len(merged), k)
        return merged[:k]

    pool = max(k, settings.hybrid_pool_k)
    lists: list[list[SearchHit]] = []
    for scope in scopes:
        index = scope.index_name(base)
        lists.append([_tag(hit, scope.name) for hit in vectors.query(index, vector, k=pool)])
    if lexical is not None:
        for scope in scopes:
            index = scope.index_name(base)
            lexical_hits = lexical.query(index, query, k=pool)
            for hit in lexical_hits:
                hit.domain = scope.name
            lists.append(lexical_hits)
    fused = rrf_fuse(lists)
    logger.debug("retrieve_layer(%s): hybrid over %d scopes, pool %d -> %d fused, top %d kept",
                 kind, len(scopes), pool, len(fused), k)
    if reranker is not None and fused:
        fused = rerank_hits(reranker, query, fused[: settings.rerank_max_candidates], k)
    return fused[:k]


def rerank_text(hit: SearchHit) -> str:
    """What the cross-encoder reads: metadata text only - never a page body."""
    if hit.text:
        return hit.text
    meta = hit.metadata or {}
    return " ".join(str(meta.get(key, "")) for key in ("title", "text") if meta.get(key)) or hit.id


def rerank_hits(reranker: Reranker, query: str, candidates: list[SearchHit], k: int,
                ) -> list[SearchHit]:
    """Reorder ``candidates`` by the reranker; on any failure keep the fused order.

    The reranker's score replaces ``score``; ``dense_score``/``lexical_score``
    stay, so the confidence gate still reads the cosine.
    """
    try:
        ranked = reranker.rerank(query, [rerank_text(hit) for hit in candidates], top_n=k)
    except Exception as exc:
        logger.warning("reranker failed (%s: %s); keeping the fused order", type(exc).__name__, exc)
        return candidates
    if not ranked:
        return candidates
    reordered: list[SearchHit] = []
    for position, score in ranked:
        if 0 <= position < len(candidates):
            hit = candidates[position]
            hit.score = float(score)
            reordered.append(hit)
    return reordered or candidates
