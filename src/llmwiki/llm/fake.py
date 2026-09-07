"""Scripted LLM double: unit tests and the offline smoke run.

It produces structurally valid output for each op the compiler and agent issue,
derived deterministically from the prompt, so an offline run exercises the real
control flow without an API key.
"""

from __future__ import annotations

import json
import re

from llmwiki.llm.base import LLMResponse
from llmwiki.models.plan import CostRecord

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}")
_STOPWORDS = {
    "this", "that", "with", "from", "have", "which", "their", "there", "about",
    "would", "these", "those", "were", "been", "into", "than", "then", "they",
    "when", "what", "your", "will", "more", "some", "such", "only", "also",
}


class FakeLLM:
    """Deterministic responses keyed on ``op``. No network, no key, no cost."""

    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self.responses = responses or {}
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
            "max_tokens": max_tokens, "temperature": temperature,
        })
        usage = CostRecord(
            op=op,
            model=model or "fake",
            input_tokens=len(system) // 4 + len(prompt) // 4,
            output_tokens=64,
            cost_usd=0.0,
        )
        if op in self.responses:
            data = self.responses[op]
            return LLMResponse(text=json.dumps(data), data=data, usage=usage)
        data = self._synthesize(op, prompt)
        return LLMResponse(text=data.get("text", json.dumps(data)), data=data, usage=usage)

    def _synthesize(self, op: str, prompt: str) -> dict:
        terms = self._salient_terms(prompt)
        if op == "summarize_source":
            return {
                "title": terms[0].title() if terms else "Untitled Source",
                "gist": f"A source about {', '.join(terms[:3]) or 'an unspecified topic'}.",
                "summary": " ".join(prompt.split()[:80]),
                "concepts": [t.lower() for t in terms[:3]],
                "entities": [t for t in terms[3:5]],
            }
        if op == "plan_compile":
            candidates = re.findall(r"^- ([a-z0-9-]+):", prompt, flags=re.MULTILINE)
            ops = [
                {"kind": "patch_page", "slug": slug, "title": slug.replace("-", " ").title(),
                 "type": "concept", "reason": "source adds detail"}
                for slug in candidates[:2]
            ]
            for term in terms[:2]:
                slug = term.lower()
                if slug not in candidates:
                    ops.append({
                        "kind": "create_page", "slug": slug,
                        "title": term.title(), "type": "concept",
                        "reason": "new concept introduced by this source",
                    })
            return {"ops": ops[:5]}
        if op in ("create_page", "patch_page"):
            return {
                "gist": f"Notes on {terms[0] if terms else 'the topic'}.",
                "body": (
                    "## Summary\n\n"
                    f"{' '.join(prompt.split()[:60])}\n\n"
                    "## Details\n\n"
                    f"Compiled offline by the fake LLM from {len(prompt)} characters of input.\n"
                ),
            }
        if op == "answer_query":
            return {
                "text": (
                    "Offline answer assembled from the retrieved context: "
                    f"{' '.join(prompt.split()[:40])}"
                )
            }
        return {"text": ""}

    @staticmethod
    def _salient_terms(prompt: str) -> list[str]:
        seen: dict[str, int] = {}
        for match in _WORD.findall(prompt):
            word = match.lower()
            if word in _STOPWORDS:
                continue
            seen[word] = seen.get(word, 0) + 1
        ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
        return [word for word, _ in ranked[:8]]
