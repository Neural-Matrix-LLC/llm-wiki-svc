#!/usr/bin/env python3
"""Show what the domain router would decide, per source, without changing anything.

Phase 2 (design §4.10.1, plan §21.6.3). ``DOMAIN_ROUTE_MIN_CONFIDENCE`` is a
judgement call that depends on the model and the registry; this prints the
raw decision - picked domain, confidence, suggested domain, reason - for every
captured source (or a few named ones) so the threshold can be set from
evidence. Nothing is written: no ``routing.json``, no recompile, and the
ledger gets the ``route_domain`` calls under the ``probe`` writer.

    scripts/probe_domain_routing.py                      # every captured source, the .env registry
    scripts/probe_domain_routing.py --source <id> ...    # just these
    scripts/probe_domain_routing.py --min-confidence 0.5 # try a threshold
    scripts/probe_domain_routing.py --offline            # fakes, fixture docs, two domains
    scripts/probe_domain_routing.py --json out.jsonl     # one record per source

Exit 1 when the registry is general-only (there is nothing to route) or a
source could not be extracted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
OFFLINE_DOCS = [("sample.pdf", "application/pdf"), ("sample.html", "text/html")]
OFFLINE_DOMAINS = {
    "retrieval": "Retrieval-augmented generation, chunking, vector search",
    "web-standards": "HTML, HTTP and browser specifications",
}


def _offline_env() -> None:
    os.environ.update(RERANKER_BACKEND="fake", STORAGE_BACKEND="local", VECTOR_BACKEND="memory",
                      EMBEDDING_BACKEND="fake", LLM_PROVIDER="fake", LANGSMITH_TRACING="false")
    os.environ.setdefault("LOCAL_STORAGE_PATH", str(REPO / ".data-probe-routing"))
    os.environ["LLMWIKI_PROVIDERS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/providers.py"
    os.environ["LLMWIKI_OPS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/ops.py"


def decide(assignment: Any, min_confidence: float) -> str:
    """One word for what the pipeline would do with this decision."""
    if assignment.domain == "general":
        return "general+suggest" if assignment.suggested_domain else "general"
    return "routed" if assignment.confidence >= min_confidence else "demoted"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", action="append", default=[], help="source id (repeatable)")
    parser.add_argument("--min-confidence", type=float,
                        help="threshold to apply (default: DOMAIN_ROUTE_MIN_CONFIDENCE)")
    parser.add_argument("--offline", action="store_true", help="no keys: fakes + fixture docs")
    parser.add_argument("--json", type=Path, help="append one JSON record per source")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    if args.offline:
        _offline_env()
    os.environ.setdefault("COST_WRITER", "probe")

    from llmwiki import factory, tools
    from llmwiki.config import load_settings
    from llmwiki.pipeline.ingest import IngestPipeline
    from llmwiki.wiki.domains import load_registry
    from llmwiki.wiki.router import DomainRouter

    cfg = load_settings()
    factory.reset()
    store = factory.object_store(cfg)
    threshold = args.min_confidence if args.min_confidence is not None else (
        cfg.domain_route_min_confidence
    )

    if args.offline:
        for name, description in OFFLINE_DOMAINS.items():
            tools.upsert_domain(name, description, cfg=cfg)
        for name, mime in OFFLINE_DOCS:
            tools.ingest_source(file=(FIXTURES / name).read_bytes(), filename=name, mime=mime,
                                cfg=cfg)

    registry = load_registry(store)
    if registry.is_general_only:
        print("registry is general-only: nothing to route (llmwiki domains add ...)")
        return 1
    print(f"registry: {', '.join(registry.names())}  threshold={threshold:.2f}")

    pipeline = IngestPipeline(store, factory.vector_store(cfg), factory.embedder(cfg),
                              factory.llm_client(cfg), cfg)
    router = DomainRouter(factory.llm_client(cfg), registry, min_confidence=threshold)

    source_ids = args.source or sorted(
        {key.split("/")[1] for key in store.list("raw/") if key.count("/") >= 2}
    )[: args.limit]
    failures = 0
    for source_id in source_ids:
        try:
            meta = pipeline.load_meta(source_id)
            doc = pipeline.extract(meta)
        except Exception as exc:
            failures += 1
            print(f"  {source_id}: cannot extract ({exc})")
            continue
        assignment, _ = router.route_source(doc)
        verdict = decide(assignment, threshold)
        current = pipeline.load_routing(source_id).domain
        print(f"  {source_id:45s} {assignment.domain:16s} conf={assignment.confidence:.2f} "
              f"{verdict:16s} now={current}"
              + (f"  suggest={assignment.suggested_domain}" if assignment.suggested_domain else "")
              + (f"  ({assignment.reason})" if assignment.reason else ""))
        if args.json:
            with args.json.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"source_id": source_id, "title": doc.title,
                                         "current": current, "verdict": verdict,
                                         **assignment.model_dump(mode="json")}) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
