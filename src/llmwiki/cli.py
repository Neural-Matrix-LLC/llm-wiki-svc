"""``llmwiki`` command line: ingest, search, page, compile, lint, cost, serve.

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
    group.add_argument("--url")
    group.add_argument("--file", type=Path)
    ingest.add_argument("--title", default="")

    search = sub.add_parser("search", help="search the knowledge base")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)

    ask = sub.add_parser("ask", help="answer a question with citations")
    ask.add_argument("query")

    page = sub.add_parser("page", help="print one wiki page")
    page.add_argument("slug")

    concepts = sub.add_parser("concepts", help="list page gists")
    concepts.add_argument("--prefix")

    compile_cmd = sub.add_parser("compile", help="re-run the compiler for one source")
    compile_cmd.add_argument("source_id")
    compile_cmd.add_argument("--force", action="store_true")

    lint = sub.add_parser("lint", help="run the global wiki lint")
    lint.add_argument("--fix", action="store_true", help="apply safe repairs")

    sub.add_parser("cost", help="summarize the measured cost ledger")
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

    # Imported after the environment is settled so the factory sees the right backends.
    from llmwiki import factory, tools
    from llmwiki.config import load_settings

    cfg = load_settings()
    factory.reset()

    if args.command == "ingest":
        if args.url:
            status = tools.ingest_now(url=args.url, title=args.title, cfg=cfg)
        else:
            data = args.file.read_bytes()
            status = tools.ingest_now(
                file=data,
                filename=args.file.name,
                title=args.title or args.file.stem,
                cfg=cfg,
            )
        _dump(status.model_dump(mode="json"))
        return 0 if status.state == "done" else 1

    if args.command == "search":
        hits = tools.search_wiki(args.query, k=args.k, cfg=cfg)
        for hit in hits:
            print(f"{hit.score:.3f}  {hit.origin:5s}  {hit.slug or hit.source_id}")
        return 0

    if args.command == "ask":
        result = tools.answer(args.query, cfg=cfg)
        print(result.text)
        print()
        for citation in result.citations:
            print(f"  [{citation.source_id}] {citation.title}")
        return 0

    if args.command == "page":
        from llmwiki.wiki.pages import render_page

        try:
            print(render_page(tools.get_page(args.slug, cfg=cfg)), end="")
        except KeyError:
            print(f"no page {args.slug!r}", file=sys.stderr)
            return 1
        return 0

    if args.command == "concepts":
        for gist in tools.list_concepts(args.prefix, cfg=cfg):
            print(f"{gist.slug:40s}  {gist.gist}")
        return 0

    if args.command == "compile":
        compiled = tools.compile_update(args.source_id, force=args.force, cfg=cfg)
        _dump(compiled.model_dump(mode="json"))
        return 0

    if args.command == "lint":
        report = tools.lint_wiki(dry_run=not args.fix, cfg=cfg)
        print(f"{report.page_count} pages, {len(report.findings)} findings")
        for finding in report.findings:
            print(f"  {finding.kind:15s} {finding.slug}: {finding.detail}")
        for repair in report.repaired:
            print(f"  repaired: {repair}")
        return 1 if report.findings and report.dry_run else 0

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
