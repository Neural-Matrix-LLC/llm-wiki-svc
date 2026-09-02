"""Settings load, and a misconfigured backend fails by name rather than at the socket."""

from __future__ import annotations

import pytest

from llmwiki.config import Settings


def test_defaults_are_the_cloud_backends() -> None:
    cfg = Settings(_env_file=None)
    assert (cfg.storage_backend, cfg.vector_backend) == ("r2", "vectorize")
    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_model == "claude-haiku-4-5"
    assert cfg.llm_default_model == "claude-haiku-4-5"


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
