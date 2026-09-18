#!/usr/bin/env python3
"""Probe the query graph's bounds live: one question, several settings, invariants checked.

Phase 1-D (design §4.9, technical document §3.3 / §10). The unit suite pins the
bounded tool loop with *scripted* decisions; this is the manual counterpart
against a real model and a real corpus (or the offline doubles). It answers a
question under one or a matrix of ``(AGENT_MAX_TOOL_CALLS,
AGENT_WEB_SEARCH_POLICY)`` settings - applied in-process, so ``.env`` stays
untouched and no server restart is needed - and checks what the code
guarantees regardless of what the model wanted:

  - len(steps) <= AGENT_MAX_TOOL_CALLS (0 -> no steps at all: Phase 0's sequence)
  - search_web calls <= AGENT_MAX_WEB_SEARCHES, and none when the policy is
    off, when the backend is none, or when the policy is weak and the wiki had
    a strong hit (no chunk fallback)
  - every citation resolves to a real raw/ object; no external ref is a citation
  - the answer text is non-empty

Per run it prints the tool calls the model made and *why* (the agent_step
decisions, captured from the graph's DEBUG log), citations, external refs,
the LangSmith run_id and the latency. ``--verify-trace`` then fetches that
trace and checks the node / LLM-run names the technical document §10.2 shows.

    scripts/probe_query_graph.py -q "..."                        # the .env settings, one run
    scripts/probe_query_graph.py -q "..." --max-tool-calls 1     # override one bound
    scripts/probe_query_graph.py -q "..." --matrix               # caps 0/1/N x every policy
    scripts/probe_query_graph.py -q "..." --web-search-backend fake --web-search-policy always
    scripts/probe_query_graph.py --offline --matrix              # no keys: fakes + fixture docs
    scripts/probe_query_graph.py -q "..." --verify-trace         # + LangSmith run-tree check
    scripts/probe_query_graph.py -q "..." --json runs.jsonl      # append one record per run

Exit 1 when any invariant fails or a trace check fails - the same posture as
``smoke_flow.py``, so ``--offline --matrix`` can sit next to it in a gate.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
OFFLINE_DOCS = [("sample.pdf", "application/pdf"), ("sample.html", "text/html")]
POLICIES = ("off", "weak", "always")
WEB_TOOL = "search_web"
# Node names one answer() produces (technical document §10.2). agent/tools
# appear only when the loop ran at least one iteration / one tool call.
ALWAYS_NODES = {"retrieve", "select_skills", "generate", "resolve_citations"}
TRACE_WAIT_S = 45


def _offline_env() -> None:
    os.environ.update(STORAGE_BACKEND="local", VECTOR_BACKEND="memory",
                      EMBEDDING_BACKEND="fake", LLM_PROVIDER="fake",
                      WEB_SEARCH_BACKEND="fake", LANGSMITH_TRACING="false")
    os.environ.setdefault("LOCAL_STORAGE_PATH", str(REPO / ".data-probe"))
    # Same guard as smoke_flow.py / eval_answer.py: a real routing table
    # outranks LLM_PROVIDER, so point the loader at a path that does not exist.
    os.environ["LLMWIKI_PROVIDERS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/providers.py"
    os.environ["LLMWIKI_OPS_CONFIG"] = "/nonexistent/llmwiki-offline-guard/ops.py"


def _ingest_fixture_docs(cfg: Any) -> None:
    from llmwiki import tools

    for name, mime in OFFLINE_DOCS:
        ref = tools.ingest_source(file=(FIXTURES / name).read_bytes(), filename=name,
                                  mime=mime, cfg=cfg)
        status = tools.process_source(ref.source_id, cfg=cfg)
        assert status.state == "done", f"{name}: ingest ended in state {status.state}"
        print(f"    ingested {name} -> {ref.source_id}")


def _fixture_question() -> str:
    from llmwiki.eval.dataset import FIXTURE_DATASET, load_examples

    return load_examples(REPO / FIXTURE_DATASET)[0].question


# --- one run -----------------------------------------------------------------


@dataclass
class RunRecord:
    """Everything one probe run is judged on. Serialised as-is with --json."""

    question: str
    max_tool_calls: int
    web_search_policy: str
    web_search_backend: str
    max_web_searches: int
    steps: list[dict] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    used_rag_fallback: bool = False
    citations: list[str] = field(default_factory=list)
    external_refs: list[str] = field(default_factory=list)
    text_chars: int = 0
    run_id: str | None = None
    latency_s: float = 0.0
    failures: list[str] = field(default_factory=list)
    trace_failures: list[str] = field(default_factory=list)


class _DecisionLog(logging.Handler):
    """Captures the graph's per-step decisions (its DEBUG/WARNING lines) for one run."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("agent_step:") or message.startswith("answer_query:"):
            self.lines.append(f"{record.levelname.lower()}: {message}")


def check_invariants(answer: Any, *, max_tool_calls: int, policy: str, max_web: int,
                     backend: str, source_exists: Any) -> list[str]:
    """The code-enforced guarantees of design §4.9, as a list of violations (empty = pass)."""
    failures: list[str] = []
    steps = list(answer.steps)
    if len(steps) > max_tool_calls:
        failures.append(f"{len(steps)} tool call(s) made but AGENT_MAX_TOOL_CALLS={max_tool_calls}")
    web_calls = sum(1 for s in steps if s.tool == WEB_TOOL)
    if web_calls > max_web:
        failures.append(f"{web_calls} search_web call(s) but AGENT_MAX_WEB_SEARCHES={max_web}")
    if policy == "off" and web_calls:
        failures.append("search_web was called with AGENT_WEB_SEARCH_POLICY=off")
    if backend == "none" and web_calls:
        failures.append("search_web was called with WEB_SEARCH_BACKEND=none (no tool should exist)")
    if policy == "weak" and web_calls and not answer.used_rag_fallback:
        failures.append("search_web was called under policy=weak without the chunk fallback")
    if backend == "none" and answer.external_refs:
        failures.append("external_refs present with WEB_SEARCH_BACKEND=none")
    if not web_calls and answer.external_refs:
        failures.append("external_refs present without a search_web step")
    for citation in answer.citations:
        if not source_exists(citation.source_id):
            failures.append(f"citation {citation.source_id!r} does not resolve under raw/")
    ref_urls = {r.url for r in answer.external_refs}
    for citation in answer.citations:
        if citation.url and citation.url in ref_urls:
            failures.append(f"external ref {citation.url} leaked into citations")
    if not answer.text.strip():
        failures.append("empty answer text")
    return failures


def run_once(question: str, cfg: Any, *, max_tool_calls: int, policy: str,
             backend: str, max_web: int) -> RunRecord:
    from llmwiki import tools

    cfg.agent_max_tool_calls = max_tool_calls
    cfg.agent_web_search_policy = policy
    cfg.web_search_backend = backend
    cfg.agent_max_web_searches = max_web

    record = RunRecord(question=question, max_tool_calls=max_tool_calls,
                       web_search_policy=policy, web_search_backend=backend,
                       max_web_searches=max_web)
    graph_logger = logging.getLogger("llmwiki.agent")
    previous_level = graph_logger.level
    capture = _DecisionLog()
    graph_logger.addHandler(capture)
    graph_logger.setLevel(logging.DEBUG)
    started = time.perf_counter()
    try:
        answer = tools.answer(question, cfg=cfg)
    finally:
        record.latency_s = time.perf_counter() - started
        graph_logger.removeHandler(capture)
        graph_logger.setLevel(previous_level)

    record.steps = [s.model_dump() for s in answer.steps]
    record.decisions = capture.lines
    record.used_rag_fallback = answer.used_rag_fallback
    record.citations = [c.source_id for c in answer.citations]
    record.external_refs = [r.url for r in answer.external_refs]
    record.text_chars = len(answer.text)
    record.run_id = answer.run_id
    record.failures = check_invariants(
        answer, max_tool_calls=max_tool_calls, policy=policy, max_web=max_web,
        backend=backend, source_exists=lambda sid: tools.source_exists(sid, cfg=cfg),
    )
    return record


def _print_run(record: RunRecord, verbose: bool) -> None:
    label = (f"cap={record.max_tool_calls} policy={record.web_search_policy} "
             f"backend={record.web_search_backend} max_web={record.max_web_searches}")
    flag = "FAIL" if record.failures else "ok  "
    print(f"\n{flag} [{label}] {record.latency_s:.1f}s")
    steps = ", ".join(f"{s['tool']}({s['chars']} chars)" for s in record.steps) or "-"
    print(f"      steps ({len(record.steps)}): {steps}")
    print(f"      used_rag_fallback={record.used_rag_fallback} "
          f"citations={record.citations} external_refs={record.external_refs} "
          f"answer={record.text_chars} chars")
    if record.run_id:
        print(f"      run_id: {record.run_id}")
    if verbose or record.failures:
        for line in record.decisions:
            print(f"      {line}")
    for failure in record.failures:
        print(f"      !! {failure}")


# --- the LangSmith run tree -------------------------------------------------


def trace_checks(names: dict[str, int], llm_names: dict[str, int], *, root_name: str | None,
                 steps: int, max_tool_calls: int, expect_llm_runs: bool) -> list[str]:
    """Compare a fetched run tree with what one answer() must produce (§10.2)."""
    failures: list[str] = []
    if root_name != "answer_query":
        failures.append(f"root run is {root_name!r}, expected 'answer_query'")
    missing = sorted(ALWAYS_NODES - names.keys())
    if missing:
        failures.append(f"node run(s) missing from the trace: {missing}")
    if names.get("tools", 0) != steps:
        failures.append(f"{names.get('tools', 0)} 'tools' node run(s) but the answer "
                        f"recorded {steps} step(s)")
    if names.get("agent", 0) > max_tool_calls + 1:
        failures.append(f"{names['agent']} 'agent' node runs but the cap allows at most "
                        f"{max_tool_calls + 1} decisions")
    if max_tool_calls == 0 and names.get("agent", 0):
        failures.append("'agent' node ran with AGENT_MAX_TOOL_CALLS=0")
    if expect_llm_runs:
        if "answer_query" not in llm_names:
            failures.append("no LLM run named 'answer_query' (LangChainLLM run_name missing?)")
        if steps and "agent_step" not in llm_names:
            failures.append("tool calls were made but no LLM run is named 'agent_step'")
        if llm_names.get("agent_step", 0) > 2 * (max_tool_calls + 1):
            failures.append(f"{llm_names['agent_step']} agent_step LLM runs exceeds "
                            f"2 x (cap + 1) - STEP_ATTEMPTS retries out of bounds?")
    return failures


def verify_trace(record: RunRecord, cfg: Any) -> None:
    if not record.run_id:
        record.trace_failures.append("no run_id on the answer - LANGSMITH_TRACING is off "
                                     "in this process")
        return
    from langsmith import Client

    client = Client(api_key=cfg.langsmith_api_key.get_secret_value(),
                    api_url=cfg.langsmith_endpoint or None)
    deadline = time.monotonic() + TRACE_WAIT_S
    runs: list[Any] = []
    while time.monotonic() < deadline:
        runs = list(client.list_runs(project_name=cfg.langsmith_project, trace_id=record.run_id))
        # The tracer uploads in the background; wait until the root has ended.
        root = next((r for r in runs if str(r.id) == record.run_id), None)
        if root is not None and root.end_time is not None:
            break
        time.sleep(3)
    if not runs:
        record.trace_failures.append(f"trace {record.run_id} not found in project "
                                     f"{cfg.langsmith_project!r} after {TRACE_WAIT_S}s")
        return

    by_id = {str(r.id): r for r in runs}

    def depth(run: Any) -> int:
        d, parent = 0, run.parent_run_id
        while parent is not None and str(parent) in by_id:
            d, parent = d + 1, by_id[str(parent)].parent_run_id
        return d

    print("      trace:")
    for run in sorted(runs, key=lambda r: r.dotted_order or ""):
        usage = ""
        if run.run_type == "llm":
            tokens = (run.prompt_tokens or 0, run.completion_tokens or 0)
            usage = f"  prompt={tokens[0]} completion={tokens[1]}"
        print(f"        {'  ' * depth(run)}{run.name} [{run.run_type}]{usage}")

    names: dict[str, int] = {}
    llm_names: dict[str, int] = {}
    for run in runs:
        target = llm_names if run.run_type == "llm" else names
        target[run.name] = target.get(run.name, 0) + 1
    root = by_id.get(record.run_id)
    record.trace_failures = trace_checks(
        names, llm_names, root_name=root.name if root else None, steps=len(record.steps),
        max_tool_calls=record.max_tool_calls,
        # FakeLLM is not a LangChain model, so it makes no LLM runs (§10.2).
        expect_llm_runs=cfg.llm_provider != "fake",
    )
    if root is not None:
        meta = (root.extra or {}).get("metadata", {})
        for key in ("agent_max_tool_calls", "agent_web_search_policy"):
            if key not in meta:
                record.trace_failures.append(f"root metadata lacks {key!r}")
    for failure in record.trace_failures:
        print(f"      !! trace: {failure}")
    if not record.trace_failures:
        print("      trace: ok")


# --- main ---------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-q", "--question", action="append", default=[],
                    help="question to ask (repeatable; --offline defaults to the fixture set's "
                         "first)")
    ap.add_argument("--max-tool-calls", type=int, default=None,
                    help="override AGENT_MAX_TOOL_CALLS")
    ap.add_argument("--web-search-policy", choices=POLICIES, default=None,
                    help="override AGENT_WEB_SEARCH_POLICY")
    ap.add_argument("--web-search-backend", choices=("none", "tavily", "fake"), default=None,
                    help="override WEB_SEARCH_BACKEND")
    ap.add_argument("--max-web-searches", type=int, default=None,
                    help="override AGENT_MAX_WEB_SEARCHES")
    ap.add_argument("--matrix", action="store_true",
                    help="run caps 0, 1 and the configured cap x every policy the backend allows")
    ap.add_argument("--offline", action="store_true",
                    help="fake adapters, fixture docs, fake web searcher - no keys")
    ap.add_argument("--verify-trace", action="store_true",
                    help="fetch each run's LangSmith trace and check its shape")
    ap.add_argument("--json", type=Path, default=None, help="append one JSON record per run")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print the agent_step decisions for every run, not only failing ones")
    args = ap.parse_args()

    if args.offline:
        _offline_env()

    from llmwiki import __version__, tools
    from llmwiki.config import load_settings

    cfg = load_settings()
    health = tools.health(cfg)["config"]
    print(f"llmwiki {__version__} | llm={cfg.llm_provider} routing={health['llm_routing']} "
          f"| storage={cfg.storage_backend} vectors={cfg.vector_backend}")
    if args.offline:
        print("\n[offline] ingesting fixture documents")
        _ingest_fixture_docs(cfg)

    questions = args.question or ([_fixture_question()] if args.offline else [])
    if not questions:
        ap.error("-q/--question is required unless --offline")

    backend = args.web_search_backend or cfg.web_search_backend
    max_web = (args.max_web_searches if args.max_web_searches is not None
               else cfg.agent_max_web_searches)
    base_cap = args.max_tool_calls if args.max_tool_calls is not None else cfg.agent_max_tool_calls
    base_policy = args.web_search_policy or cfg.agent_web_search_policy

    if args.matrix:
        caps = sorted({0, 1, base_cap})
        policies = list(POLICIES) if backend != "none" else ["off"]
        if backend == "none":
            print("\n[matrix] WEB_SEARCH_BACKEND=none: only policy=off is meaningful "
                  "(pass --web-search-backend fake|tavily to cover weak/always)")
        settings_grid = [(cap, policy) for cap in caps for policy in policies]
    else:
        settings_grid = [(base_cap, base_policy)]
    if args.verify_trace and not cfg.langsmith_tracing:
        print("\n[verify-trace] LANGSMITH_TRACING is false in this process - traces cannot "
              "be verified")

    records: list[RunRecord] = []
    for question in questions:
        print(f"\n=== {question}")
        for cap, policy in settings_grid:
            record = run_once(question, cfg, max_tool_calls=cap, policy=policy,
                              backend=backend, max_web=max_web)
            _print_run(record, args.verbose)
            if args.verify_trace:
                verify_trace(record, cfg)
            records.append(record)
            if args.json:
                with args.json.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(record), default=str) + "\n")

    failed = [r for r in records if r.failures or r.trace_failures]
    print(f"\n{len(records)} run(s), {len(failed)} failed")
    if failed:
        print("PROBE FAIL")
        return 1
    print("PROBE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
