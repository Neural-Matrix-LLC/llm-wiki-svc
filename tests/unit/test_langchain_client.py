"""The LangChain adapter, against a scripted chat model.

No provider SDK, no network: the double returns real ``AIMessage`` objects, so
tool-call parsing and ``usage_metadata`` behave exactly as in production.
"""

from __future__ import annotations

import pytest

from llmwiki.llm.langchain_client import LangChainLLM

langchain_core = pytest.importorskip("langchain_core")
from langchain_core.messages import AIMessage  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"gist": {"type": "string"}},
    "required": ["gist"],
}


class ScriptedChatModel:
    """Stands in for a BaseChatModel; records what it was asked to do."""

    def __init__(self, reply: AIMessage, model: str, max_tokens: int, temperature: float) -> None:
        self.reply = reply
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.bound: list[tuple] = []
        self.messages: list = []

    def bind_tools(self, tools: list, tool_choice: str | None = None) -> ScriptedChatModel:
        self.bound.append((tools, tool_choice))
        return self

    def invoke(self, messages: list) -> AIMessage:
        self.messages = messages
        return self.reply


def make_client(reply: AIMessage, default_model: str = "gpt-5") -> tuple:
    built: list[ScriptedChatModel] = []

    def build(model: str, max_tokens: int, temperature: float) -> ScriptedChatModel:
        chat = ScriptedChatModel(reply, model, max_tokens, temperature)
        built.append(chat)
        return chat

    return LangChainLLM(build, default_model=default_model, version="9.9.9"), built


def test_a_plain_completion_returns_its_text() -> None:
    client, built = make_client(AIMessage(content="hello there"))
    response = client.complete(op="answer_query", system="sys", prompt="user")

    assert response.text == "hello there"
    assert response.data is None
    assert built[0].model == "gpt-5"
    kinds = [type(message).__name__ for message in built[0].messages]
    assert kinds == ["SystemMessage", "HumanMessage"]


def test_a_schema_forces_a_single_tool_call_and_returns_its_arguments() -> None:
    reply = AIMessage(
        content="",
        tool_calls=[{"name": "emit", "args": {"gist": "a gist"}, "id": "1"}],
    )
    client, built = make_client(reply)
    response = client.complete(op="summarize_source", system="s", prompt="p", schema=SCHEMA)

    assert response.data == {"gist": "a gist"}
    tools, tool_choice = built[0].bound[0]
    assert tool_choice == "emit"
    assert tools[0]["function"]["parameters"] is SCHEMA


def test_prose_despite_tool_choice_is_still_parsed() -> None:
    """Not every provider honours a forced tool call; do not lose the answer."""
    client, _ = make_client(AIMessage(content='{"gist": "recovered"}'))
    response = client.complete(op="summarize_source", system="s", prompt="p", schema=SCHEMA)

    assert response.data == {"gist": "recovered"}


def test_usage_is_recorded_with_the_op_model_and_version() -> None:
    reply = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    client, _ = make_client(reply)
    usage = client.complete(op="plan_compile", system="s", prompt="p").usage

    assert usage is not None
    assert (usage.op, usage.model, usage.version) == ("plan_compile", "gpt-5", "9.9.9")
    assert (usage.input_tokens, usage.output_tokens) == (100, 20)


def test_cached_tokens_are_not_billed_twice() -> None:
    """LangChain counts cached tokens inside input_tokens; pricing does not."""
    reply = AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 10,
            "total_tokens": 1010,
            "input_token_details": {"cache_read": 600, "cache_creation": 100},
        },
    )
    client, _ = make_client(reply, default_model="claude-haiku-4-5")
    usage = client.complete(op="patch_page", system="s", prompt="p").usage

    assert usage is not None
    assert usage.input_tokens == 300
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (600, 100)


def test_an_unpriced_model_records_real_tokens_and_zero_cost() -> None:
    """Cost is measured, never estimated: no rate means no number, not a guess."""
    reply = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 500, "output_tokens": 50, "total_tokens": 550},
    )
    client, _ = make_client(reply, default_model="some-unpriced-model")
    usage = client.complete(op="answer_query", system="s", prompt="p").usage

    assert usage is not None
    assert usage.input_tokens == 500
    assert usage.cost_usd == 0.0


def test_a_priced_model_costs_what_the_table_says() -> None:
    reply = AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "total_tokens": 1_000_000,
        },
    )
    client, _ = make_client(reply, default_model="claude-haiku-4-5")
    usage = client.complete(op="answer_query", system="s", prompt="p").usage

    assert usage is not None
    assert usage.cost_usd == pytest.approx(1.0)


def test_a_per_call_model_builds_its_own_chat_model() -> None:
    """The compiler escalates patch generation to a stronger model (D5)."""
    client, built = make_client(AIMessage(content="x"))
    client.complete(op="create_page", system="s", prompt="p")
    client.complete(op="create_page", system="s", prompt="p", model="claude-sonnet-5")

    assert [chat.model for chat in built] == ["gpt-5", "claude-sonnet-5"]


def test_chat_models_are_reused_across_identical_calls() -> None:
    client, built = make_client(AIMessage(content="x"))
    client.complete(op="answer_query", system="s", prompt="p")
    client.complete(op="answer_query", system="s", prompt="q")
    client.complete(op="answer_query", system="s", prompt="q", max_tokens=99)

    assert [chat.max_tokens for chat in built] == [2048, 99]


def test_a_temperature_change_builds_its_own_chat_model() -> None:
    """temperature is baked in at construction, same as max_tokens (D5 comment)."""
    client, built = make_client(AIMessage(content="x"))
    client.complete(op="answer_query", system="s", prompt="p")
    client.complete(op="answer_query", system="s", prompt="p", temperature=0.2)
    client.complete(op="answer_query", system="s", prompt="p", temperature=0.2)

    assert [chat.temperature for chat in built] == [1.0, 0.2]


def test_omitted_max_tokens_and_temperature_use_the_configured_defaults() -> None:
    """implement-plan-v1.4.md §19.3: a caller passing neither resolves to the
    adapter's own construction-time defaults, not a hardcoded 2048/1.0."""
    built: list = []

    def build(model: str, max_tokens: int, temperature: float):
        chat = ScriptedChatModel(AIMessage(content="x"), model, max_tokens, temperature)
        built.append(chat)
        return chat

    client = LangChainLLM(build, default_model="gpt-5", default_max_tokens=777,
                           default_temperature=0.4)
    client.complete(op="answer_query", system="s", prompt="p")

    assert (built[0].max_tokens, built[0].temperature) == (777, 0.4)
