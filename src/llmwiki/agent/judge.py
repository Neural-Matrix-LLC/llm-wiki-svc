"""LLM-as-judge groundedness grader (Phase 1-D, design §4.9) - eval only.

Never on the query path: ``QueryAgent`` does not call this. The eval loop
(``llmwiki.eval``, ``scripts/eval_answer.py --judge``) asks it whether an
answer is supported by the exact context the answer was generated from.
One forced-schema ``complete(op="judge_answer")`` call, routed like any other
op through ``config/ops.py``; the offline ``FakeLLM`` grades everything as
grounded, which is what keeps the offline eval loop key-free.
"""

from __future__ import annotations

import logging

from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.llm.base import LLMClient
from llmwiki.models.plan import Verdict

logger = logging.getLogger(__name__)

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["grounded", "score", "reasoning"],
}


class Judge:
    """Grades an answer against the context it came from."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def grade(self, question: str, answer: str, context: str) -> Verdict:
        response = self.llm.complete(
            op="judge_answer",
            system=load_prompt("judge_answer"),
            prompt=(
                f"# Question\n\n{question}\n\n"
                f"# Retrieved context\n\n{context}\n\n"
                f"# Answer under review\n\n{answer}"
            ),
            schema=VERDICT_SCHEMA,
        )
        data = response.data or {}
        try:
            return Verdict(
                grounded=bool(data["grounded"]),
                score=float(data["score"]),
                reasoning=str(data.get("reasoning", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            # A judge that cannot produce a verdict fails the example rather
            # than passing it: a silent pass is the worse eval outcome.
            logger.warning("judge_answer: unusable verdict %r (%s)", data, exc)
            return Verdict(
                grounded=False, score=0.0, reasoning=f"judge returned no usable verdict: {exc}",
            )
