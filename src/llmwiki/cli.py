"""``llmwiki`` command line: ingest, search, page, concepts, domains, compile, lint, cost, serve.

Transport only - every subcommand calls one function in ``tools.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

OFFLINE_ENV = {
    "STORAGE_BACKEND": "local",
    "VECTOR_BACKEND": "memory",
    "EMBEDDING_BACKEND": "fake",
    "LLM_PROVIDER": "fake",
    # Phase 2: the reranker is a Cloudflare call; the lexical index is local
    # SQLite and stays on (it is what --offline should exercise).
    "RERANKER_BACKEND": "fake",
    # A checkout with config/providers.py + config/ops.py present is in routed
    # mode regardless of LLM_PROVIDER (design §4.8.1): the table wins over the
    # single-provider fallback. Pointing both paths at nothing is what makes
    # --offline actually offline - the same guard scripts/smoke_flow.py uses.
    # (Found 2026-09-19: an --offline ingest on a dev box compiled with the
    # real OpenRouter route.)
    "LLMWIKI_PROVIDERS_CONFIG": "/nonexistent/llmwiki-offline-guard/providers.py",
    "LLMWIKI_OPS_CONFIG": "/nonexistent/llmwiki-offline-guard/ops.py",
    "LANGSMITH_TRACING": "false",
}


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI surface."""
    parser = argparse.ArgumentParser(prog="llmwiki", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--offline",
        action="store_true",
        help="force local/memory/fake backends - no network, no keys",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="capture and compile a source")
    group = ingest.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="blog post, YouTube video, or a direct link to a PDF")
    group.add_argument("--file", type=Path, help="a PDF or a .txt/.md text file")
    group.add_argument("--text", help="text to store verbatim; '-' reads stdin")
    ingest.add_argument("--title", default="")
    ingest.add_argument("--domain", help="file the source under this registered domain")

    search = sub.add_parser("search", help="search the knowledge base")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--domain", help="search one domain only")

    ask = sub.add_parser("ask", help="answer a question with citations")
    ask.add_argument("query")
    ask.add_argument("--domain", help="search one domain only")

    feedback = sub.add_parser(
        "feedback", help="score an answer's LangSmith run and record what was right",
    )
    feedback.add_argument("run_id", help="Answer.run_id from `ask` (needs LANGSMITH_TRACING)")
    feedback.add_argument("--score", type=float, required=True, help="0 (wrong) to 1 (right)")
    feedback.add_argument("--correction", default="",
                          help="what the right answer is; cite source ids so it can be promoted")

    page = sub.add_parser("page", help="print one wiki page")
    page.add_argument("slug")
    page.add_argument("--domain", help="which domain's page (default: general)")

    concepts = sub.add_parser("concepts", help="list page gists")
    concepts.add_argument("--prefix")
    concepts.add_argument("--domain", help="which domain's manifest (default: general)")

    domains = sub.add_parser("domains", help="list or manage the domain registry (Phase 2)")
    domains_sub = domains.add_subparsers(dest="domains_command", required=True)
    domains_sub.add_parser("list", help="every domain, general first")
    add = domains_sub.add_parser("add", help="register a domain (or update its description)")
    add.add_argument("name", help="lowercase letters, digits and hyphens, at most 32 chars")
    add.add_argument("--description", default="", help="one line: what belongs here")
    update = domains_sub.add_parser("update", help="change a domain's description")
    update.add_argument("name")
    update.add_argument("--description", required=True)
    remove = domains_sub.add_parser("remove", help="unregister a domain (pages are kept)")
    remove.add_argument("name")
    remove.add_argument("--force", action="store_true",
                        help="unregister even if the domain still holds pages")

    compile_cmd = sub.add_parser("compile", help="re-run the compiler for one source")
    compile_cmd.add_argument("source_id")
    compile_cmd.add_argument("--force", action="store_true")

    lexical = sub.add_parser("lexical", help="the keyword index (Phase 2 hybrid retrieval)")
    lexical_sub = lexical.add_subparsers(dest="lexical_command", required=True)
    rebuild = lexical_sub.add_parser("rebuild", help="rebuild the FTS index from raw/ + manifests")
    rebuild.add_argument("--domain", help="one domain only (default: every domain)")

    synth = sub.add_parser("synthesize", help="write/refresh domain overview pages (Phase 2)")
    synth_group = synth.add_mutually_exclusive_group(required=True)
    synth_group.add_argument("--domain", help="one domain")
    synth_group.add_argument("--all", action="store_true", help="every domain, general included")

    lint = sub.add_parser("lint", help="run the global wiki lint")
    lint.add_argument("--fix", action="store_true", help="apply safe repairs")
    lint.add_argument("--domain", help="lint one domain only (default: every domain)")

    sub.add_parser("cost", help="summarize the measured cost ledger (everything ever recorded)")
    usage = sub.add_parser("usage", help="spend by domain/kind/op/model/day (Phase 2)")
    usage.add_argument("--month", help="YYYY-MM (default: this month to date)")
    usage.add_argument("--domain", help="one domain only")
    usage.add_argument("--json", action="store_true", help="print the full summary as JSON")
    usage.add_argument("--migrate", action="store_true",
                       help="move the legacy wiki/_meta/cost.jsonl into partitioned keys")
    worker = sub.add_parser("worker", help="ingest worker state (Phase 2)")
    worker.add_argument("--resume", action="store_true",
                        help="retry sources parked under the monthly cost cap now")
    sub.add_parser("status", help="print health and configured backends")

    status = sub.add_parser("source", help="show one source's pipeline status")
    status.add_argument("source_id")

    serve = sub.add_parser("serve", help="run the FastAPI + MCP service")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``llmwiki`` console script."""
    args = build_parser().parse_args(argv)

    if args.offline:
        os.environ.update(OFFLINE_ENV)
    # Phase 2 (plan §21.2 C1): the CLI writes its own daily ledger keys, so a
    # `llmwiki ingest` on the box never races the API's appends.
    os.environ.setdefault("COST_WRITER", "cli")
    # A CLI command waits for its own work; the threaded worker is the API's.
    os.environ.setdefault("WORKER_MODE", "inline")

    # Imported after the environment is settled so the factory sees the right backends.
    from llmwiki import factory, tools
    from llmwiki.config import configure_logging, load_settings

    cfg = load_settings()
    configure_logging(cfg.log_level)
    factory.reset()

    if args.command == "ingest":
        if args.url:
            status = tools.ingest_now(url=args.url, title=args.title, domain=args.domain,
                                      cfg=cfg)
        elif args.text is not None:
            text = sys.stdin.read() if args.text == "-" else args.text
            status = tools.ingest_now(text=text, title=args.title, domain=args.domain, cfg=cfg)
        else:
            data = args.file.read_bytes()
            status = tools.ingest_now(
                file=data,
                filename=args.file.name,
                title=args.title or args.file.stem,
                domain=args.domain,
                cfg=cfg,
            )
        _dump(status.model_dump(mode="json"))
        return 0 if status.state == "done" else 1

    if args.command == "search":
        hits = tools.search_wiki(args.query, k=args.k, domain=args.domain, cfg=cfg)
        for hit in hits:
            where = "" if hit.domain == "general" else f"  [{hit.domain}]"
            print(f"{hit.score:.3f}  {hit.origin:5s}  {hit.slug or hit.source_id}{where}")
        return 0

    if args.command == "ask":
        result = tools.answer(args.query, domain=args.domain, cfg=cfg)
        print(result.text)
        print()
        for citation in result.citations:
            print(f"  [{citation.source_id}] {citation.title}")
        for ref in result.external_refs:
            print(f"  (external) {ref.title or ref.url} <{ref.url}>")
        if result.steps:
            print(f"  tools: {', '.join(f'{s.tool}({s.chars} chars)' for s in result.steps)}")
        if result.run_id:
            print(f"  run_id: {result.run_id}")
        if result.domains and result.domains != ["general"]:
            print(f"  domains: {', '.join(result.domains)}")
        if result.cost_usd:
            print(f"  cost: ${result.cost_usd:.4f}")
        return 0

    if args.command == "feedback":
        try:
            feedback_id = tools.record_feedback(
                args.run_id, args.score, args.correction, cfg=cfg,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"recorded feedback {feedback_id} on run {args.run_id}")
        return 0

    if args.command == "page":
        from llmwiki.wiki.pages import render_page

        try:
            print(render_page(tools.get_page(args.slug, domain=args.domain, cfg=cfg)), end="")
        except KeyError:
            print(f"no page {args.slug!r}", file=sys.stderr)
            return 1
        return 0

    if args.command == "concepts":
        for gist in tools.list_concepts(args.prefix, domain=args.domain, cfg=cfg):
            print(f"{gist.slug:40s}  {gist.gist}")
        return 0

    if args.command == "domains":
        if args.domains_command == "list":
            for domain in tools.list_domains(cfg=cfg):
                print(f"{domain.name:32s}  {domain.description}")
            return 0
        if args.domains_command in ("add", "update"):
            try:
                domain = tools.upsert_domain(args.name, args.description, cfg=cfg)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            _dump(domain.model_dump(mode="json"))
            return 0
        try:
            removed = tools.remove_domain(args.name, force=args.force, cfg=cfg)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"{'removed' if removed else 'not registered'}: {args.name}")
        return 0 if removed else 1

    if args.command == "compile":
        compiled = tools.compile_update(args.source_id, force=args.force, cfg=cfg)
        _dump(compiled.model_dump(mode="json"))
        return 0

    if args.command == "lexical":
        try:
            counts = tools.rebuild_lexical(domain=args.domain, cfg=cfg)
        except (RuntimeError, KeyError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for index, count in sorted(counts.items()):
            print(f"{index:48s} {count:6d} documents")
        return 0

    if args.command == "synthesize":
        try:
            results = tools.synthesize(domain=None if args.all else args.domain, cfg=cfg)
        except KeyError as exc:
            print(f"no such domain: {exc.args[0]!r}", file=sys.stderr)
            return 1
        for outcome in results:
            state = "written" if outcome.written else f"skipped ({outcome.reason})"
            print(f"{outcome.domain:32s} {state}  pages_read={outcome.pages_read} "
                  f"${outcome.cost_usd:.4f}")
        return 0

    if args.command == "lint":
        report = tools.lint_wiki(dry_run=not args.fix, domain=args.domain, cfg=cfg)
        print(f"{report.page_count} pages, {len(report.findings)} findings "
              f"(domains: {', '.join(report.domains)})")
        for finding in report.findings:
            where = "" if finding.domain == "general" else f" [{finding.domain}]"
            print(f"  {finding.kind:15s} {finding.slug}{where}: {finding.detail}")
        for repair in report.repaired:
            print(f"  repaired: {repair}")
        return 1 if report.findings and report.dry_run else 0

    if args.command == "worker":
        if args.resume:
            print(f"resumed: {tools.resume_processing(cfg=cfg)}")
        _dump(tools.worker_status(cfg=cfg).model_dump(mode="json"))
        return 0

    if args.command == "usage":
        if args.migrate:
            print(f"migrated {tools.migrate_cost_ledger(cfg=cfg)} legacy line(s)")
        summary = tools.usage_summary(month=args.month, domain=args.domain, cfg=cfg)
        if args.json:
            _dump(summary.model_dump(mode="json"))
            return 0
        budget = tools.budget_status(cfg=cfg)
        print(f"{budget['month']}: ${budget['month_usd']:.4f} this month, "
              f"${budget['day_usd']:.4f} today"
              + ("  [PAUSED: hard cap reached]" if budget["capped"] else ""))
        print(f"window: {summary.call_count} calls, ${summary.total_usd:.4f}")
        for title, rows in (("by domain", summary.by_domain), ("by kind", summary.by_kind),
                            ("by op", summary.by_op), ("by model", summary.by_model)):
            if rows:
                print(f"  {title}:")
                for key, usd in sorted(rows.items(), key=lambda kv: -kv[1]):
                    print(f"    {key:40s} ${usd:.4f}")
        return 0

    if args.command == "cost":
        _dump(tools.cost_summary(cfg=cfg).model_dump(mode="json"))
        return 0

    if args.command == "status":
        _dump(tools.health(cfg))
        return 0

    if args.command == "source":
        _dump(tools.get_source_status(args.source_id, cfg=cfg).model_dump(mode="json"))
        return 0

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "llmwiki.api.app:app",
            host=args.host or cfg.api_host,
            port=args.port or cfg.api_port,
            reload=args.reload,
        )
        return 0

    return 1  # pragma: no cover - argparse rejects unknown commands first


def _dump(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
