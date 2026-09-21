"""RoutingLLMClient dispatch - implement-plan.md Part II §19.3/§19.7.3."""

from __future__ import annotations

from llmwiki.llm.fake import FakeLLM
from llmwiki.llm.router import RoutingLLMClient
from llmwiki.llm.routing_config import OpRoute, ProviderCreds, RoutingConfig


def _routing() -> RoutingConfig:
    return RoutingConfig(
        providers={
            "fake-a": ProviderCreds(provider="fake-a", api_key="", base_url=""),
            "fake-b": ProviderCreds(provider="fake-b", api_key="", base_url=""),
        },
        ops={
            "answer_query": OpRoute(
                op="answer_query", provider="fake-a", model="model-a",
                temperature=0.5, max_tokens=111,
            ),
            "plan_compile": OpRoute(
                op="plan_compile", provider="fake-b", model="model-b",
                temperature=0.9, max_tokens=222,
            ),
        },
    )


def test_dispatches_to_the_configured_providers_client() -> None:
    client_a, client_b = FakeLLM(), FakeLLM()
    router = RoutingLLMClient(_routing(), {"fake-a": client_a, "fake-b": client_b})

    router.complete(op="answer_query", system="s", prompt="p")
    router.complete(op="plan_compile", system="s", prompt="p")

    assert len(client_a.calls) == 1 and len(client_b.calls) == 1
    assert client_a.calls[0]["model"] == "model-a"
    assert client_b.calls[0]["model"] == "model-b"


def test_op_row_supplies_model_temperature_and_max_tokens() -> None:
    client_a = FakeLLM()
    router = RoutingLLMClient(_routing(), {"fake-a": client_a, "fake-b": FakeLLM()})

    router.complete(op="answer_query", system="s", prompt="p")

    call = client_a.calls[0]
    assert (call["model"], call["temperature"], call["max_tokens"]) == ("model-a", 0.5, 111)


def test_explicit_call_site_overrides_win() -> None:
    client_a = FakeLLM()
    router = RoutingLLMClient(_routing(), {"fake-a": client_a, "fake-b": FakeLLM()})

    router.complete(
        op="answer_query", system="s", prompt="p",
        model="override-model", max_tokens=999, temperature=0.0,
    )

    call = client_a.calls[0]
    assert (call["model"], call["temperature"], call["max_tokens"]) == ("override-model", 0.0, 999)


def test_schema_is_forwarded_to_the_underlying_client() -> None:
    client_a = FakeLLM()
    router = RoutingLLMClient(_routing(), {"fake-a": client_a, "fake-b": FakeLLM()})
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}

    response = router.complete(op="answer_query", system="s", prompt="p", schema=schema)

    assert response.data is not None
