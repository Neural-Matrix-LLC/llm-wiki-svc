"""Settings load, and a misconfigured backend fails by name rather than at the socket."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from llmwiki.config import Settings


def test_defaults_are_the_cloud_backends() -> None:
    cfg = Settings(_env_file=None)
    assert (cfg.storage_backend, cfg.vector_backend) == ("r2", "vectorize")
    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_model == "claude-haiku-4-5"
    assert cfg.llm_default_model == "claude-haiku-4-5"
    assert cfg.llm_max_tokens == 2048
    assert cfg.llm_temperature == 1.0
    assert cfg.log_level == "INFO"


def test_secrets_do_not_appear_in_repr() -> None:
    cfg = Settings(_env_file=None, anthropic_api_key="sk-ant-secret-value")
    assert "sk-ant-secret-value" not in repr(cfg)
    assert cfg.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"


def test_require_names_the_missing_variables() -> None:
    cfg = Settings(_env_file=None, r2_access_key_id="", r2_endpoint_url="")
    with pytest.raises(RuntimeError) as excinfo:
        cfg.require("r2_access_key_id", "r2_endpoint_url")
    message = str(excinfo.value)
    assert "R2_ACCESS_KEY_ID" in message and "R2_ENDPOINT_URL" in message


def test_require_rejects_the_placeholder_value() -> None:
    """`changeme` from .env.example is not a credential."""
    cfg = Settings(_env_file=None, cf_account_id="changeme")
    with pytest.raises(RuntimeError):
        cfg.require("cf_account_id")


def test_offline_settings_need_no_credentials() -> None:
    cfg = Settings(_env_file=None, storage_backend="local", vector_backend="memory",
                   embedding_backend="fake", llm_backend="fake")
    assert cfg.storage_backend == "local"


# --- the provider-generic LLM contract (plan-v1.4 7.6) --------------------
#
# LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_BASE_URL are the names the
# shared agentkit-llm layer will own, so both spellings must agree until the
# deprecated aliases are removed at N4.  These tests are what make that claim
# checkable rather than a comment in .env.example.


def test_generic_names_are_the_canonical_spelling() -> None:
    cfg = Settings(_env_file=None, llm_provider="fake", llm_model="claude-sonnet-5",
                   llm_api_key="sk-generic", llm_base_url="https://gateway.example/v1")
    assert cfg.llm_provider == "fake"
    assert cfg.llm_model == "claude-sonnet-5"
    assert cfg.llm_base_url == "https://gateway.example/v1"
    assert cfg.llm_api_key.get_secret_value() == "sk-generic"


def test_deprecated_aliases_still_configure_the_generic_fields() -> None:
    """A pre-rename .env or test fixture keeps working, unchanged."""
    cfg = Settings(_env_file=None, llm_backend="fake", llm_default_model="claude-opus-5",
                   anthropic_api_key="sk-ant-old")
    assert cfg.llm_provider == "fake"
    assert cfg.llm_model == "claude-opus-5"
    assert cfg.llm_api_key.get_secret_value() == "sk-ant-old"


def test_both_spellings_read_the_same_value_after_resolution() -> None:
    """Call sites written against either name see the truth, not a stale default."""
    cfg = Settings(_env_file=None, llm_provider="fake", llm_model="claude-sonnet-5",
                   llm_api_key="sk-generic")
    assert cfg.llm_backend == cfg.llm_provider
    assert cfg.llm_default_model == cfg.llm_model
    assert cfg.anthropic_api_key is not None
    assert cfg.anthropic_api_key.get_secret_value() == cfg.llm_api_key.get_secret_value()


def test_the_generic_name_wins_over_its_deprecated_alias() -> None:
    cfg = Settings(_env_file=None, llm_provider="fake", llm_backend="anthropic",
                   llm_model="claude-sonnet-5", llm_default_model="claude-haiku-4-5")
    assert cfg.llm_provider == "fake"
    assert cfg.llm_model == "claude-sonnet-5"


def test_generic_api_key_does_not_appear_in_repr() -> None:
    cfg = Settings(_env_file=None, llm_api_key="sk-generic-secret")
    assert "sk-generic-secret" not in repr(cfg)


def test_every_provider_the_config_accepts_can_actually_be_built() -> None:
    """The Literal and the registry must not drift apart.

    Replaced the older milestone-message test: openai and google are no longer
    unimplemented, so an accepted value that the factory cannot route is now a
    bug rather than a documented gap.
    """
    from typing import get_args

    from llmwiki.config import Provider
    from llmwiki.llm import providers

    routed = {"anthropic", "fake"} | set(providers.REGISTRY)
    assert set(get_args(Provider)) == routed


def test_missing_llm_key_is_reported_by_its_generic_name() -> None:
    from llmwiki import factory

    cfg = Settings(_env_file=None, llm_provider="anthropic", llm_api_key="")
    factory.reset()
    with pytest.raises(RuntimeError) as excinfo:
        factory.llm_client(cfg)
    assert "LLM_API_KEY" in str(excinfo.value)
    factory.reset()


# --- application-specific LLM routing (design v1.4 4.8, plan 19) ----------
#
# The paths default to a config/ directory relative to the process cwd. A
# developer may have set up a real one in this checkout for actual use (R1) -
# conftest.py's autouse _isolate_llm_routing_config fixture is what keeps the
# rest of this suite on the fallback path regardless; the test below verifies
# the true unmodified default directly, bypassing that guard on purpose.


def test_routing_config_paths_default_under_a_config_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LLMWIKI_PROVIDERS_CONFIG", raising=False)
    monkeypatch.delenv("LLMWIKI_OPS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # an empty directory - proves the default, not this repo's state

    cfg = Settings(_env_file=None)

    assert cfg.llm_providers_config == Path("config/providers.py")
    assert cfg.llm_ops_config == Path("config/ops.py")
    assert not cfg.llm_providers_config.exists()
    assert not cfg.llm_ops_config.exists()


def test_agent_skills_dir_defaults_outside_src(monkeypatch) -> None:
    """R5 landed: query-agent skill discovery (agent/skills.py) reads this. Bypasses
    conftest.py's autouse _isolate_agent_skills_dir on purpose, same reasoning as
    test_routing_config_paths_default_under_a_config_directory above - that fixture exists
    to keep the *rest* of the suite off this repository's real skills/ directory, not to
    hide the true default from the one test that verifies it."""
    monkeypatch.delenv("AGENT_SKILLS_DIR", raising=False)
    cfg = Settings(_env_file=None)
    assert cfg.agent_skills_dir == Path("skills")


def test_compile_executor_model_setting_no_longer_exists() -> None:
    """Retired: create_page/patch_page get their own config/ops.py rows instead."""
    cfg = Settings(_env_file=None)
    assert not hasattr(cfg, "compile_executor_model")


def test_documented_env_var_names_actually_configure_the_routing_paths(monkeypatch) -> None:
    """Regression: the auto-derived name (LLM_PROVIDERS_CONFIG, no "WIKI") is not
    what .env.example and implement-plan-v1.4.md §19.8 document - without an
    explicit validation_alias, LLMWIKI_PROVIDERS_CONFIG/LLMWIKI_OPS_CONFIG were
    silently ignored and the default path was used instead."""
    monkeypatch.setenv("LLMWIKI_PROVIDERS_CONFIG", "/somewhere/providers.py")
    monkeypatch.setenv("LLMWIKI_OPS_CONFIG", "/somewhere/ops.py")
    cfg = Settings(_env_file=None)
    assert cfg.llm_providers_config == Path("/somewhere/providers.py")
    assert cfg.llm_ops_config == Path("/somewhere/ops.py")


# --- LangSmith tracing: off by default, independent of LLM_PROVIDER --------


def test_langsmith_tracing_defaults_off() -> None:
    cfg = Settings(_env_file=None)
    assert cfg.langsmith_tracing is False
    assert cfg.langsmith_project == "llmwiki"


def test_langsmith_api_key_does_not_appear_in_repr() -> None:
    cfg = Settings(_env_file=None, langsmith_api_key="lsv2-secret")
    assert "lsv2-secret" not in repr(cfg)


def test_configure_langsmith_is_a_noop_when_tracing_is_off(monkeypatch) -> None:
    from llmwiki import factory

    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    cfg = Settings(_env_file=None, langsmith_tracing=False)
    factory._configure_langsmith(cfg)
    assert "LANGSMITH_TRACING" not in os.environ


def test_configure_langsmith_requires_the_api_key() -> None:
    from llmwiki import factory

    cfg = Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key="")
    with pytest.raises(RuntimeError) as excinfo:
        factory._configure_langsmith(cfg)
    assert "LANGSMITH_API_KEY" in str(excinfo.value)


def test_configure_langsmith_exports_the_env_vars(monkeypatch) -> None:
    from llmwiki import factory

    names = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    cfg = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="lsv2-secret",
        langsmith_project="my-project",
        langsmith_endpoint="https://smith.example/api",
    )
    try:
        factory._configure_langsmith(cfg)
        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGSMITH_API_KEY"] == "lsv2-secret"
        assert os.environ["LANGSMITH_PROJECT"] == "my-project"
        assert os.environ["LANGSMITH_ENDPOINT"] == "https://smith.example/api"
    finally:
        # _configure_langsmith writes os.environ directly (every LangChain
        # provider's own auto-instrumentation must see it) - monkeypatch never
        # tracked these keys, so they must be removed by hand or every later
        # test in the session sees LANGSMITH_TRACING=true.
        for name in names:
            monkeypatch.delenv(name, raising=False)
