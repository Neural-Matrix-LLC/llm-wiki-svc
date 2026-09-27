#!/usr/bin/env python3
"""Move sources captured before Phase 2 (all in ``general``) into registered domains.

``migrate_phase2.py`` upgrades a deployment without moving anything: every
pre-Phase-2 source simply *is* in ``general``, whose R2 keys and Vectorize
indexes are the Phase 0/1 ones. This script is the optional second step, for
when you have registered domains and want the existing corpus filed under them
too. Two stages, with a reviewable plan file in between:

    # 0. register the domains first (their Vectorize indexes are created here)
    llmwiki domains add ml-systems --description "ML infrastructure, training, serving"

    # 1. plan: one route_domain LLM call per general source (or none with --default)
    scripts/migrate_domains.py plan --out domain-plan.json
    scripts/migrate_domains.py plan --default ml-systems --out domain-plan.json

    # 2. edit domain-plan.json if you like ("to": "general" keeps a source), then
    scripts/migrate_domains.py apply --plan domain-plan.json          # dry run
    scripts/migrate_domains.py apply --plan domain-plan.json --yes    # do it

For each moved source, ``apply`` re-embeds its chunks into ``{chunks}-{d}``,
compiles it into ``wiki/domains/{d}/`` (normal compile cost, one source at a
time), rewrites ``raw/{id}/routing.json`` and deletes its chunks from the
general index. ``raw/`` originals are never touched. Afterwards general's pages
every source of which moved are pruned (``--no-prune`` keeps them); a page that
also cites a source that stayed is kept and listed. Re-running ``apply`` with
the same plan is safe: a source already in its target domain is skipped.

Run it while nothing else is ingesting (stop capture, or run it in a one-off
container: ``docker compose run --rm api python scripts/migrate_domains.py ...``) -
the per-domain lock is per process.

Exit 1 when any source failed to move.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_WRITER = "migrate"


def _offline_env() -> None:
    os.environ.update(RERANKER_BACKEND="fake", STORAGE_BACKEND="local",
                      VECTOR_BACKEND="memory", EMBEDDING_BACKEND="fake",
                      LLM_PROVIDER="fake", LANGSMITH_TRACING="false")
    os.environ["LLMWIKI_PROVIDERS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/providers.py"
    os.environ["LLMWIKI_OPS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/ops.py"


def _general_sources(pipeline, store) -> tuple[list[str], list[str]]:  # type: ignore[no-untyped-def]
    """(sources routed to general by default, sources a caller named general at capture)."""
    from llmwiki.storage.layout import GENERAL, RAW_PREFIX, source_id_from_key

    ids = sorted({sid for key in store.list(RAW_PREFIX)
                  if (sid := source_id_from_key(key)) is not None})
    movable, pinned = [], []
    for source_id in ids:
        routing = pipeline.load_routing(source_id)
        if routing.domain != GENERAL:
            continue
        (pinned if routing.explicit else movable).append(source_id)
    return movable, pinned


def plan(args: argparse.Namespace) -> int:
    from llmwiki import factory, tools
    from llmwiki.config import load_settings
    from llmwiki.storage.layout import GENERAL
    from llmwiki.wiki.domains import load_registry, require_domain

    cfg = load_settings()
    factory.reset()
    store = factory.object_store(cfg)
    registry = load_registry(store)
    if registry.is_general_only:
        print("No domains are registered - nothing to move to. Register them first:\n"
              "  llmwiki domains add <name> --description \"what belongs there\"",
              file=sys.stderr)
        return 2
    if args.default:
        require_domain(registry, args.default)

    pipeline = tools._pipeline(cfg)
    movable, pinned = _general_sources(pipeline, store)
    if args.source:
        wanted = set(args.source)
        movable = [sid for sid in movable if sid in wanted]
    if args.limit:
        movable = movable[: args.limit]
    how = f"--default {args.default} (no LLM call)" if args.default else \
        f"route_domain, one LLM call each (min confidence {cfg.domain_route_min_confidence})"
    print(f"llmwiki | storage={cfg.storage_backend} vector={cfg.vector_backend}")
    print(f"domains: {', '.join(registry.names())}")
    print(f"{len(movable)} general source(s) to plan via {how}; "
          f"{len(pinned)} named 'general' at capture are left alone\n")

    rows = []
    for index, source_id in enumerate(movable, start=1):
        title = pipeline.load_meta(source_id).title
        try:
            if args.default:
                row = {"to": args.default, "confidence": 1.0, "suggested_domain": "",
                       "reason": f"--default {args.default}"}
            else:
                pick = tools.propose_domain(source_id, cfg=cfg)
                row = {"to": pick.domain, "confidence": round(pick.confidence, 3),
                       "suggested_domain": pick.suggested_domain, "reason": pick.reason}
        except Exception as exc:  # one unreadable source must not sink the plan
            row = {"to": GENERAL, "confidence": 0.0, "suggested_domain": "",
                   "reason": f"routing failed: {type(exc).__name__}: {exc}"}
        rows.append({"source_id": source_id, "title": title, "from": GENERAL, **row})
        hint = f" (suggested {row['suggested_domain']})" if row["suggested_domain"] else ""
        print(f"[{index}/{len(movable)}] {source_id}: -> {row['to']}"
              f" {row['confidence']:.2f}{hint}")

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["to"]] = counts.get(row["to"], 0) + 1
    print("\nplanned: " + (", ".join(f"{d}={n}" for d, n in sorted(counts.items())) or "nothing"))

    payload = {"created": datetime.now(UTC).isoformat(timespec="seconds"),
               "storage": cfg.storage_backend, "sources": rows}
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} - review it, then: "
          f"scripts/migrate_domains.py apply --plan {args.out} --yes")
    return 0


def apply(args: argparse.Namespace) -> int:
    from llmwiki import factory, tools
    from llmwiki.config import load_settings
    from llmwiki.wiki.domains import UnknownDomain, load_registry, require_domain

    cfg = load_settings()
    factory.reset()
    store = factory.object_store(cfg)
    registry = load_registry(store)
    pipeline = tools._pipeline(cfg)
    rows = json.loads(Path(args.plan).read_text(encoding="utf-8"))["sources"]

    todo, skipped = [], 0
    for row in rows:
        source_id, target, origin = row["source_id"], row["to"], row.get("from", "general")
        if target == origin:
            continue
        try:
            require_domain(registry, target)
        except UnknownDomain:
            print(f"  {source_id}: SKIP - domain {target!r} is not registered")
            skipped += 1
            continue
        current = pipeline.load_routing(source_id).domain
        if current == target:
            print(f"  {source_id}: already in {target}")
            continue
        if current != origin:
            print(f"  {source_id}: SKIP - now in {current!r}, the plan expected {origin!r}")
            skipped += 1
            continue
        todo.append(row)

    print(f"{len(todo)} source(s) to move, {skipped} skipped")
    for row in todo:
        print(f"  {row['source_id']}: {row.get('from', 'general')} -> {row['to']}")
    if not args.yes:
        print("\nDry run - nothing changed. Re-run with --yes to move them.")
        return 0

    print()
    moved: dict[str, set[str]] = {}
    failures, total_cost = 0, 0.0
    for index, row in enumerate(todo, start=1):
        source_id, target = row["source_id"], row["to"]
        try:
            result = tools.reassign_source(
                source_id, target, confidence=float(row.get("confidence", 1.0)),
                reason=f"migrate_domains: {row.get('reason', '')}".strip(), cfg=cfg,
            )
        except Exception as exc:
            failures += 1
            print(f"[{index}/{len(todo)}] {source_id}: FAILED {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            continue
        total_cost += result.cost_usd
        if result.aborted:
            failures += 1
            print(f"[{index}/{len(todo)}] {source_id}: ABORTED ({result.reason}) - "
                  "left where it was", file=sys.stderr)
            continue
        moved.setdefault(row.get("from", "general"), set()).add(source_id)
        print(f"[{index}/{len(todo)}] {source_id} -> {target}: "
              f"+{len(result.created)} ~{len(result.patched)} ${result.cost_usd:.4f}")

    for origin, sources in sorted(moved.items()):
        pruned = tools.prune_moved_sources(origin, sources, dry_run=args.no_prune, cfg=cfg)
        verb = "would prune (--no-prune)" if args.no_prune else "pruned"
        print(f"\n{origin}: {verb} {len(pruned.removed)} page(s) whose sources all moved")
        for slug in pruned.removed:
            print(f"    - {slug}")
        if pruned.mixed:
            print(f"{origin}: kept {len(pruned.mixed)} page(s) that also cite a source "
                  "that stayed (their citations still resolve):")
            for slug in pruned.mixed:
                print(f"    = {slug}")

    print(f"\nmoved {sum(len(s) for s in moved.values())}, failed {failures}, "
          f"total ${total_cost:.4f}")
    if moved:
        print("next: llmwiki synthesize --all   # refresh each domain's overview page")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="fakes + local storage")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="decide a domain for every general source; write a plan file")
    p.add_argument("--out", default="domain-plan.json", help="plan file to write")
    p.add_argument("--default", metavar="DOMAIN",
                   help="send every general source to DOMAIN, no LLM call")
    p.add_argument("--source", action="append", metavar="ID", help="only this source (repeatable)")
    p.add_argument("--limit", type=int, help="plan at most this many sources")

    a = sub.add_parser("apply", help="move the sources a plan file names")
    a.add_argument("--plan", required=True, help="plan file written by `plan`")
    a.add_argument("--yes", action="store_true", help="actually move; without it, a dry run")
    a.add_argument("--no-prune", action="store_true",
                   help="keep general's pages even when every source they cite moved")

    args = parser.parse_args(argv)
    if args.offline:
        _offline_env()
    os.environ.setdefault("COST_WRITER", REPO_WRITER)
    return plan(args) if args.command == "plan" else apply(args)


if __name__ == "__main__":
    sys.exit(main())
