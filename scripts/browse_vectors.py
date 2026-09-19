#!/usr/bin/env python3
"""Browse the actual content stored in a Cloudflare Vectorize index.

``check_cloudflare_setup.py`` only proves the *plumbing* works (upsert one
throwaway probe vector, query it, delete it). This script reads what's
*really* in the ``llmwiki-chunks`` / ``llmwiki-gists`` indexes - the chunk
text and gist metadata that ride along on every vector (plan 5.3).

Vectorize has no "list everything" endpoint - only two ways in:

  1. query   - nearest-neighbor search, optionally filtered by metadata
               (source_id/slug/type - the same metadata indexes bootstrap.py
               creates). This is what --source-id, --query and the no-filter
               fallback use.
  2. get_by_ids - fetch specific vector ids directly, no distance involved.
               This is what --id uses.

A no-filter, no-query run sends the zero vector as the probe and returns
whatever Vectorize hands back for it - useful as a quick "is there anything
in here" look, but it is NOT a guaranteed enumeration of the whole index once
it holds more than --limit (Vectorize caps topK at 100 per call anyway).
Filter by --source-id for a complete, exact list of one source's chunks.

Examples:
  python scripts/browse_vectors.py --index chunks --source-id a0998b8b345992ba
  python scripts/browse_vectors.py --index gists  --query "attention mechanism"
  python scripts/browse_vectors.py --index chunks --id a0998b8b345992ba:0 a0998b8b345992ba:1
  python scripts/browse_vectors.py --index gists  --limit 50
"""

from __future__ import annotations

import argparse
import sys
import textwrap

import httpx

from llmwiki.config import Settings, load_settings

TIMEOUT_S = 30.0


def _index_name(settings: Settings, which: str) -> str:
    return settings.vectorize_chunks_index if which == "chunks" else settings.vectorize_gists_index


def fetch_by_ids(settings: Settings, index: str, ids: list[str]) -> list[dict]:
    """Direct REST call - VectorizeStore has no get_by_ids wrapper (it only
    ever needs delete_by_ids in production code), so this stays local."""
    with httpx.Client(
        base_url=f"https://api.cloudflare.com/client/v4/accounts/{settings.cf_account_id}/vectorize/v2",
        headers={"Authorization": f"Bearer {settings.cf_api_token.get_secret_value()}"},
        timeout=TIMEOUT_S,
    ) as client:
        response = client.post(f"/indexes/{index}/get_by_ids", json={"ids": ids})
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", True):
            raise RuntimeError(f"Vectorize error: {payload.get('errors')}")
        return payload.get("result") or []


def embed_query(settings: Settings, text: str) -> list[float]:
    from llmwiki.embedding.workers_ai import WorkersAIEmbedder

    embedder = WorkersAIEmbedder(
        account_id=settings.cf_account_id,
        api_token=settings.cf_api_token.get_secret_value(),
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )
    try:
        return embedder.embed([text])[0]
    finally:
        embedder.close()


def print_hit(vector_id: str, score: float | None, metadata: dict) -> None:
    header = vector_id
    if score is not None:
        header += f"  score={score:.4f}"
    print(header)
    for key in ("source_id", "slug", "type", "title", "section", "url"):
        if metadata.get(key):
            print(f"    {key}: {metadata[key]}")
    text = metadata.get("text") or metadata.get("gist") or ""
    if text:
        wrapped = textwrap.fill(
            text[:2000], width=100, initial_indent="    ", subsequent_indent="    "
        )
        print(wrapped)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--index", choices=["chunks", "gists"], default="chunks")
    parser.add_argument(
        "--source-id", help="exact list of every chunk vector for this source (chunks index)"
    )
    parser.add_argument("--query", help="semantic search: embed this text, show nearest matches")
    parser.add_argument("--id", nargs="+", help="fetch these specific vector ids directly")
    parser.add_argument(
        "--limit", type=int, default=20, help="max results (default 20; Vectorize caps topK at 100)"
    )
    args = parser.parse_args()

    settings = load_settings()
    try:
        settings.require(
            "cf_account_id", "cf_api_token", "vectorize_chunks_index", "vectorize_gists_index"
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    index = _index_name(settings, args.index)

    if args.id:
        rows = fetch_by_ids(settings, index, args.id)
        if not rows:
            print("no vectors found for those ids")
            return 1
        for row in rows:
            print_hit(row["id"], None, row.get("metadata") or {})
        return 0

    from llmwiki.vector.vectorize import VectorizeStore

    store = VectorizeStore(
        settings.cf_account_id, settings.cf_api_token.get_secret_value(),
        probe_dim=settings.embedding_dim,
    )
    try:
        k = min(args.limit, 100)
        if args.query:
            vector = embed_query(settings, args.query)
            hits = store.query(index, vector, k=k)
        else:
            where = {"source_id": args.source_id} if args.source_id else None
            probe = [0.0] * settings.embedding_dim
            hits = store.query(index, probe, k=k, where=where)
    finally:
        store.close()

    if not hits:
        suffix = f" source_id={args.source_id!r}" if args.source_id else ""
        print(f"no vectors matched in {index!r}{suffix}")
        return 1

    print(f"{len(hits)} vector(s) in {index!r}:\n")
    for hit in hits:
        print_hit(hit.id, hit.score, hit.metadata)
    return 0


if __name__ == "__main__":
    sys.exit(main())
