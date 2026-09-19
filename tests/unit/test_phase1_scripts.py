"""The two Phase 1 manual-test scripts: check_local_llm.py (C) and probe_query_graph.py (D).

Both are standalone entry points under scripts/, loaded here by path. Their
network halves (a live vLLM/llama.cpp server, a LangSmith trace) are exercised
by hand per docs/phase1-manual-test-plan-C-D.md; what this file pins is the
judgement each script applies to what it sees - the invariant and trace checks
of the probe, and the config/served-model resolution of the local-LLM check -
plus one offline end-to-end run of the probe, the same way smoke_flow.py
--offline is gated.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from llmwiki.models.plan import AgentStep, Answer, Citation, ExternalRef

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve `from __future__` annotations via sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe() -> ModuleType:
    return _load("probe_query_graph")


@pytest.fixture(scope="module")
def local_llm() -> ModuleType:
    return _load("check_local_llm")


# --- probe_query_graph.check_invariants ---------------------------------------


def _answer(**overrides) -> Answer:
    base = dict(text="An answer.", citations=[Citation(source_id="abc-doc")], steps=[])
    base.update(overrides)
    return Answer(**base)


def _check(probe: ModuleType, answer: Answer, **kw) -> list[str]:
    defaults = dict(max_tool_calls=4, policy="off", max_web=1, backend="none",
                    source_exists=lambda sid: sid == "abc-doc")
    defaults.update(kw)
    return probe.check_invariants(answer, **defaults)


def test_a_clean_answer_passes_every_invariant(probe) -> None:
    assert _check(probe, _answer()) == []


def test_more_steps_than_the_cap_is_a_violation(probe) -> None:
    steps = [AgentStep(tool="search_chunks") for _ in range(3)]
    failures = _check(probe, _answer(steps=steps), max_tool_calls=2)
    assert any("AGENT_MAX_TOOL_CALLS=2" in f for f in failures)
    assert _check(probe, _answer(steps=steps), max_tool_calls=3) == []


def test_cap_zero_means_no_steps_at_all(probe) -> None:
    failures = _check(probe, _answer(steps=[AgentStep(tool="get_page")]), max_tool_calls=0)
    assert failures and "AGENT_MAX_TOOL_CALLS=0" in failures[0]


def test_search_web_under_policy_off_or_backend_none_is_a_violation(probe) -> None:
    web = _answer(steps=[AgentStep(tool="search_web")],
                  external_refs=[ExternalRef(url="https://x.test/1")])
    off = _check(probe, web, policy="off", backend="fake")
    assert any("POLICY=off" in f for f in off)
    none = _check(probe, web, policy="always", backend="none")
    assert any("BACKEND=none" in f for f in none)
    assert _check(probe, web, policy="always", backend="fake") == []


def test_policy_weak_needs_the_chunk_fallback_first(probe) -> None:
    web = _answer(steps=[AgentStep(tool="search_web")],
                  external_refs=[ExternalRef(url="https://x.test/1")])
    assert any("weak" in f for f in _check(probe, web, policy="weak", backend="fake"))
    web.used_rag_fallback = True
    assert _check(probe, web, policy="weak", backend="fake") == []


def test_web_search_count_is_bounded(probe) -> None:
    web = _answer(steps=[AgentStep(tool="search_web"), AgentStep(tool="search_web")],
                  external_refs=[ExternalRef(url="https://x.test/1")], used_rag_fallback=True)
    assert any("AGENT_MAX_WEB_SEARCHES=1" in f
               for f in _check(probe, web, policy="always", backend="fake", max_web=1))
    assert _check(probe, web, policy="always", backend="fake", max_web=2) == []


def test_every_citation_must_resolve_and_no_external_ref_may_be_one(probe) -> None:
    ghost = _answer(citations=[Citation(source_id="ghost")])
    assert any("does not resolve" in f for f in _check(probe, ghost))

    leaked = _answer(
        steps=[AgentStep(tool="search_web")], used_rag_fallback=True,
        external_refs=[ExternalRef(url="https://x.test/1")],
        citations=[Citation(source_id="abc-doc", url="https://x.test/1")],
    )
    failures = _check(probe, leaked, policy="always", backend="fake")
    assert any("leaked into citations" in f for f in failures)


def test_external_refs_without_a_search_step_and_empty_text_are_violations(probe) -> None:
    orphan = _answer(external_refs=[ExternalRef(url="https://x.test/1")])
    assert any("without a search_web step" in f for f in _check(probe, orphan, backend="fake"))
    assert any("empty answer" in f for f in _check(probe, _answer(text="  ")))


# --- probe_query_graph.trace_checks -------------------------------------------


def _tree(**counts: int) -> dict[str, int]:
    base = {"retrieve": 1, "select_skills": 1, "generate": 1, "resolve_citations": 1}
    base.update(counts)
    return base


def test_a_complete_trace_passes(probe) -> None:
    failures = probe.trace_checks(
        _tree(agent=2, tools=1), {"agent_step": 2, "answer_query": 2},
        root_name="answer_query", steps=1, max_tool_calls=4, expect_llm_runs=True,
    )
    assert failures == []


def test_trace_must_have_every_always_node_and_the_right_root(probe) -> None:
    failures = probe.trace_checks(
        {"retrieve": 1}, {"answer_query": 1}, root_name="RunnableSequence", steps=0,
        max_tool_calls=4, expect_llm_runs=True,
    )
    assert any("root run" in f for f in failures)
    assert any("missing" in f and "generate" in f for f in failures)


def test_trace_tools_runs_must_match_the_recorded_steps_and_cap(probe) -> None:
    mismatch = probe.trace_checks(
        _tree(agent=2, tools=2), {"agent_step": 2, "answer_query": 1},
        root_name="answer_query", steps=1, max_tool_calls=4, expect_llm_runs=True,
    )
    assert any("'tools' node run" in f for f in mismatch)
    over = probe.trace_checks(
        _tree(agent=3, tools=1), {"agent_step": 3, "answer_query": 1},
        root_name="answer_query", steps=1, max_tool_calls=1, expect_llm_runs=True,
    )
    assert any("'agent' node runs" in f for f in over)
    zero = probe.trace_checks(
        _tree(agent=1), {"answer_query": 1}, root_name="answer_query", steps=0,
        max_tool_calls=0, expect_llm_runs=True,
    )
    assert any("AGENT_MAX_TOOL_CALLS=0" in f for f in zero)


def test_trace_llm_run_names_are_only_required_for_a_real_model(probe) -> None:
    args = dict(root_name="answer_query", steps=1, max_tool_calls=4)
    assert any("agent_step" in f for f in probe.trace_checks(
        _tree(agent=2, tools=1), {"answer_query": 1}, expect_llm_runs=True, **args))
    # FakeLLM is not a LangChain model: node runs only, no LLM runs (tech doc §10.2)
    assert probe.trace_checks(_tree(agent=2, tools=1), {}, expect_llm_runs=False, **args) == []


# --- check_local_llm: config resolution and served-model matching -----------


PROVIDERS_PY = '''
PROVIDERS = [
    {"provider": "openrouter", "api_key_env": "OPENROUTER_API_KEY"},
    {"provider": "vllm", "api_key_env": "VLLM_API_KEY", "base_url_env": "VLLM_BASE_URL"},
    {"provider": "llamacpp", "api_key_env": "LLAMACPP_API_KEY",
     "base_url_env": "LLAMACPP_BASE_URL"},
    {"provider": "fake"},
]
'''


def test_only_local_providers_with_a_key_are_active(local_llm, tmp_path, monkeypatch) -> None:
    path = tmp_path / "providers.py"
    path.write_text(PROVIDERS_PY)
    for var in ("VLLM_API_KEY", "VLLM_BASE_URL", "LLAMACPP_API_KEY", "LLAMACPP_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    dotenv = {"VLLM_API_KEY": "k", "VLLM_BASE_URL": "http://gpu:8100/v1", "LLAMACPP_API_KEY": ""}
    assert local_llm.active_local_providers(path, dotenv) == {"vllm": ("k", "http://gpu:8100/v1")}
    # the real environment outranks .env, and --provider narrows the set
    monkeypatch.setenv("LLAMACPP_API_KEY", "cpp")
    monkeypatch.setenv("LLAMACPP_BASE_URL", "http://gpu:8080/v1")
    active = local_llm.active_local_providers(path, dotenv)
    assert set(active) == {"vllm", "llamacpp"}
    assert local_llm.active_local_providers(path, dotenv, only="llamacpp") == {
        "llamacpp": ("cpp", "http://gpu:8080/v1")
    }


def test_routed_model_must_be_one_the_server_serves(local_llm, capsys) -> None:
    from llmwiki.llm.routing_config import OpRoute

    routes = {
        "summarize_source": OpRoute("summarize_source", "vllm", "qwen2.5-14b", 1.0, 2048),
        "plan_compile": OpRoute("plan_compile", "llamacpp", "qwen2.5-14b-gguf", 1.0, 2048),
    }
    report = local_llm.Report()
    local_llm.check_routes_served("vllm", ["qwen2.5-14b"], routes, report)
    assert not report.failed
    local_llm.check_routes_served("vllm", ["Qwen/Qwen2.5-14B-Instruct-AWQ"], routes, report)
    assert report.failed
    out = capsys.readouterr().out
    assert "--served-model-name" in out
    # the llamacpp row is judged only against the llamacpp server
    assert "plan_compile" not in out


# --- the probe's offline end-to-end run -----------------------------------


def test_probe_offline_matrix_passes_end_to_end(tmp_path) -> None:
    env = {**os.environ, "LOCAL_STORAGE_PATH": str(tmp_path / "data")}
    result = subprocess.run(
        # the cap is passed explicitly: conftest's autouse fixture pins
        # AGENT_MAX_TOOL_CALLS=0 in this environment and the child inherits it
        [sys.executable, str(SCRIPTS / "probe_query_graph.py"), "--offline", "--matrix",
         "--max-tool-calls", "4"],
        capture_output=True, text=True, env=env, cwd=REPO, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE PASS" in result.stdout
    # caps 0/1/4 x off/weak/always: the fake web searcher makes every policy meaningful
    assert result.stdout.count("\nok   [cap=") == 9


# --- Phase 2: scripts/probe_domain_routing.py ------------------------------------------------


def test_probe_domain_routing_verdicts() -> None:
    from llmwiki.models.source import DomainAssignment

    probe = _load("probe_domain_routing")
    assert probe.decide(DomainAssignment(domain="ml", confidence=0.9), 0.6) == "routed"
    assert probe.decide(DomainAssignment(domain="ml", confidence=0.3), 0.6) == "demoted"
    assert probe.decide(DomainAssignment(domain="general"), 0.6) == "general"
    assert probe.decide(DomainAssignment(domain="general", suggested_domain="x"), 0.6) == (
        "general+suggest")


def test_probe_domain_routing_runs_offline_end_to_end(tmp_path) -> None:
    """Fakes + fixture docs + a two-domain registry: exit 0 and one line per source."""
    env = {**os.environ, "LOCAL_STORAGE_PATH": str(tmp_path / "data"),
           "LANGSMITH_TRACING": "false"}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "probe_domain_routing.py"), "--offline"],
        capture_output=True, text=True, env=env, cwd=REPO, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "registry: general, retrieval, web-standards" in result.stdout
    assert result.stdout.count("conf=") == 2


# --- Phase 2: scripts/migrate_phase2.py -------------------------------------------------------


def test_migrate_phase2_check_then_apply_offline(tmp_path) -> None:
    """--check exits 1 while the keyword index is unbuilt, --apply builds it, --check exits 0."""
    from llmwiki import factory, tools
    from llmwiki.config import Settings
    from llmwiki.storage.layout import COST_KEY

    data = tmp_path / "data"
    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake", local_storage_path=data,
                   embedding_dim=64, worker_mode="inline", lexical_backend="none")
    factory.reset()
    try:
        tools.ingest_now(text="# Firmware\n\nThe XK-7781 controller.\n", cfg=cfg)
        factory.object_store(cfg).put(COST_KEY, b'{"op":"old","model":"m","cost_usd":0.5}\n')
    finally:
        factory.reset()

    env = {**os.environ, "LOCAL_STORAGE_PATH": str(data), "LANGSMITH_TRACING": "false",
           "LEXICAL_BACKEND": "sqlite"}

    def run(*flags: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "migrate_phase2.py"), "--offline",
                               *flags], capture_output=True, text=True, env=env, cwd=REPO,
                              timeout=120)

    check = run("--check")
    assert check.returncode == 1, check.stdout + check.stderr
    assert "to build:" in check.stdout and "legacy wiki/_meta/cost.jsonl present" in check.stdout

    apply = run("--apply")
    assert apply.returncode == 0, apply.stdout + apply.stderr
    assert "MIGRATION DONE" in apply.stdout and "moved 1 line(s)" in apply.stdout
    assert not (data / "wiki" / "_meta" / "cost.jsonl").exists()
    assert list((data / "lexical").glob("*.sqlite"))

    recheck = run("--check")
    assert recheck.returncode == 0 and "nothing to do" in recheck.stdout
