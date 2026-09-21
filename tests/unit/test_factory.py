"""factory.py logs which adapter it actually built - the answer to "which LLM
client is this ingest/compile using".
"""

from __future__ import annotations

import logging

from llmwiki import factory
from llmwiki.config import Settings
from llmwiki.llm import routing_config


def test_llm_client_build_logs_the_provider_and_model(caplog) -> None:
    cfg = Settings(_env_file=None, llm_provider="fake")
    factory.reset()
    with caplog.at_level(logging.INFO, logger="llmwiki.factory"):
        factory.llm_client(cfg)

    assert any("provider=fake" in record.message for record in caplog.records)
    factory.reset()


def test_anthropic_client_build_logs_its_model(caplog) -> None:
    cfg = Settings(_env_file=None, llm_provider="anthropic", llm_api_key="sk-ant-fake",
                    llm_model="claude-sonnet-5", langsmith_tracing=False)
    factory.reset()
    with caplog.at_level(logging.INFO, logger="llmwiki.factory"):
        factory.llm_client(cfg)

    messages = [record.message for record in caplog.records]
    assert any("provider=anthropic" in m and "claude-sonnet-5" in m for m in messages)
    factory.reset()


# --- routed mode: config/providers.py + config/ops.py present -------------
# implement-plan.md Part II §19.2-19.3, §19.7.3.


def _ops_rows(provider: str = "fake") -> str:
    rows = ", ".join(
        f'{{"op": {op!r}, "provider": {provider!r}, "model": "m"}}'
        for op in sorted(routing_config.KNOWN_OPS)
    )
    return f"OPS = [{rows}]\n"


def test_routed_mode_builds_a_routing_client_and_answers(tmp_path) -> None:
    (tmp_path / "providers.py").write_text('PROVIDERS = [{"provider": "fake"}]\n', encoding="utf-8")
    (tmp_path / "ops.py").write_text(_ops_rows(), encoding="utf-8")

    cfg = Settings(
        _env_file=None,
        llm_providers_config=tmp_path / "providers.py",
        llm_ops_config=tmp_path / "ops.py",
    )
    factory.reset()
    client = factory.llm_client(cfg)

    from llmwiki.llm.router import RoutingLLMClient

    assert isinstance(client, RoutingLLMClient)
    response = client.complete(op="answer_query", system="s", prompt="p")
    assert response.text
    factory.reset()


def test_an_active_but_unused_provider_is_never_constructed(tmp_path, monkeypatch) -> None:
    """Only providers an OPS row actually names get built (§19.2 step 4)."""
    monkeypatch.setenv("LLMWIKI_TEST_UNUSED_KEY", "unused")
    (tmp_path / "providers.py").write_text(
        'PROVIDERS = [{"provider": "fake"}, '
        '{"provider": "openai", "api_key_env": "LLMWIKI_TEST_UNUSED_KEY"}]\n',
        encoding="utf-8",
    )
    (tmp_path / "ops.py").write_text(_ops_rows(), encoding="utf-8")  # every op routes to "fake"

    cfg = Settings(
        _env_file=None,
        llm_providers_config=tmp_path / "providers.py",
        llm_ops_config=tmp_path / "ops.py",
    )
    factory.reset()

    built: list[str] = []
    real = factory._construct_provider_client

    def spy(provider, **kwargs):
        built.append(provider)
        return real(provider, **kwargs)

    monkeypatch.setattr(factory, "_construct_provider_client", spy)
    factory.llm_client(cfg)

    assert built == ["fake"]
    factory.reset()


def test_absent_routing_config_leaves_the_fallback_path_untouched(tmp_path) -> None:
    """The default paths point at a config/ that does not exist here - fallback applies."""
    cfg = Settings(_env_file=None, llm_provider="fake",
                    llm_providers_config=tmp_path / "providers.py",
                    llm_ops_config=tmp_path / "ops.py")
    factory.reset()
    client = factory.llm_client(cfg)

    from llmwiki.llm.fake import FakeLLM
    from llmwiki.llm.router import RoutingLLMClient

    assert isinstance(client, FakeLLM)
    assert not isinstance(client, RoutingLLMClient)
    factory.reset()
