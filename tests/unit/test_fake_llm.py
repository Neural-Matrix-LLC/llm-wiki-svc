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


# --- Phase 2 ops (plan §21.2 X1) ---------------------------------------------------


def test_route_domain_picks_a_registered_domain_only_when_the_source_names_it() -> None:
    schema = {"properties": {"domain": {"enum": ["ml-systems", "biology", "general"]}}}
    hit = FakeLLM().complete(op="route_domain", system="s",
                             prompt="A paper on GPU scheduling for ML-Systems.", schema=schema)
    assert hit.data["domain"] == "ml-systems"
    miss = FakeLLM().complete(op="route_domain", system="s", prompt="A recipe.", schema=schema)
    assert miss.data["domain"] == "general" and miss.data["suggested_domain"] == ""


def test_route_domain_query_form_returns_a_list() -> None:
    schema = {"properties": {"domains": {"items": {"enum": ["general", "biology"]}}}}
    response = FakeLLM().complete(op="route_domain", system="s", prompt="q", schema=schema)
    assert response.data == {"domains": ["biology"]}


def test_describe_image_and_synthesize_domain_are_structurally_valid() -> None:
    described = FakeLLM().complete(op="describe_image", system="s", prompt="page 3")
    assert described.text.startswith("Offline image description")
    synthesized = FakeLLM().complete(op="synthesize_domain", system="s", prompt="- a: gist")
    assert {"title", "gist", "body"} <= set(synthesized.data)
