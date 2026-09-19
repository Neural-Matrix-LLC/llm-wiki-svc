#!/usr/bin/env python3
"""End-to-end flow check: ingest a source, compile it, search it, answer from it.

Deliberately not a pytest test - it is slow, ordered, stateful and prints a
narrative. Forcing it into pytest would either break the "unit tests make no API
calls" rule or hide the trace.

    scripts/smoke_flow.py --offline          # no network, no keys, runs in the pre-commit gate
    scripts/smoke_flow.py                    # real backends from .env
    scripts/smoke_flow.py --url https://...  # a specific source

Exit code is non-zero at the first failure, and the failing step is named.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "sample.pdf"
TIMEOUT_S = 180


def step(n: int, label: str) -> None:
    print(f"\n[{n}] {label}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="ingest this URL instead of the fixture PDF")
    ap.add_argument("--offline", action="store_true",
                    help="force local/memory/fake backends - no network, no keys")
    ap.add_argument("--question", default="What is retrieval-augmented generation?")
    ap.add_argument("--keep", action="store_true", help="do not clean up the ingested source")
    args = ap.parse_args()

    if args.offline:
        os.environ.update(RERANKER_BACKEND="fake", STORAGE_BACKEND="local", VECTOR_BACKEND="memory",
                          EMBEDDING_BACKEND="fake", LLM_PROVIDER="fake")
        os.environ.setdefault("LOCAL_STORAGE_PATH", str(REPO / ".data"))
        # LLM_PROVIDER=fake alone is not enough: factory.py checks
        # config/providers.py + config/ops.py *before* LLM_PROVIDER at all
        # (plan §19.2) - a real routing config set up for actual use (as this
        # repo may have) would otherwise make real network calls here despite
        # --offline. Force both to a path that cannot exist.
        os.environ["LLMWIKI_PROVIDERS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/providers.py"
        os.environ["LLMWIKI_OPS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/ops.py"

    # Imported after the env is set so factory.py sees the right backends.
    from llmwiki import __version__, tools
    from llmwiki.config import load_settings

    os.environ.setdefault("COST_WRITER", "smoke")  # Phase 2, plan §21.2 C1
    settings = load_settings()
    print(f"llmwiki {__version__} | storage={settings.storage_backend} "
          f"vector={settings.vector_backend} embed={settings.embedding_backend} "
          f"llm={settings.llm_provider}")

    step(1, "ingest")
    if args.url:
        ref = tools.ingest_source(url=args.url, cfg=settings)
    else:
        ref = tools.ingest_source(file=FIXTURE.read_bytes(), filename=FIXTURE.name,
                                  mime="application/pdf", cfg=settings)
    print(f"    source_id={ref.source_id} status={ref.status} duplicate={ref.duplicate}")

    step(2, f"run the pipeline (timeout {TIMEOUT_S}s)")
    started = time.monotonic()
    status = tools.process_source(ref.source_id, cfg=settings)
    while status.state not in ("done", "failed") and time.monotonic() - started < TIMEOUT_S:
        time.sleep(2)
        status = tools.get_source_status(ref.source_id, cfg=settings)
        print(f"    {status.state}...", flush=True)
    if status.state != "done":
        print(f"    FAILED state={status.state} error={status.error}")
        return 1
    print(f"    chunks={status.chunk_count} pages_touched={status.pages_touched} "
          f"elapsed={status.elapsed_s:.1f}s")

    step(3, "wiki pages are well formed")
    concepts = tools.list_concepts(cfg=settings)
    assert concepts, "the compiler produced no pages"
    page = tools.get_page(concepts[0].slug, cfg=settings)
    assert page.front_matter.gist, "page has no gist - the hierarchical index would be useless"
    print(f"    {len(concepts)} pages; sampled '{page.front_matter.slug}' "
          f"v{page.front_matter.version}")

    step(4, "search_wiki")
    hits = tools.search_wiki(args.question, k=3, cfg=settings)
    assert hits, "search returned nothing"
    for hit in hits:
        print(f"    {hit.score:.3f}  {hit.slug or hit.source_id}  (via {hit.origin})")

    step(5, "answer with citations")
    answer = tools.answer(args.question, cfg=settings)
    assert answer.citations, "answer carries no citations - the core contract is broken"
    for citation in answer.citations:
        assert tools.source_exists(citation.source_id, cfg=settings), \
            f"dangling citation: {citation.source_id}"
    print(f"    {answer.text[:200].strip()}...")
    print(f"    citations: {[c.source_id for c in answer.citations]}")

    step(6, "cost ledger")
    cost = tools.cost_summary(cfg=settings)
    print(f"    ${cost.total_usd:.4f} over {cost.call_count} LLM calls; "
          f"cache reads {cost.cache_read_tokens} tok")
    if not args.offline and cost.total_usd == 0:
        print("    WARNING: real backends but zero recorded cost - the ledger is not wired up")

    step(7, "lint")
    report = tools.lint_wiki(dry_run=True, cfg=settings)
    print(f"    {report.page_count} pages, {len(report.findings)} findings "
          f"(domains: {', '.join(report.domains)})")
    for finding in report.findings[:5]:
        print(f"      {finding.kind}: {finding.slug} - {finding.detail}")

    # --- Phase 2 (design §4.10): a second domain, scoped and fanned-out queries,
    # hybrid retrieval, usage, and - offline only - an image described by the
    # fake vision model. Only in --offline mode, so a live run never registers
    # a throwaway domain in the real registry.
    extra_ids: list[str] = []
    if args.offline:
        step(8, "phase 2: a second domain")
        domain = "smoke-domain"
        tools.upsert_domain(domain, "Smoke-test domain (offline only)", cfg=settings)
        second = tools.ingest_now(
            text="# Paged attention\n\nPagedAttention keeps KV-cache blocks XK-7781 resident "
                 "on the GPU so decoding never copies them.\n",
            title="Paged attention notes", domain=domain, cfg=settings,
        )
        assert second.state == "done" and second.domain == domain, second
        extra_ids.append(second.source_id)
        names = [d.name for d in tools.list_domains(cfg=settings)]
        assert names == ["general", domain], names
        assert tools.list_concepts(domain=domain, cfg=settings), "the domain has no pages"
        assert not any(g.slug == "paged-attention" for g in tools.list_concepts(cfg=settings)), (
            "general's manifest must not see the domain's pages")
        print(f"    domains={names}; {second.pages_touched} page(s) compiled into {domain}")

        step(9, "phase 2: scoped and fanned-out retrieval (hybrid + fake reranker)")
        scoped = tools.search_wiki("XK-7781 paged attention", domain=domain, cfg=settings)
        assert scoped and all(h.domain == domain for h in scoped), scoped
        everywhere = tools.answer("what keeps KV-cache blocks on the GPU?", cfg=settings)
        assert everywhere.domains == ["general", domain], everywhere.domains
        assert everywhere.citations, "the fanned-out answer carries no citations"
        lexical_hits = sum(h.lexical_score is not None for h in scoped)
        print(f"    scoped hits={len(scoped)} (lexical={lexical_hits}); "
              f"answer searched {everywhere.domains}, cost ${everywhere.cost_usd:.4f}")

        step(10, "phase 2: usage, worker, synthesis")
        usage = tools.usage_summary(cfg=settings)
        assert {"compile", "query"} <= set(usage.by_kind), usage.by_kind
        assert domain in usage.by_domain, usage.by_domain
        worker = tools.worker_status(cfg=settings)
        assert not worker.paused
        synthesized = tools.synthesize(domain, cfg=settings)[0]
        assert synthesized.written, synthesized
        print(f"    usage kinds={sorted(usage.by_kind)} domains={sorted(usage.by_domain)}; "
              f"worker mode={worker.mode}; overview written ({synthesized.pages_read} pages read)")

        step(11, "phase 2: an image described by the (fake) vision model")
        import pymupdf

        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 48), False)
        pixmap.clear_with(120)
        vision_cfg = settings.model_copy(update={"vision_mode": "auto"})
        image = tools.ingest_now(file=bytes(pixmap.tobytes("png")), filename="board.png",
                                 mime="image/png", title="Whiteboard", cfg=vision_cfg)
        assert image.state == "done" and image.vision_calls == 1, image
        extra_ids.append(image.source_id)
        print(f"    image source {image.source_id}: vision_calls={image.vision_calls}")

    if not args.keep:
        tools.delete_source(ref.source_id, cfg=settings)
        for source_id in extra_ids:
            tools.delete_source(source_id, cfg=settings)
        print("\n    cleaned up (pass --keep to retain)")

    print("\nSMOKE PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\nSMOKE FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
