"""Answer-quality evaluators - pure functions over (inputs, outputs, reference).

Each takes the LangSmith ``(inputs, outputs, reference_outputs)`` triple and
returns ``{"key", "score", "comment"}``, so the same function serves
``langsmith.evaluate`` and the offline ``run.run_local`` unchanged. ``outputs``
is what :func:`run.answer_target` produced from ``tools.answer``::

    {"text", "citations": [source_id, ...], "used_rag_fallback", "context",
     "steps": [{"tool", "args", "chars"}, ...], "external_refs": [...]}

The four deterministic ones cost nothing; ``judge_grounded`` spends one
``judge_answer`` call per example and is opt-in (``--judge``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from llmwiki import tools

Score = dict[str, Any]
Evaluator = Callable[[dict, dict, dict], Score]


def citations_resolve(inputs: dict, outputs: dict, reference_outputs: dict) -> Score:
    """Every cited source id resolves to a real ``raw/`` object (the load-bearing contract)."""
    cited = list(outputs.get("citations", []))
    dangling = [sid for sid in cited if not tools.source_exists(sid)]
    return {
        "key": "citations_resolve",
        "score": 0.0 if dangling else 1.0,
        "comment": f"dangling: {dangling}" if dangling else f"{len(cited)} citation(s) resolve",
    }


def expected_source_cited(inputs: dict, outputs: dict, reference_outputs: dict) -> Score:
    """Fraction of the example's expected sources that the answer actually cites."""
    expected = list(reference_outputs.get("expected_sources", []))
    if not expected:
        return {"key": "expected_source_cited", "score": 1.0, "comment": "no expectation"}
    cited = set(outputs.get("citations", []))
    hit = [sid for sid in expected if sid in cited]
    missing = [sid for sid in expected if sid not in cited]
    return {
        "key": "expected_source_cited",
        "score": len(hit) / len(expected),
        "comment": f"missing: {missing}" if missing else "all expected sources cited",
    }


def must_mention(inputs: dict, outputs: dict, reference_outputs: dict) -> Score:
    """Fraction of required terms present in the answer text, case-insensitive."""
    terms = list(reference_outputs.get("must_mention", []))
    if not terms:
        return {"key": "must_mention", "score": 1.0, "comment": "no expectation"}
    text = str(outputs.get("text", "")).lower()
    missing = [t for t in terms if t.lower() not in text]
    return {
        "key": "must_mention",
        "score": (len(terms) - len(missing)) / len(terms),
        "comment": f"missing: {missing}" if missing else "all terms present",
    }


def tool_calls(inputs: dict, outputs: dict, reference_outputs: dict) -> Score:
    """How many tool calls the graph made - a cost metric, not a pass/fail."""
    steps = list(outputs.get("steps", []))
    names = [s.get("tool", "?") for s in steps]
    return {"key": "tool_calls", "score": float(len(steps)), "comment": ", ".join(names) or "none"}


def judge_grounded(inputs: dict, outputs: dict, reference_outputs: dict) -> Score:
    """LLM-as-judge: is the answer supported by the context it was written from?"""
    verdict = tools.judge_answer(
        str(inputs.get("question", "")), str(outputs.get("text", "")),
        str(outputs.get("context", "")),
    )
    return {"key": "judge_grounded", "score": verdict.score, "comment": verdict.reasoning}


# Pass/fail thresholds for the offline gate (scripts/eval_answer.py exits 1
# below them). tool_calls and judge_grounded are reported, never gated.
DETERMINISTIC: list[Evaluator] = [
    citations_resolve, expected_source_cited, must_mention, tool_calls,
]
GATED: dict[str, float] = {
    "citations_resolve": 1.0, "expected_source_cited": 1.0, "must_mention": 1.0,
}
