"""The LLM-as-judge groundedness grader (Phase 1-D) - eval only, never the query path."""

from __future__ import annotations

from tests.doubles import ScriptedLLM

from llmwiki.agent.judge import VERDICT_SCHEMA, Judge
from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.llm.fake import FakeLLM


def test_grade_is_one_forced_schema_judge_answer_call() -> None:
    llm = ScriptedLLM({"judge_answer": {"grounded": True, "score": 0.5, "reasoning": "partly"}})
    verdict = Judge(llm).grade("q", "the answer", "the context")

    assert verdict.grounded is True and verdict.score == 0.5 and verdict.reasoning == "partly"
    call = llm.calls[0]
    assert call["op"] == "judge_answer"
    assert call["system"] == load_prompt("judge_answer")
    assert "# Answer under review\n\nthe answer" in call["prompt"]
    assert "# Retrieved context\n\nthe context" in call["prompt"]


def test_an_unusable_verdict_fails_the_example_rather_than_passing_it(caplog) -> None:
    llm = ScriptedLLM({"judge_answer": {"score": "not a number"}})
    verdict = Judge(llm).grade("q", "a", "c")

    assert verdict.grounded is False and verdict.score == 0.0
    assert "no usable verdict" in verdict.reasoning
    assert "unusable verdict" in caplog.text


def test_the_offline_double_grades_everything_grounded() -> None:
    verdict = Judge(FakeLLM()).grade("q", "a", "c")
    assert verdict.grounded is True and verdict.score == 1.0


def test_schema_requires_all_three_fields() -> None:
    assert VERDICT_SCHEMA["required"] == ["grounded", "score", "reasoning"]
