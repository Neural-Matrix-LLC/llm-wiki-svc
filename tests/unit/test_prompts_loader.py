"""SKILL.md-format frontmatter on ``chains/prompts/*.md`` - plan §19.4/§19.7.3 (R4).

``load_prompt`` must keep returning exactly what it always has (the body,
cached by name) while the files themselves gain ``name``/``description``
frontmatter that makes each one independently readable as an Agent Skill by
an external harness - this file asserts both halves of that claim.
"""

from __future__ import annotations

import frontmatter
import pytest

from llmwiki.chains import prompts_loader
from llmwiki.chains.prompts_loader import PROMPT_DIR, load_prompt

REAL_PROMPTS = [
    "summarize_source",
    "plan_compile",
    "create_page",
    "patch_page",
    "answer_query",
    # Phase 1-D and Phase 2 (plan §21.2 X1) prompts follow the same contract.
    "agent_step",
    "judge_answer",
    "route_domain_source",
    "route_domain_query",
    "describe_image",
    "synthesize_domain",
]


@pytest.fixture(autouse=True)
def _clear_cache():
    """``load_prompt`` is ``@cache``d by name - never let one test's monkeypatched
    ``PROMPT_DIR`` leak a cached body into another test asking for the same name."""
    load_prompt.cache_clear()
    yield
    load_prompt.cache_clear()


@pytest.mark.parametrize("name", REAL_PROMPTS)
def test_every_prompt_file_declares_name_and_description(name: str) -> None:
    """Round-trips the frontmatter an external SKILL.md consumer would read."""
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    post = frontmatter.loads(raw)

    assert post.metadata["name"] == name.replace("_", "-")
    assert post.metadata["description"]


@pytest.mark.parametrize("name", REAL_PROMPTS)
def test_load_prompt_returns_the_body_only(name: str) -> None:
    body = load_prompt(name)

    assert not body.startswith("---"), "frontmatter must be stripped, not returned"
    assert "name:" not in body.splitlines()[0]
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    assert body == frontmatter.loads(raw).content.strip()


def test_a_prompt_file_with_no_frontmatter_still_loads(tmp_path, monkeypatch) -> None:
    """Back-compat during the migration: a plain file (no ``---`` block) still works."""
    monkeypatch.setattr(prompts_loader, "PROMPT_DIR", tmp_path)
    (tmp_path / "legacy.md").write_text(
        "Just a plain prompt body, no frontmatter.\n", encoding="utf-8"
    )

    assert load_prompt("legacy") == "Just a plain prompt body, no frontmatter."


def test_missing_prompt_still_names_the_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(prompts_loader, "PROMPT_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match="no-such-prompt"):
        load_prompt("no-such-prompt")
