"""Anthropic adapter: model routing, prompt caching, retries, cost accounting.

Every Anthropic call in the project goes through here, using the official
``anthropic`` SDK - never raw HTTP to the Messages API.

Two details matter for cost:

* The **system** block carries the stable prefix (role instructions, output
  contract) and is marked with ``cache_control``.  Volatile per-source content
  goes in the user message, after the last breakpoint, or the cache never hits.
* Structured output is done with a forced tool call rather than "reply with
  JSON", which removes the parse-failure retry loop entirely.

``tracing=True`` (``LANGSMITH_TRACING``, factory.py) wraps the client with
``langsmith.wrappers.wrap_anthropic`` so every call also reaches LangSmith.
``langsmith`` is imported only in that branch - tracing off (the default)
costs nothing and needs nothing installed.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from llmwiki.llm import pricing
from llmwiki.llm.base import LLMResponse
from llmwiki.models.plan import CostRecord
from llmwiki.models.source import ImageInput

MAX_ATTEMPTS = 4
CACHE_MIN_CHARS = 2000  # below roughly this, a cache breakpoint costs more than it saves


def price(model: str, usage: CostRecord) -> float:
    """Measured cost of one Anthropic call in USD.

    Rates live in :mod:`llmwiki.llm.pricing`, shared with the LangChain
    adapter.  An unknown id falls back to Haiku rates here - unlike the generic
    path, which records 0.0 - because everything reaching this adapter is an
    Anthropic model and those rates are the right order of magnitude.
    """
    return pricing.price(model, usage, fallback="claude-haiku-4-5")


def _wrap_for_tracing(client: Any) -> Any:
    """Wrap an Anthropic client so every call is also sent to LangSmith.

    Separate function so the import failure path names the extra to install,
    same as ``cfg.require`` does for a missing credential.
    """
    try:
        from langsmith.wrappers import wrap_anthropic
    except ImportError as exc:
        raise RuntimeError(
            "LANGSMITH_TRACING is set but the `langsmith` package is not installed. "
            'Run `pip install "llmwiki[langsmith]"`.'
        ) from exc
    return wrap_anthropic(client)


class AnthropicLLM:
    """LLMClient backed by the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
        version: str = "",
        tracing: bool = False,
        default_max_tokens: int = 2048,
        default_temperature: float = 1.0,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        # ``base_url`` covers gateways and proxies (LLM_BASE_URL).  Passed only
        # when set, so the SDK keeps its own default otherwise.
        options: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
        if base_url:
            options["base_url"] = base_url
        client = anthropic.Anthropic(**options)
        if tracing:
            client = _wrap_for_tracing(client)
        self._client = client
        self.default_model = default_model
        self.version = version
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature

    def describe(
        self,
        *,
        op: str,
        system: str,
        prompt: str,
        images: list[ImageInput],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Vision form of :meth:`complete` (Phase 2, plan §21.2 D1): image blocks, then the text."""
        content: list[dict[str, Any]] = [
            {"type": "image",
             "source": {"type": "base64", "media_type": image.media_type,
                        "data": base64.b64encode(image.data).decode("ascii")}}
            for image in images
        ]
        content.append({"type": "text", "text": prompt})
        return self.complete(op=op, system=system, prompt=content, model=model,  # type: ignore[arg-type]
                             max_tokens=max_tokens, temperature=temperature)

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
        chosen = model or self.default_model
        resolved_max_tokens = self.default_max_tokens if max_tokens is None else max_tokens
        resolved_temperature = self.default_temperature if temperature is None else temperature
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if len(system) >= CACHE_MIN_CHARS:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        request: dict[str, Any] = {
            "model": chosen,
            "max_tokens": resolved_max_tokens,
            "temperature": resolved_temperature,
            "system": system_blocks,
            "messages": [{"role": "user", "content": prompt}],
        }
        if schema is not None:
            request["tools"] = [
                {
                    "name": "emit",
                    "description": "Return the result in the required structure.",
                    "input_schema": schema,
                }
            ]
            request["tool_choice"] = {"type": "tool", "name": "emit"}

        message = self._call_with_retries(request)
        usage = self._usage(op, chosen, message)

        data: dict | None = None
        text_parts: list[str] = []
        for block in message.content:
            if block.type == "tool_use":
                data = dict(block.input)
            elif block.type == "text":
                text_parts.append(block.text)
        text = "\n".join(text_parts)
        if data is None and schema is not None and text:
            # Defensive: a model that answered in prose despite tool_choice.
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        return LLMResponse(text=text or (json.dumps(data) if data else ""), data=data, usage=usage)

    def _call_with_retries(self, request: dict) -> Any:
        """Retry transient failures only; a bad request is a bug, not a blip."""
        delay = 1.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._client.messages.create(**request)
            except (self._anthropic.RateLimitError, self._anthropic.APIConnectionError):
                if attempt == MAX_ATTEMPTS:
                    raise
                time.sleep(delay)
                delay *= 2
            except self._anthropic.APIStatusError as exc:
                if exc.status_code >= 500 and attempt < MAX_ATTEMPTS:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError("unreachable")

    def _usage(self, op: str, model: str, message: Any) -> CostRecord:
        raw = message.usage
        record = CostRecord(
            op=op,
            model=model,
            input_tokens=getattr(raw, "input_tokens", 0) or 0,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
            version=self.version,
        )
        record.cost_usd = price(model, record)
        return record
