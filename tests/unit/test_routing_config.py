"""config/providers.py + config/ops.py loading and validation.

design v1.4 §4.8.1, implement-plan-v1.4.md §19.2/§19.7.3. Every fixture writes
its own throwaway ``providers.py``/``ops.py`` under ``tmp_path`` - the real
repository has no ``config/`` directory, which is exactly what keeps every
other test in the suite on the single-provider fallback path untouched by any
of this.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from llmwiki.llm import routing_config
from llmwiki.llm.routing_config import KNOWN_OPS, load_routing_config

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "llmwiki"
CONFIG = REPO_ROOT / "config"


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _ops_rows(provider: str = "fake", **row_overrides: str) -> str:
    extra = "".join(f', "{k}": {v!r}' for k, v in row_overrides.items())
    rows = ", ".join(
        f'{{"op": {op!r}, "provider": {provider!r}, "model": "m"{extra}}}'
        for op in sorted(KNOWN_OPS)
    )
    return f"OPS = [{rows}]\n"


def test_absent_providers_file_is_fallback_mode(tmp_path) -> None:
    assert load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py") is None


def test_half_present_config_fails_loudly(tmp_path) -> None:
    _write(tmp_path / "providers.py", "PROVIDERS = []\n")
    with pytest.raises(RuntimeError, match="both files are required together"):
        load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")


def test_a_full_valid_config_resolves_every_known_op(tmp_path) -> None:
    _write(tmp_path / "providers.py", 'PROVIDERS = [{"provider": "fake"}]\n')
    _write(tmp_path / "ops.py", _ops_rows())

    routing = load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")

    assert routing is not None
    assert set(routing.ops) == KNOWN_OPS
    assert routing.providers_in_use() == {"fake"}
    for route in routing.ops.values():
        assert route.provider == "fake"
        assert route.model == "m"


def test_ops_row_defaults_temperature_and_max_tokens(tmp_path) -> None:
    _write(tmp_path / "providers.py", 'PROVIDERS = [{"provider": "fake"}]\n')
    _write(tmp_path / "ops.py", _ops_rows())  # no temperature/max_tokens given

    routing = load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")

    assert routing is not None
    for route in routing.ops.values():
        assert route.temperature == routing_config.DEFAULT_TEMPERATURE
        assert route.max_tokens == routing_config.DEFAULT_MAX_TOKENS


def test_inactive_provider_is_dropped_not_an_error(tmp_path, monkeypatch) -> None:
    """An unset credential env var excludes the provider - it is not a startup error."""
    monkeypatch.delenv("LLMWIKI_TEST_UNSET_KEY", raising=False)
    _write(
        tmp_path / "providers.py",
        'PROVIDERS = [{"provider": "openai", "api_key_env": "LLMWIKI_TEST_UNSET_KEY"}, '
        '{"provider": "fake"}]\n',
    )
    _write(tmp_path / "ops.py", _ops_rows())

    routing = load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")

    assert routing is not None
    assert "openai" not in routing.providers
    assert routing.providers_in_use() == {"fake"}


def test_credentials_are_resolved_from_dotenv_when_not_a_real_env_var(
    tmp_path, monkeypatch
) -> None:
    """The real bug: .env is loaded by Settings into its own fields only, never
    exported to os.environ (no load_dotenv() call anywhere in this codebase) -
    so a provider's api_key_env must also be resolvable straight from .env."""
    monkeypatch.delenv("LLMWIKI_TEST_DOTENV_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLMWIKI_TEST_DOTENV_KEY=from-dotenv\n", encoding="utf-8")
    _write(tmp_path / "providers.py",
           'PROVIDERS = [{"provider": "openai", "api_key_env": "LLMWIKI_TEST_DOTENV_KEY"}]\n')
    _write(tmp_path / "ops.py", _ops_rows(provider="openai"))

    routing = load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")

    assert routing is not None
    assert routing.providers["openai"].api_key == "from-dotenv"


def test_a_real_env_var_wins_over_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLMWIKI_TEST_DOTENV_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("LLMWIKI_TEST_DOTENV_KEY", "from-real-env")
    _write(tmp_path / "providers.py",
           'PROVIDERS = [{"provider": "openai", "api_key_env": "LLMWIKI_TEST_DOTENV_KEY"}]\n')
    _write(tmp_path / "ops.py", _ops_rows(provider="openai"))

    routing = load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")

    assert routing is not None
    assert routing.providers["openai"].api_key == "from-real-env"


def test_credentials_are_resolved_from_the_named_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLMWIKI_TEST_KEY", "secret-value")
    monkeypatch.setenv("LLMWIKI_TEST_BASE", "https://example.test")
    _write(
        tmp_path / "providers.py",
        'PROVIDERS = [{"provider": "openai", "api_key_env": "LLMWIKI_TEST_KEY", '
        '"base_url_env": "LLMWIKI_TEST_BASE"}]\n',
    )
    _write(tmp_path / "ops.py", _ops_rows(provider="openai"))

    routing = load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")

    assert routing is not None
    creds = routing.providers["openai"]
    assert creds.api_key == "secret-value"
    assert creds.base_url == "https://example.test"


def test_missing_known_op_fails_loudly(tmp_path) -> None:
    _write(tmp_path / "providers.py", 'PROVIDERS = [{"provider": "fake"}]\n')
    rows = ", ".join(
        f'{{"op": {op!r}, "provider": "fake", "model": "m"}}'
        for op in sorted(KNOWN_OPS)
        if op != "answer_query"
    )
    _write(tmp_path / "ops.py", f"OPS = [{rows}]\n")

    with pytest.raises(RuntimeError, match="answer_query"):
        load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")


def test_duplicate_op_fails_loudly(tmp_path) -> None:
    _write(tmp_path / "providers.py", 'PROVIDERS = [{"provider": "fake"}]\n')
    _write(
        tmp_path / "ops.py",
        'OPS = [{"op": "answer_query", "provider": "fake", "model": "m"}, '
        '{"op": "answer_query", "provider": "fake", "model": "m"}]\n',
    )

    with pytest.raises(RuntimeError, match="duplicate op"):
        load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")


def test_op_naming_an_inactive_provider_fails_loudly(tmp_path) -> None:
    _write(tmp_path / "providers.py", 'PROVIDERS = [{"provider": "fake"}]\n')
    _write(tmp_path / "ops.py", _ops_rows(provider="openai"))  # never declared active

    with pytest.raises(RuntimeError, match="not active"):
        load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")


def test_unknown_provider_kind_fails_loudly(tmp_path) -> None:
    _write(tmp_path / "providers.py", 'PROVIDERS = [{"provider": "not-a-real-provider"}]\n')
    _write(tmp_path / "ops.py", _ops_rows())

    with pytest.raises(RuntimeError, match="has no adapter"):
        load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")


def test_malformed_providers_module_names_the_file(tmp_path) -> None:
    _write(tmp_path / "providers.py", "PROVIDERS = 'not-a-list'\n")
    _write(tmp_path / "ops.py", _ops_rows())

    with pytest.raises(RuntimeError, match="PROVIDERS"):
        load_routing_config(tmp_path / "providers.py", tmp_path / "ops.py")


# --- the tracked config/providers.py + config/ops.py, not a tmp_path fixture:
#     the 2026-09-14 provider split's actual point (every other test in this
#     file writes its own throwaway files and never touches these real ones).


def test_the_tracked_providers_config_gives_vllm_and_llamacpp_their_own_env_vars(
    tmp_path, monkeypatch
) -> None:
    """vLLM, llama.cpp, and real cloud OpenAI can all be active at once.

    Before 2026-09-14, "vllm"/"llamacpp" didn't exist as registry entries -
    the only local-LLM mechanism was pointing the shared "openai" row's
    OPENAI_BASE_URL at whichever self-hosted server was running, which meant
    a local endpoint and a real cloud OpenAI key could never both be active.

    ``config/providers.py`` is the real, tracked file - that's the point being
    guarded. ``config/ops.py`` is *not* used real here: the real one doesn't
    route anything to vllm/llamacpp yet (its local-routing example is still
    commented out - no endpoint is reachable), and ``RoutingConfig.providers``
    only keeps providers an op actually names (``load_routing_config``'s
    ``used`` filter) - so a throwaway ops.py that routes to all three is what
    it takes to observe them resolved side by side.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-cloud")
    monkeypatch.setenv("VLLM_API_KEY", "sk-vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.local/v1")
    monkeypatch.setenv("LLAMACPP_API_KEY", "sk-llamacpp")
    monkeypatch.setenv("LLAMACPP_BASE_URL", "http://llamacpp.local/v1")
    _write(
        tmp_path / "ops.py",
        'OPS = ['
        '{"op": "summarize_source", "provider": "vllm", "model": "m"}, '
        '{"op": "plan_compile", "provider": "llamacpp", "model": "m"}, '
        '{"op": "create_page", "provider": "openai", "model": "m"}, '
        '{"op": "patch_page", "provider": "openai", "model": "m"}, '
        '{"op": "answer_query", "provider": "openai", "model": "m"}, '
        '{"op": "agent_step", "provider": "openai", "model": "m"}, '
        '{"op": "judge_answer", "provider": "openai", "model": "m"}]\n',
    )

    routing = load_routing_config(CONFIG / "providers.py", tmp_path / "ops.py")

    assert routing is not None
    assert {"openai", "vllm", "llamacpp"} <= set(routing.providers)
    assert routing.providers["vllm"].base_url == "http://vllm.local/v1"
    assert routing.providers["llamacpp"].base_url == "http://llamacpp.local/v1"
    assert routing.providers["vllm"].api_key != routing.providers["llamacpp"].api_key
    assert routing.providers["vllm"].api_key != routing.providers["openai"].api_key


# --- drift guard: KNOWN_OPS must equal what the codebase actually calls ----


def _ops_called_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "complete"):
            continue
        for kw in node.keywords:
            if kw.arg == "op" and isinstance(kw.value, ast.Constant):
                found.add(kw.value.value)
    return found


def test_known_ops_matches_every_real_call_site() -> None:
    """Breaks the moment a call site adds an op with no config/ops.py row."""
    called = _ops_called_in(SRC / "agent" / "query.py")
    called |= _ops_called_in(SRC / "wiki" / "compiler.py")
    # Phase 1-D: the query graph's tool-decision call and the eval judge.
    called |= _ops_called_in(SRC / "agent" / "graph.py")
    called |= _ops_called_in(SRC / "agent" / "judge.py")
    assert called == KNOWN_OPS
