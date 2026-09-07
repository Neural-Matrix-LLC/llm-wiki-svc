"""LangChain adapter: one ``LLMClient`` over any provider LangChain can reach.

This is the "``get_client()`` may be implemented over ``get_llm()``, never the
reverse" half of implement-plan-v1.4.md 7.5.  The domain layer keeps talking to
:meth:`complete`, so the compiler and the query agent are untouched by which
provider is configured.

Two things are deliberately weaker here than in the native Anthropic adapter,
and that is the price of provider breadth:

* **No prompt caching.**  There is no portable cache-breakpoint concept, so the
  stable system prefix is re-billed on every call.
* **Cost is priced only for models in** :mod:`llmwiki.llm.pricing`.  Token
  counts are always real - they come from LangChain's ``usage_metadata`` - but
  an unpriced model records ``cost_usd = 0.0`` rather than a guess.

Structured output uses a forced tool call, exactly as the Anthropic adapter
does, so the JSON schemas in ``wiki/compiler.py`` are passed through unchanged.

Retries are left to the integration: every LangChain chat model already retries
transient failures, and stacking a second backoff loop on top only multiplies
the wait.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from llmwiki.llm import pricing
from llmwiki.llm.base import LLMResponse
from llmwiki.models.plan import CostRecord

TOOL_NAME = "emit"


def _tool_for(schema: dict) -> dict:
    """Wrap a bare JSON schema as the one tool the model is forced to call."""
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Return the result in the required structure.",
            "parameters": schema,
        },
    }


def _text_of(message: Any) -> str:
    """The plain text of a reply, whether the content is a string or blocks."""
    text = getattr(message, "text", None)
    if isinstance(text, str):  # langchain-core 1.x: a property
        return text
    if callable(text):  # older releases: a method
        text = text()
        if isinstance(text, str):
            return text
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


class LangChainLLM:
    """LLMClient backed by a LangChain ``BaseChatModel``.

    Takes a *builder* rather than a model instance: ``complete`` accepts a
    per-call ``model``, and the compiler uses that to escalate patch generation
    to a stronger model (D5).  Built models are cached per
    (model, token cap, temperature), since constructing one opens no
    connection but does re-read credentials.
    """

    def __init__(
        self,
        build: Callable[[str, int, float], Any],
        default_model: str,
        version: str = "",
        default_max_tokens: int = 2048,
        default_temperature: float = 1.0,
    ) -> None:
        self._build = build
        self._models: dict[tuple[str, int, float], Any] = {}
        self.default_model = default_model
        self.version = version
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature

    def _model_for(self, model: str, max_tokens: int, temperature: float) -> Any:
        key = (model, max_tokens, temperature)
        if key not in self._models:
            self._models[key] = self._build(model, max_tokens, temperature)
        return self._models[key]

    def complete(
        self,
        *,
        op: str,
        system: str,
        prompt: str,
        schema: dict | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        from langchain_core.messages import HumanMessage, SystemMessage

        chosen = model or self.default_model
        resolved_max_tokens = self.default_max_tokens if max_tokens is None else max_tokens
        resolved_temperature = self.default_temperature if temperature is None else temperature
        runnable: Any = self._model_for(chosen, resolved_max_tokens, resolved_temperature)
        if schema is not None:
            runnable = runnable.bind_tools([_tool_for(schema)], tool_choice=TOOL_NAME)

        message = runnable.invoke([SystemMessage(system), HumanMessage(prompt)])

        data: dict | None = None
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == TOOL_NAME:
                data = dict(call.get("args") or {})
                break

        text = _text_of(message)
        if data is None and schema is not None and text:
            # Defensive: a model that answered in prose despite tool_choice.
            import json

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            data = parsed if isinstance(parsed, dict) else None

        if not text and data is not None:
            import json

            text = json.dumps(data)
        return LLMResponse(text=text, data=data, usage=self._usage(op, chosen, message))

    def _usage(self, op: str, model: str, message: Any) -> CostRecord:
        """Map LangChain's ``usage_metadata`` onto the project's cost ledger.

        LangChain reports ``input_tokens`` as the *total* prompt size, cached
        tokens included, while :func:`pricing.price` bills cached tokens at
        their own multipliers.  Subtracting them here is what keeps a cache hit
        from being charged twice.
        """
        raw = getattr(message, "usage_metadata", None) or {}
        details = raw.get("input_token_details") or {}
        cache_read = int(details.get("cache_read", 0) or 0)
        cache_write = int(details.get("cache_creation", 0) or 0)
        total_input = int(raw.get("input_tokens", 0) or 0)

        record = CostRecord(
            op=op,
            model=model,
            input_tokens=max(total_input - cache_read - cache_write, 0),
            output_tokens=int(raw.get("output_tokens", 0) or 0),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            version=self.version,
        )
        record.cost_usd = pricing.price(model, record)
        return record
