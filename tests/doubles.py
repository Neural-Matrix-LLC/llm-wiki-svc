"""Test doubles: a spy object store and a scripted LLM.

The spy is what makes the design's central constraint testable - it records
which keys were read and which prefixes were listed, so a test can assert that
compiling one source did not walk the wiki.
"""

from __future__ import annotations

from llmwiki.llm.base import LLMResponse
from llmwiki.models.plan import CostRecord
from llmwiki.storage.base import ObjectStore


class SpyObjectStore:
    """Wraps a real store and records every operation."""

    def __init__(self, inner: ObjectStore) -> None:
        self.inner = inner
        self.get_keys: list[str] = []
        self.put_keys: list[str] = []
        self.list_prefixes: list[str] = []
        self.exists_keys: list[str] = []

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.put_keys.append(key)
        self.inner.put(key, data, content_type)

    def get(self, key: str) -> bytes:
        self.get_keys.append(key)
        return self.inner.get(key)

    def exists(self, key: str) -> bool:
        self.exists_keys.append(key)
        return self.inner.exists(key)

    def list(self, prefix: str) -> list[str]:
        self.list_prefixes.append(prefix)
        return self.inner.list(prefix)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    # -- assertions helpers -------------------------------------------------

    def gets(self, prefix: str) -> list[str]:
        """Every read whose key starts with ``prefix``, including repeats."""
        return [key for key in self.get_keys if key.startswith(prefix)]

    def distinct_gets(self, prefix: str) -> set[str]:
        """The distinct keys read under ``prefix``.

        This is the measure that matters for the no-full-scan guard: reading one
        page twice (the compiler reads a body, then re-reads it for the optimistic
        version check) is not a scan; reading many *different* pages is.
        """
        return {key for key in self.get_keys if key.startswith(prefix)}

    def lists(self, prefix: str) -> list[str]:
        """Every prefix listing that fell under ``prefix``."""
        return [seen for seen in self.list_prefixes if seen.startswith(prefix)]

    def reset(self) -> None:
        self.get_keys.clear()
        self.put_keys.clear()
        self.list_prefixes.clear()
        self.exists_keys.clear()


class ScriptedLLM:
    """Returns pre-set responses per op and records what it was asked."""

    def __init__(self, script: dict[str, dict]) -> None:
        self.script = script
        self.calls: list[dict] = []

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
        # Recorded as passed - None included - so a test can assert a call
        # site left these to be resolved by whichever adapter is really
        # configured (plan §19.3), rather than always seeing a concrete number.
        self.calls.append({
            "op": op, "system": system, "prompt": prompt, "model": model,
            "max_tokens": max_tokens, "temperature": temperature, "schema": schema,
        })
        data = self.script.get(op, {})
        return LLMResponse(
            text=data.get("text", ""),
            data=data,
            usage=CostRecord(op=op, model=model or "scripted", input_tokens=len(prompt) // 4,
                             output_tokens=32),
        )

    def prompts_for(self, op: str) -> list[str]:
        """Every prompt sent for one op - used to assert prompt size stays bounded."""
        return [call["prompt"] for call in self.calls if call["op"] == op]
