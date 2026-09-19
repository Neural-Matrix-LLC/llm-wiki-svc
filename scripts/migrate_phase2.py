#!/usr/bin/env python3
"""Bring an existing deployment up to Phase 2 - nothing moves, three things get built.

Phase 2 (design §4.10, plan §21.4) deliberately keeps ``general`` as the
Phase 0/1 layout, so an upgraded box needs no data migration. What it does
need, once:

  1. the keyword index built from what raw/ already holds
     (``LEXICAL_BACKEND=sqlite`` starts empty; queries are dense-only until then);
  2. the legacy single-file cost ledger moved into partitioned keys
     (read-through works without this; the move is tidier and bounded);
  3. the vector indexes of any registered domain created (``domains add``
     does this itself; this covers a registry file restored by hand).

    scripts/migrate_phase2.py --check     # report what would be done; change nothing
    scripts/migrate_phase2.py --apply     # do it
    scripts/migrate_phase2.py --apply --skip-lexical   # when the rebuild runs elsewhere

Exit 1 in --check mode when something is still to do, so it can gate a deploy.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_WRITER = "migrate"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report only; change nothing")
    group.add_argument("--apply", action="store_true", help="build what is missing")
    parser.add_argument("--skip-lexical", action="store_true", help="leave the keyword index alone")
    parser.add_argument("--offline", action="store_true", help="fakes + local storage")
    args = parser.parse_args(argv)

    if args.offline:
        os.environ.update(RERANKER_BACKEND="fake", STORAGE_BACKEND="local",
                          VECTOR_BACKEND="memory", EMBEDDING_BACKEND="fake",
                          LLM_PROVIDER="fake", LANGSMITH_TRACING="false")
        os.environ["LLMWIKI_PROVIDERS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/providers.py"
        os.environ["LLMWIKI_OPS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/ops.py"
    os.environ.setdefault("COST_WRITER", REPO_WRITER)

    from llmwiki import factory, tools
    from llmwiki.config import load_settings
    from llmwiki.storage.layout import COST_KEY, domain_index_name
    from llmwiki.wiki.domains import load_registry

    cfg = load_settings()
    factory.reset()
    store = factory.object_store(cfg)
    todo = 0

    print(f"llmwiki | storage={cfg.storage_backend} vector={cfg.vector_backend} "
          f"lexical={cfg.lexical_backend} ({cfg.lexical_root})")

    # 1. lexical index
    print("\n[1] keyword index")
    if cfg.lexical_backend == "none":
        print("    LEXICAL_BACKEND=none: nothing to build (dense-only retrieval)")
    elif args.skip_lexical:
        print("    skipped (--skip-lexical)")
    else:
        lexical = factory.lexical_index(cfg)
        registry = load_registry(store)
        missing = [
            domain_index_name(base, name)
            for name in registry.names()
            for base in (cfg.vectorize_chunks_index, cfg.vectorize_gists_index)
            if lexical.count(domain_index_name(base, name)) == 0
        ]
        if not missing:
            print("    every index has documents")
        elif args.check:
            todo += 1
            print(f"    to build: {', '.join(missing)}")
        else:
            counts = tools.rebuild_lexical(cfg=cfg)
            for index, count in sorted(counts.items()):
                print(f"    {index:48s} {count:6d} documents")

    # 2. legacy ledger
    print("\n[2] cost ledger")
    if not store.exists(COST_KEY):
        print("    no legacy wiki/_meta/cost.jsonl - already partitioned")
    elif args.check:
        todo += 1
        print("    legacy wiki/_meta/cost.jsonl present - will be moved into wiki/_meta/cost/")
    else:
        moved = tools.migrate_cost_ledger(cfg=cfg)
        print(f"    moved {moved} line(s) into partitioned keys")

    # 3. domain indexes
    print("\n[3] domain vector indexes")
    registry = load_registry(store)
    names = [name for name in registry.names() if name != "general"]
    if not names:
        print("    no domains registered (general only)")
    else:
        vectors = factory.vector_store(cfg)
        for name in names:
            for base in (cfg.vectorize_chunks_index, cfg.vectorize_gists_index):
                index = domain_index_name(base, name)
                if args.check:
                    describe = getattr(vectors, "describe_index", None)
                    present = describe(index) is not None if describe else True
                    if not present:
                        todo += 1
                    print(f"    {index:48s} {'ok' if present else 'MISSING'}")
                else:
                    created = vectors.ensure_index(index)
                    print(f"    {index:48s} {'created' if created else 'ok'}")

    if args.check:
        print(f"\n{todo} step(s) to do" if todo else "\nnothing to do")
        return 1 if todo else 0
    print("\nMIGRATION DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
