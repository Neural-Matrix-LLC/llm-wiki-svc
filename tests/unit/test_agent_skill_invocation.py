"""Query-agent skill invocation - design v1.4 §4.8.2, plan §19.5/§19.7.3 (R5).

Every existing test in ``test_agent.py`` keeps exercising the pre-R5
fixed-prompt path unmodified: ``conftest.py``'s ``_isolate_agent_skills_dir``
autouse fixture points ``Settings.agent_skills_dir`` at a guaranteed-absent
path unless a test here explicitly overrides it, the same isolation pattern
``_isolate_llm_routing_config`` already established for R1's ``config/``. This
file is where ``agent_skills_dir`` gets pointed at a real, populated
directory.
"""

from __future__ import annotations

import pytest
from tests.factories import make_extracted_doc

from llmwiki.agent.query import SKILL_SELECTION_ATTEMPTS, QueryAgent
from llmwiki.agent.skills import discover_skills
from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.llm.base import LLMResponse
from llmwiki.models.plan import CostRecord
from llmwiki.models.source import SourceMeta
from llmwiki.storage.layout import raw_meta
from llmwiki.wiki.compiler import Compiler

GROUNDED_ANSWER_SKILL = """---
name: grounded-answer
description: Default single-skill answer.
---

GROUNDED ANSWER SKILL BODY.
"""

CROSS_REFERENCE_SKILL = """---
name: cross-reference
description: Compares more than one concept.
---

CROSS REFERENCE SKILL BODY.
"""


class SequencedLLM:
    """Returns one scripted response per call, in order; records every call."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def complete(self, *, op, system, prompt, schema=None, model=None,
                 max_tokens=None, temperature=None) -> LLMResponse:
        self.calls.append({"op": op, "system": system, "prompt": prompt, "schema": schema})
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        data = self.responses[index] if self.responses else {}
        return LLMResponse(
            text=data.get("text", ""),
            data=data,
            usage=CostRecord(op=op, model=model or "scripted", input_tokens=len(prompt) // 4,
                             output_tokens=16),
        )


def _write_skills(tmp_path, *bodies: str):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for i, body in enumerate(bodies):
        (skills_dir / f"skill_{i}.md").write_text(body, encoding="utf-8")
    return skills_dir


@pytest.fixture
def populated(store, vectors, embedder, llm, settings):
    """A wiki with one compiled source and its raw meta on disk (mirrors test_agent.py)."""
    doc = make_extracted_doc()
    store.put(
        raw_meta(doc.source_id),
        SourceMeta(source_id=doc.source_id, modality="web", sha256="0" * 64,
                   url=doc.url, title=doc.title).model_dump_json().encode(),
        "application/json",
    )
    Compiler(store, vectors, embedder, llm, settings).compile_source(doc)
    return doc


# --- discovery ---------------------------------------------------------------


def test_discover_skills_finds_every_frontmatter_file(tmp_path) -> None:
    skills_dir = _write_skills(tmp_path, GROUNDED_ANSWER_SKILL, CROSS_REFERENCE_SKILL)

    skills = discover_skills(skills_dir)

    assert set(skills) == {"grounded-answer", "cross-reference"}
    assert skills["grounded-answer"].description == "Default single-skill answer."
    assert skills["grounded-answer"].body == "GROUNDED ANSWER SKILL BODY."


def test_discover_skills_returns_empty_for_an_absent_directory(tmp_path) -> None:
    assert discover_skills(tmp_path / "does-not-exist") == {}


def test_discover_skills_skips_a_file_with_no_name(tmp_path, caplog) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "no_name.md").write_text("---\ndescription: missing a name\n---\nbody\n",
                                            encoding="utf-8")

    with caplog.at_level("WARNING"):
        result = discover_skills(skills_dir)

    assert result == {}
    assert "no_name.md" in caplog.text


def test_discover_skills_skips_a_duplicate_name(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "a.md").write_text(GROUNDED_ANSWER_SKILL, encoding="utf-8")
    (skills_dir / "b.md").write_text(GROUNDED_ANSWER_SKILL, encoding="utf-8")

    skills = discover_skills(skills_dir)

    assert list(skills) == ["grounded-answer"]
    assert skills["grounded-answer"].path.name == "a.md"  # first one wins, sorted by filename


# --- QueryAgent.answer(): skill selection and chaining ------------------------


def test_no_discoverable_skills_uses_the_fixed_prompt_unchanged(
    populated, store, vectors, embedder, llm, settings
) -> None:
    """settings.agent_skills_dir is the isolated (absent) default here - the fallback path."""
    agent = QueryAgent(store, vectors, embedder, llm, settings)

    agent.answer("retrieval augmented generation")

    calls = [c for c in llm.calls if c["op"] == "answer_query"]
    assert len(calls) == 1, "no skills discovered must mean exactly one fixed-prompt call"
    assert calls[0]["system"] == load_prompt("answer_query")


def test_the_models_skill_choice_is_honored(
    populated, store, vectors, embedder, settings, tmp_path
) -> None:
    skills_dir = _write_skills(tmp_path, GROUNDED_ANSWER_SKILL, CROSS_REFERENCE_SKILL)
    settings = settings.model_copy(update={"agent_skills_dir": skills_dir})
    scripted = SequencedLLM([
        {"skills": ["cross-reference"]},
        {"text": "Cross-reference answer text"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, settings)

    result = agent.answer("compare two things from the wiki")

    assert result.text == "Cross-reference answer text"
    assert len(scripted.calls) == 2
    assert "skills" in (scripted.calls[0]["schema"] or {}).get("properties", {})
    assert scripted.calls[1]["system"] == "CROSS REFERENCE SKILL BODY."


def test_a_chained_multi_skill_turn_is_handled(
    populated, store, vectors, embedder, settings, tmp_path
) -> None:
    skills_dir = _write_skills(tmp_path, GROUNDED_ANSWER_SKILL, CROSS_REFERENCE_SKILL)
    settings = settings.model_copy(update={"agent_skills_dir": skills_dir})
    scripted = SequencedLLM([
        {"skills": ["grounded-answer", "cross-reference"]},
        {"text": "STEP1 OUTPUT"},
        {"text": "STEP2 OUTPUT USING STEP1"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, settings)

    result = agent.answer("a question needing two skills")

    assert result.text == "STEP2 OUTPUT USING STEP1"
    assert len(scripted.calls) == 3
    assert scripted.calls[1]["system"] == "GROUNDED ANSWER SKILL BODY."
    assert scripted.calls[2]["system"] == "CROSS REFERENCE SKILL BODY."
    assert "Output of the previous skill (grounded-answer)" in scripted.calls[2]["prompt"]
    assert "STEP1 OUTPUT" in scripted.calls[2]["prompt"]


def test_an_invalid_skill_choice_falls_back_to_the_fixed_skill(
    populated, store, vectors, embedder, settings, tmp_path
) -> None:
    skills_dir = _write_skills(tmp_path, GROUNDED_ANSWER_SKILL)
    settings = settings.model_copy(update={"agent_skills_dir": skills_dir})
    scripted = SequencedLLM([
        {"skills": ["not-a-real-skill"]},
        {"skills": []},
        {"text": "FIXED FALLBACK ANSWER"},
    ])
    agent = QueryAgent(store, vectors, embedder, scripted, settings)

    result = agent.answer("a question the model mis-selects for")

    assert result.text == "FIXED FALLBACK ANSWER"
    assert len(scripted.calls) == SKILL_SELECTION_ATTEMPTS + 1
    assert scripted.calls[-1]["system"] == load_prompt("answer_query")


def test_every_citation_resolves_to_a_real_raw_object_via_skill_invocation(
    populated, store, vectors, embedder, llm, settings, tmp_path
) -> None:
    """The load-bearing contract (test_agent.py), re-run specifically against
    the skill-invoked path - the citation filter sits above skill selection
    and must not care which skill produced the text (plan §19.5 item 3)."""
    skills_dir = _write_skills(tmp_path, GROUNDED_ANSWER_SKILL)
    settings = settings.model_copy(update={"agent_skills_dir": skills_dir})
    agent = QueryAgent(store, vectors, embedder, llm, settings)

    result = agent.answer("retrieval augmented generation")

    assert result.citations, "an answer grounded in the wiki must carry citations"
    for citation in result.citations:
        assert agent.source_exists(citation.source_id), f"dangling citation {citation.source_id}"
