#!/usr/bin/env python3
"""Diagnose a self-hosted vLLM / llama.cpp endpoint before flipping local routing on.

Phase 1-C (KB design §5, implement-plan.md Part II §7.4's 2026-09-14 addendum).
``config/providers.py`` gives ``vllm`` and ``llamacpp`` their own
``*_API_KEY``/``*_BASE_URL`` pairs; ``config/ops.py`` decides which ops go
there. Everything between those two files and a working compile is what this
script checks, one pass/fail line each, with a remediation hint:

  1. Config      - which local providers are active (their env-var pair is set);
                   base URL looks like an OpenAI-compatible root (.../v1)
  2. Reachable   - GET {base_url}/models answers 200 with the bearer key; lists
                   the served model ids
  3. Routing     - every config/ops.py row that names a local provider names a
                   model the server actually serves (--served-model-name / --alias)
  4. Completion  - one plain chat completion through the project's own adapter
                   (LangChainLLM over ChatOpenAI, the exact class the router
                   builds); real, nonzero token counts come back
  5. Structured  - one forced-tool-call completion - the shape summarize_source,
                   plan_compile, agent_step and judge_answer all use. A server
                   that cannot do this compiles nothing.
  6. Routed op   - (--op) the real routed LLMClient.complete(op=...) built by
                   factory.llm_client, i.e. precisely what the compiler calls;
                   prints the CostRecord (cost_usd 0.0 is expected: local
                   models are not in llm/pricing.py, token counts must be real)

    scripts/check_local_llm.py                        # 1-5 for every active local provider
    scripts/check_local_llm.py --provider vllm        # one of them
    scripts/check_local_llm.py --quick                # 1-3 only: no tokens generated
    scripts/check_local_llm.py --model qwen2.5-14b    # model for checks 4-5
    scripts/check_local_llm.py --op summarize_source --op plan_compile   # + check 6 per op

Exit 1 if any check fails - including "no local provider is active at all",
since the only reason to run this is that you expect one to be.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from llmwiki.config import Settings, load_settings

LOCAL_PROVIDERS = ("vllm", "llamacpp")
PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "one word"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["answer"],
}
PROBE_SYSTEM = "You are a connectivity probe. Follow the instruction exactly."
PROBE_PROMPT = "Reply with the single word: pong"
HTTP_TIMEOUT_S = 15.0

STRUCTURED_HINT = {
    "vllm": "named tool_choice needs guided decoding (on by default) - if the server "
            "returned 400 mentioning tool_choice/tools, start it with "
            "--enable-auto-tool-choice --tool-call-parser hermes (Qwen) and check "
            "`vllm serve --help` for your version",
    "llamacpp": "llama-server needs --jinja for OpenAI-style tool calls, and a chat "
                "template that supports tools (Qwen2.5-Instruct GGUFs do); older "
                "builds ignore a named tool_choice and answer in prose",
}


class Report:
    """Collects pass/fail/skip lines and remembers whether anything failed."""

    def __init__(self) -> None:
        self.failed = False

    def ok(self, message: str) -> None:
        print(f"  [OK]   {message}")

    def fail(self, message: str, hint: str = "") -> None:
        self.failed = True
        print(f"  [FAIL] {message}")
        if hint:
            print(f"         -> {hint}")

    def skip(self, message: str) -> None:
        print(f"  [SKIP] {message}")

    def warn(self, message: str) -> None:
        print(f"  [WARN] {message}")


# --- 1. config ----------------------------------------------------------------


def _providers_rows(path: Path) -> list[dict]:
    """The PROVIDERS list of config/providers.py, loaded as the router loads it."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = getattr(module, "PROVIDERS", None)
    if not isinstance(rows, list):
        raise RuntimeError(f"{path} defines no PROVIDERS list")
    return rows


def _env(name: str, dotenv: dict[str, str]) -> str:
    # Same precedence as llmwiki.llm.routing_config: the real process
    # environment wins over .env.
    return (os.environ.get(name) or dotenv.get(name) or "").strip()


def active_local_providers(providers_path: Path, dotenv: dict[str, str],
                           only: str | None = None) -> dict[str, tuple[str, str]]:
    """``provider -> (api_key, base_url)`` for every local provider whose key is set."""
    active: dict[str, tuple[str, str]] = {}
    for row in _providers_rows(providers_path):
        name = row.get("provider")
        if name not in LOCAL_PROVIDERS or (only and name != only):
            continue
        key = _env(row.get("api_key_env", ""), dotenv)
        if not key:
            continue
        active[name] = (key, _env(row.get("base_url_env", ""), dotenv))
    return active


def check_config(settings: Settings, report: Report,
                 only: str | None) -> dict[str, tuple[str, str]]:
    print("1. Config")
    providers_path = settings.llm_providers_config
    if not providers_path.exists():
        report.fail(f"{providers_path} does not exist - the router is not in force",
                    "local routing needs config/providers.py + config/ops.py (both tracked)")
        return {}
    dotenv = {k: v for k, v in dotenv_values(".env").items() if v is not None}
    active = active_local_providers(providers_path, dotenv, only)
    wanted = [only] if only else list(LOCAL_PROVIDERS)
    for name in wanted:
        if name not in active:
            report.skip(f"{name}: inactive ({name.upper()}_API_KEY unset or empty)")
            continue
        key, base_url = active[name]
        if not base_url:
            report.fail(f"{name}: {name.upper()}_BASE_URL is empty",
                        "point it at http://<host>:<port>/v1")
            continue
        if not base_url.rstrip("/").endswith("/v1"):
            report.warn(f"{name}: base URL {base_url!r} does not end in /v1 - "
                        "ChatOpenAI appends /chat/completions to it as-is")
        if any(h in base_url for h in ("localhost", "127.0.0.1")):
            report.warn(f"{name}: base URL points at localhost - unreachable from a "
                        "container unless the server runs in the same network namespace")
        report.ok(f"{name}: active, base_url={base_url}")
    if not active:
        report.fail("no local provider is active",
                    "set VLLM_API_KEY/VLLM_BASE_URL (and/or LLAMACPP_*) in .env")
    return active


# --- 2. reachability ----------------------------------------------------------


def check_reachable(name: str, key: str, base_url: str, report: Report) -> list[str]:
    print(f"2. Reachable - {name}")
    url = base_url.rstrip("/") + "/models"
    started = time.perf_counter()
    try:
        response = httpx.get(url, headers={"Authorization": f"Bearer {key}"},
                             timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        report.fail(f"{name}: GET {url} -> {exc.__class__.__name__}: {exc}",
                    "is the server up, listening on 0.0.0.0, and the port open from here?")
        return []
    elapsed = time.perf_counter() - started
    if response.status_code == 401:
        report.fail(f"{name}: GET {url} -> 401",
                    f"{name.upper()}_API_KEY must equal the server's --api-key exactly")
        return []
    if response.status_code != 200:
        report.fail(f"{name}: GET {url} -> {response.status_code}: {response.text[:200]}")
        return []
    try:
        served = [m["id"] for m in response.json().get("data", [])]
    except (ValueError, KeyError, TypeError):
        report.fail(f"{name}: /models answered 200 but not in OpenAI's shape: "
                    f"{response.text[:200]}")
        return []
    report.ok(f"{name}: /models in {elapsed * 1000:.0f} ms - served: {served or '(none)'}")
    return served


# --- 3. routing ---------------------------------------------------------------


def local_routes(settings: Settings, report: Report) -> dict[str, Any]:
    """``op -> OpRoute`` for every op routed to a local provider; {} on any problem."""
    from llmwiki.llm.routing_config import load_routing_config

    try:
        routing = load_routing_config(settings.llm_providers_config, settings.llm_ops_config)
    except Exception as exc:  # noqa: BLE001 - RuntimeError, or a SyntaxError in the file
        report.fail(f"routing table does not load: {exc.__class__.__name__}: {exc}",
                    "this is the same error the service raises at startup")
        return {}
    if routing is None:
        report.skip("single-provider fallback (no config files) - nothing is routed")
        return {}
    routes = {op: r for op, r in routing.ops.items() if r.provider in LOCAL_PROVIDERS}
    if not routes:
        report.warn("no op is routed to a local provider yet - config/ops.py's "
                    "local-routing example is still commented out (checks 4-5 still "
                    "run against the served model)")
    return routes


def check_routes_served(name: str, served: list[str], routes: dict[str, Any],
                        report: Report) -> None:
    print(f"3. Routing - {name}")
    mine = {op: r for op, r in routes.items() if r.provider == name}
    if not mine:
        report.skip(f"{name}: no config/ops.py row routes to it")
    for op, route in sorted(mine.items()):
        if route.model in served:
            report.ok(f"{op} -> {name}/{route.model} (served)")
        else:
            report.fail(f"{op} -> {name}/{route.model} but the server serves {served}",
                        "config/ops.py's \"model\" must equal --served-model-name "
                        "(vLLM) / --alias (llama.cpp) exactly")


# --- 4/5. completions through the project's adapter -------------------------


def _adapter(name: str, key: str, base_url: str, model: str) -> Any:
    from llmwiki import __version__
    from llmwiki.llm import providers
    from llmwiki.llm.langchain_client import LangChainLLM

    def build(m: str, max_tokens: int, temperature: float) -> Any:
        return providers.build(name, model=m, api_key=key, base_url=base_url,
                               max_tokens=max_tokens, temperature=temperature)

    return LangChainLLM(build, default_model=model, version=__version__,
                        default_max_tokens=128, default_temperature=0.0)


def _describe(usage: Any, elapsed: float) -> str:
    if usage is None:
        return f"{elapsed * 1000:.0f} ms, no usage recorded"
    return (f"{elapsed * 1000:.0f} ms, in={usage.input_tokens} out={usage.output_tokens} "
            f"cost=${usage.cost_usd:.4f}")


def check_completion(name: str, adapter: Any, model: str, report: Report) -> None:
    print(f"4. Completion - {name}/{model}")
    started = time.perf_counter()
    try:
        response = adapter.complete(op="probe", system=PROBE_SYSTEM, prompt=PROBE_PROMPT)
    except Exception as exc:  # noqa: BLE001 - any failure is the finding
        report.fail(f"{name}: completion raised {exc.__class__.__name__}: {str(exc)[:300]}")
        return
    elapsed = time.perf_counter() - started
    usage = response.usage
    if not response.text.strip():
        report.fail(f"{name}: empty completion ({_describe(usage, elapsed)})",
                    "a reasoning model may have spent max_tokens thinking - see config/ops.py")
        return
    if usage is None or usage.input_tokens <= 0 or usage.output_tokens <= 0:
        report.fail(f"{name}: no token counts in usage_metadata ({_describe(usage, elapsed)})",
                    "the cost ledger would record zeros - check the server returns `usage`")
        return
    report.ok(f"{name}: {response.text.strip()[:60]!r} ({_describe(usage, elapsed)})")


def check_structured(name: str, adapter: Any, model: str, report: Report) -> None:
    print(f"5. Structured (forced tool call) - {name}/{model}")
    started = time.perf_counter()
    try:
        response = adapter.complete(op="probe", system=PROBE_SYSTEM, prompt=PROBE_PROMPT,
                                    schema=PROBE_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        report.fail(f"{name}: structured completion raised {exc.__class__.__name__}: "
                    f"{str(exc)[:300]}", STRUCTURED_HINT[name])
        return
    elapsed = time.perf_counter() - started
    data = response.data
    if not isinstance(data, dict) or not str(data.get("answer", "")).strip():
        report.fail(f"{name}: no structured result (data={data!r}, text={response.text[:80]!r})",
                    STRUCTURED_HINT[name])
        return
    report.ok(f"{name}: data={data} ({_describe(response.usage, elapsed)})")


# --- 6. the real routed op ---------------------------------------------------


def check_routed_op(settings: Settings, op: str, routes: dict[str, Any], report: Report) -> None:
    print(f"6. Routed op - {op}")
    route = routes.get(op)
    if route is None:
        report.fail(f"{op} is not routed to a local provider",
                    "uncomment/adjust its row in config/ops.py first (checks 3 above)")
        return
    from llmwiki import factory

    client = factory.llm_client(settings)
    started = time.perf_counter()
    try:
        response = client.complete(op=op, system=PROBE_SYSTEM, prompt=PROBE_PROMPT,
                                   schema=PROBE_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        report.fail(f"{op} via {route.provider}/{route.model} raised "
                    f"{exc.__class__.__name__}: {str(exc)[:300]}")
        return
    elapsed = time.perf_counter() - started
    usage = response.usage
    if not isinstance(response.data, dict):
        report.fail(f"{op} via {route.provider}/{route.model}: no structured result "
                    f"(text={response.text[:80]!r})", STRUCTURED_HINT[route.provider])
        return
    if usage is None or usage.model != route.model or usage.op != op:
        report.fail(f"{op}: CostRecord does not name the route "
                    f"(usage={usage!r}, expected op={op} model={route.model})")
        return
    if usage.cost_usd != 0.0:
        report.warn(f"{op}: cost_usd={usage.cost_usd} - a local model is priced in "
                    "llm/pricing.py? expected 0.0")
    report.ok(f"{op} -> {route.provider}/{route.model}: data={response.data} "
              f"({_describe(usage, elapsed)})")


# --- main -----------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", choices=LOCAL_PROVIDERS, default=None,
                    help="check only this local provider (default: every active one)")
    ap.add_argument("--quick", action="store_true", help="checks 1-3 only, no tokens")
    ap.add_argument("--model", default=None,
                    help="model for checks 4-5 (default: the routed one, else the first served)")
    ap.add_argument("--op", action="append", default=[],
                    help="also run check 6 for this op (repeatable)")
    args = ap.parse_args()

    settings = load_settings()
    report = Report()
    print(f"llm-wiki-svc local-LLM check | providers={settings.llm_providers_config} "
          f"ops={settings.llm_ops_config}\n")

    active = check_config(settings, report, args.provider)
    routes = local_routes(settings, report)

    for name, (key, base_url) in active.items():
        served = check_reachable(name, key, base_url, report)
        if not served:
            continue
        check_routes_served(name, served, routes, report)
        if args.quick:
            continue
        routed_models = [r.model for r in routes.values() if r.provider == name]
        model = args.model or (routed_models[0] if routed_models else served[0])
        if model not in served:
            report.fail(f"{name}: model {model!r} is not served; skipping checks 4-5",
                        f"pass --model with one of {served}")
            continue
        adapter = _adapter(name, key, base_url, model)
        check_completion(name, adapter, model, report)
        check_structured(name, adapter, model, report)

    if args.op and not args.quick:
        for op in args.op:
            check_routed_op(settings, op, routes, report)

    print()
    if report.failed:
        print("Some checks FAILED - see the -> hints above.")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
