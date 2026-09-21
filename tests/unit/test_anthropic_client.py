"""AnthropicLLM's tracing wrap - no network, no real Anthropic or LangSmith call.

``langsmith`` is a dev dependency (see pyproject.toml) purely so this test can
monkeypatch ``wrap_anthropic``; the point being tested is that AnthropicLLM
imports it *only* when tracing is requested.
"""

from __future__ import annotations

import pytest

from llmwiki.llm.anthropic_client import AnthropicLLM


def test_tracing_off_never_imports_langsmith(monkeypatch) -> None:
    """The default path must not need langsmith installed at all."""
    import sys

    monkeypatch.delitem(sys.modules, "langsmith", raising=False)
    monkeypatch.delitem(sys.modules, "langsmith.wrappers", raising=False)

    llm = AnthropicLLM(api_key="sk-ant-fake", default_model="claude-haiku-4-5")

    assert "langsmith" not in sys.modules
    assert llm._client is not None


def test_tracing_on_wraps_the_client(monkeypatch) -> None:
    sentinel = object()
    calls = []

    def fake_wrap_anthropic(client):
        calls.append(client)
        return sentinel

    monkeypatch.setattr(
        "langsmith.wrappers.wrap_anthropic", fake_wrap_anthropic, raising=False
    )

    llm = AnthropicLLM(api_key="sk-ant-fake", default_model="claude-haiku-4-5", tracing=True)

    assert llm._client is sentinel
    assert len(calls) == 1


def test_tracing_on_without_langsmith_installed_raises_a_clear_error(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "langsmith.wrappers" or name.startswith("langsmith"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(RuntimeError) as excinfo:
        AnthropicLLM(api_key="sk-ant-fake", default_model="claude-haiku-4-5", tracing=True)
    assert "llmwiki[langsmith]" in str(excinfo.value)


# --- max_tokens / temperature: forwarded verbatim into the request dict ----


class _StubMessages:
    def __init__(self) -> None:
        self.last_request: dict | None = None

    def create(self, **request):
        self.last_request = request
        from types import SimpleNamespace

        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


def test_max_tokens_and_temperature_reach_the_request(monkeypatch) -> None:
    llm = AnthropicLLM(api_key="sk-ant-fake", default_model="claude-haiku-4-5")
    stub = _StubMessages()
    monkeypatch.setattr(llm._client, "messages", stub)

    llm.complete(op="answer_query", system="s", prompt="p", max_tokens=512, temperature=0.3)

    assert stub.last_request is not None
    assert stub.last_request["max_tokens"] == 512
    assert stub.last_request["temperature"] == 0.3


def test_temperature_defaults_to_one(monkeypatch) -> None:
    llm = AnthropicLLM(api_key="sk-ant-fake", default_model="claude-haiku-4-5")
    stub = _StubMessages()
    monkeypatch.setattr(llm._client, "messages", stub)

    llm.complete(op="answer_query", system="s", prompt="p")

    assert stub.last_request is not None
    assert stub.last_request["temperature"] == 1.0


def test_omitted_max_tokens_and_temperature_use_the_configured_defaults(monkeypatch) -> None:
    """implement-plan.md Part II §19.3: dropping model=/max_tokens=/temperature= from call
    sites must not silently ignore Settings.llm_max_tokens/llm_temperature - the adapter's
    own construction-time defaults are what a caller passing nothing now resolves to."""
    llm = AnthropicLLM(
        api_key="sk-ant-fake", default_model="claude-haiku-4-5",
        default_max_tokens=777, default_temperature=0.4,
    )
    stub = _StubMessages()
    monkeypatch.setattr(llm._client, "messages", stub)

    llm.complete(op="answer_query", system="s", prompt="p")

    assert stub.last_request is not None
    assert stub.last_request["max_tokens"] == 777
    assert stub.last_request["temperature"] == 0.4

    # An explicit call-site value still overrides the construction-time default.
    llm.complete(op="answer_query", system="s", prompt="p", max_tokens=50, temperature=0.9)
    assert stub.last_request["max_tokens"] == 50
    assert stub.last_request["temperature"] == 0.9
