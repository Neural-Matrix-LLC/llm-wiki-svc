#!/usr/bin/env python3
"""Evaluate answer quality against the golden set; push it; promote corrections.

The eval half of Phase 1-D (design §4.9, technical document §10). Runs the
golden set through ``tools.answer`` and scores every answer with the
evaluators in ``llmwiki.eval.evaluators``.

    scripts/eval_answer.py --offline                 # no keys: fake adapters + fixture docs/set
    scripts/eval_answer.py                           # local run against the real backends in .env
    scripts/eval_answer.py --judge                   # + LLM-as-judge groundedness (costs tokens)
    scripts/eval_answer.py --push                    # mirror the JSONL to LangSmith
    scripts/eval_answer.py --langsmith --judge       # run as a LangSmith experiment, prints its URL
    scripts/eval_answer.py --export-failures f.jsonl # failing examples + actual output
    scripts/eval_answer.py --promote-feedback        # corrected runs (POST /feedback) -> JSONL
    scripts/eval_answer.py --dataset my/golden.jsonl # your corpus's own golden set

Exit code is 1 when any gated evaluator (citations_resolve, expected_source_cited,
must_mention) scores below its threshold in a local run - the same posture as
``smoke_flow.py``, so ``--offline`` can sit in the pre-commit gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
OFFLINE_DOCS = [("sample.pdf", "application/pdf"), ("sample.html", "text/html")]


def _offline_env() -> None:
    os.environ.update(STORAGE_BACKEND="local", VECTOR_BACKEND="memory",
                      EMBEDDING_BACKEND="fake", LLM_PROVIDER="fake",
                      WEB_SEARCH_BACKEND="none", LANGSMITH_TRACING="false")
    os.environ.setdefault("LOCAL_STORAGE_PATH", str(REPO / ".data-eval"))
    # Same guard as smoke_flow.py: a real routing table outranks LLM_PROVIDER.
    os.environ["LLMWIKI_PROVIDERS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/providers.py"
    os.environ["LLMWIKI_OPS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/ops.py"


def _ingest_fixture_docs(cfg) -> None:
    from llmwiki import tools

    for name, mime in OFFLINE_DOCS:
        path = FIXTURES / name
        ref = tools.ingest_source(file=path.read_bytes(), filename=name, mime=mime, cfg=cfg)
        status = tools.process_source(ref.source_id, cfg=cfg)
        assert status.state == "done", f"{name}: ingest ended in state {status.state}"
        print(f"    ingested {name} -> {ref.source_id}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", type=Path, default=None,
                    help="golden set JSONL (default: tests/fixtures/eval/answer_quality.jsonl)")
    ap.add_argument("--offline", action="store_true",
                    help="fake adapters, ingest the fixture docs first - no keys, no network")
    ap.add_argument("--judge", action="store_true", help="add the LLM-as-judge evaluator")
    ap.add_argument("--push", action="store_true", help="mirror the JSONL to LangSmith")
    ap.add_argument("--langsmith", action="store_true",
                    help="run as a LangSmith experiment instead of locally")
    ap.add_argument("--experiment-prefix", default="llmwiki")
    ap.add_argument("--export-failures", type=Path, default=None,
                    help="write failing examples with their actual output to this JSONL")
    ap.add_argument("--promote-feedback", action="store_true",
                    help="append corrected runs from LangSmith feedback to the golden set")
    args = ap.parse_args()

    if args.offline:
        _offline_env()

    from llmwiki import __version__, tools
    from llmwiki.config import load_settings
    from llmwiki.eval import evaluators as ev
    from llmwiki.eval.dataset import FIXTURE_DATASET, append_examples, load_examples, push_dataset
    from llmwiki.eval.run import run_experiment, run_local

    cfg = load_settings()
    dataset_path = args.dataset or (REPO / FIXTURE_DATASET)
    print(f"llmwiki {__version__} | llm={cfg.llm_provider} routing="
          f"{tools.health(cfg)['config']['llm_routing']} | dataset={dataset_path}")

    if args.promote_feedback:
        from llmwiki.eval.feedback import corrected_examples

        cfg.require("langsmith_api_key")
        promoted = corrected_examples(
            api_key=cfg.langsmith_api_key.get_secret_value(), project=cfg.langsmith_project,
            endpoint=cfg.langsmith_endpoint,
        )
        n = append_examples(dataset_path, promoted)
        print(f"promoted {n} corrected run(s) into {dataset_path}")
        for example in promoted:
            print(f"    + {example.question!r} sources={example.expected_sources}")
        if not (args.push or args.langsmith):
            return 0

    examples = load_examples(dataset_path)
    print(f"{len(examples)} example(s)")

    if args.push:
        cfg.require("langsmith_api_key")
        dataset_id = push_dataset(
            examples, cfg.langsmith_eval_dataset,
            api_key=cfg.langsmith_api_key.get_secret_value(), endpoint=cfg.langsmith_endpoint,
        )
        print(f"pushed to LangSmith dataset {cfg.langsmith_eval_dataset!r} ({dataset_id})")
        if not args.langsmith:
            return 0

    evaluators = list(ev.DETERMINISTIC) + ([ev.judge_grounded] if args.judge else [])

    if args.offline:
        print("\n[offline] ingesting fixture documents")
        _ingest_fixture_docs(cfg)

    if args.langsmith:
        results = run_experiment(cfg.langsmith_eval_dataset, evaluators,
                                 experiment_prefix=args.experiment_prefix, cfg=cfg)
        print(f"\nexperiment: {results.experiment_name}")
        return 0

    print("\nrunning locally")
    result = run_local(examples, evaluators, cfg=cfg)
    for row in result.rows:
        flag = "FAIL" if row.failed else "ok  "
        print(f"\n{flag} {row.example.question}")
        for score in row.scores:
            print(f"      {score['key']:<24} {float(score['score']):.2f}  {score['comment']}")
        if row.outputs.get("steps"):
            print(f"      tools: {[s['tool'] for s in row.outputs['steps']]}")

    print("\nsummary")
    for key in ("citations_resolve", "expected_source_cited", "must_mention", "tool_calls",
                "judge_grounded"):
        mean = result.mean(key)
        if mean is not None:
            print(f"    {key:<24} mean {mean:.2f}")

    if args.export_failures is not None:
        with args.export_failures.open("w", encoding="utf-8") as fh:
            for row in result.failures:
                record = row.example.model_dump()
                record["actual"] = {
                    "text": row.outputs.get("text", ""),
                    "citations": row.outputs.get("citations", []),
                    "failed": row.failed,
                }
                fh.write(json.dumps(record) + "\n")
        print(f"\nwrote {len(result.failures)} failing example(s) to {args.export_failures}")

    if result.failures:
        print(f"\nEVAL FAIL: {len(result.failures)} of {len(result.rows)} example(s) "
              "below threshold", file=sys.stderr)
        return 1
    print("\nEVAL PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"\nEVAL FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
