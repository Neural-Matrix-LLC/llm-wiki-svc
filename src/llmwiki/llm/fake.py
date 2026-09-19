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
        data = self._synthesize(op, prompt, schema)
        return LLMResponse(text=data.get("text", json.dumps(data)), data=data, usage=usage)

    def describe(
        self,
        *,
        op: str,
        system: str,
        prompt: str,
        images: list,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Vision form (Phase 2): a deterministic description naming what it was given."""
        self.calls.append({
            "op": op, "system": system, "prompt": prompt, "model": model,
            "max_tokens": max_tokens, "temperature": temperature, "images": len(images),
        })
        usage = CostRecord(op=op, model=model or "fake", input_tokens=len(prompt) // 4 + 800,
                           output_tokens=96, cost_usd=0.0)
        if op in self.responses:
            data = self.responses[op]
            return LLMResponse(text=data.get("text", json.dumps(data)), data=data, usage=usage)
        sizes = ", ".join(f"{len(image.data)} bytes {image.media_type}" for image in images)
        text = (f"Offline description of {len(images)} image(s) ({sizes}) for: {prompt}\n\n"
                "Transcribed text: (none - offline double)")
        return LLMResponse(text=text, data={"text": text}, usage=usage)

    def _synthesize(self, op: str, prompt: str, schema: dict | None = None) -> dict:
        # Query-agent skill selection (plan §19.5, R5) reuses the answer_query
        # op with its own schema shape rather than a distinct op - detected
        # here the same way, by looking for the "skills" property, since this
        # double has no other signal to tell the two calls apart.
        if op == "answer_query" and schema and "skills" in schema.get("properties", {}):
            enum = schema["properties"]["skills"].get("items", {}).get("enum", [])
            return {"skills": enum[:1]}
        # Phase 1-D: the query graph's tool decision. The double always answers
        # at once, so the default/offline path never enters the tool loop and
        # every pre-graph test sees exactly the call sequence it always did.
        # Loop tests script "agent_step" explicitly (tests/doubles.ScriptedLLM).
        if op == "agent_step":
            return {"action": "answer", "args": {},
                    "reason": "offline: answer from what was retrieved"}
        if op == "judge_answer":
            return {"grounded": True, "score": 1.0, "reasoning": "offline: not judged"}
        # Phase 2 (plan §21.2 X1). route_domain: pick the first registered domain
        # the schema offers other than general when the prompt mentions it,
        # else general - deterministic, never invents a name. The query form
        # asks for a list ("domains") instead of one ("domain").
        if op == "route_domain":
            props = (schema or {}).get("properties", {})
            if "domains" in props:
                enum = props["domains"].get("items", {}).get("enum", [])
                return {"domains": [d for d in enum if d != "general"][:1] or enum[:1]}
            enum = props.get("domain", {}).get("enum", [])
            # Only the source itself counts - the prompt's registry listing
            # names every domain, and would otherwise match every time.
            lowered = prompt.split("# Source", 1)[-1].lower()
            for name in enum:
                if name != "general" and name.lower() in lowered:
                    return {"domain": name, "confidence": 0.9, "suggested_domain": "",
                            "reason": "offline: name appears in the source"}
            return {"domain": "general", "confidence": 1.0, "suggested_domain": "",
                    "reason": "offline: nothing registered matched"}
        if op == "describe_image":
            return {"text": f"Offline image description ({len(prompt)} chars of context)."}
        if op == "synthesize_domain":
            return {
                "title": "Overview",
                "gist": "Offline overview of the domain.",
                "body": (
                    "## Overview\n\n"
                    f"{' '.join(prompt.split()[:60])}\n\n"
                    "## Key pages\n\n"
                    "Compiled offline by the fake LLM.\n"
                ),
            }
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
