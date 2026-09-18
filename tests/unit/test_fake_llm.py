"""The offline LLM double's Phase 1-D synthesis: never loops, always grades grounded."""

from __future__ import annotations

from llmwiki.llm.fake import FakeLLM


def test_agent_step_always_answers_immediately() -> None:
    """The offline path must never enter the tool loop on its own - every
    pre-graph test and the offline smoke/eval runs depend on it."""
    response = FakeLLM().complete(op="agent_step", system="s", prompt="p", schema={})
    assert response.data["action"] == "answer"
    assert response.data["args"] == {}


def test_judge_answer_grades_grounded_with_a_full_score() -> None:
    response = FakeLLM().complete(op="judge_answer", system="s", prompt="p", schema={})
    assert response.data == {"grounded": True, "score": 1.0, "reasoning": "offline: not judged"}


def test_scripted_responses_still_win_over_synthesis() -> None:
    llm = FakeLLM({"agent_step": {"action": "get_page", "args": {"slug": "x"}}})
    assert llm.complete(op="agent_step", system="s", prompt="p").data["action"] == "get_page"
