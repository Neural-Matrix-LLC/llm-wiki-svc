#!/usr/bin/env python3
"""Bulk re-compile every captured source.

Use after a compiler or prompt change, when the wiki should be rebuilt from the
sources already in ``raw/``. Interactive ingest is not the right path for this:
it is not latency-sensitive, so it belongs on the cheapest route available.

    scripts/backfill.py --dry-run
    scripts/backfill.py --limit 20

Note: this currently re-compiles serially through the normal path. The 50%
Batch API discount applies to asynchronous bulk work and is the obvious next
step here; it is not wired up yet, so a large backfill costs full price.
"""

from __future__ import annotations

import argparse
import os
import sys

from llmwiki import factory, tools
from llmwiki.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list what would be recompiled")
    parser.add_argument("--limit", type=int, help="stop after this many sources")
    parser.add_argument("--force", action="store_true",
                        help="recompile even sources already present in the wiki")
    parser.add_argument("--domain",
                        help="only sources routed to this domain (raw/{id}/routing.json; "
                             "sources captured before Phase 2 are 'general')")
    args = parser.parse_args()

    # Own ledger keys, so a long backfill never races the API's appends
    # (Phase 2, plan §21.2 C1).
    os.environ.setdefault("COST_WRITER", "backfill")
    settings = load_settings()
    factory.reset()
    store = factory.object_store(settings)

    source_ids = sorted({key.split("/")[1] for key in store.list("raw/") if key.count("/") >= 2})
    if args.domain:
        from llmwiki.pipeline.ingest import IngestPipeline

        pipeline = IngestPipeline(store, factory.vector_store(settings), factory.embedder(settings),
                                  factory.llm_client(settings), settings)
        source_ids = [sid for sid in source_ids if pipeline.load_routing(sid).domain == args.domain]
    if args.limit:
        source_ids = source_ids[: args.limit]
    print(f"{len(source_ids)} captured sources")

    if args.dry_run:
        for source_id in source_ids:
            print(f"  {source_id}")
        return 0

    total_cost = 0.0
    failures = 0
    for index, source_id in enumerate(source_ids, start=1):
        try:
            result = tools.compile_update(source_id, force=args.force, cfg=settings)
            total_cost += result.cost_usd
            print(f"[{index}/{len(source_ids)}] {source_id} [{result.domain}]: "
                  f"+{len(result.created)} ~{len(result.patched)} ${result.cost_usd:.4f}")
        except Exception as exc:
            failures += 1
            print(f"[{index}/{len(source_ids)}] {source_id}: FAILED {type(exc).__name__}: {exc}",
                  file=sys.stderr)

    print(f"\ntotal ${total_cost:.4f}, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
