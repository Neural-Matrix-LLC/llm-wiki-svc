"""Query-agent skill discovery - design v1.4 §4.8.2, plan §19.5/§19.9 item 1.

Skills live in a repo-root directory (``Settings.agent_skills_dir``, default
``./skills``), deliberately outside ``chains/prompts/`` - the compiler's four
fixed, non-agentic prompts never move here - and outside ``src/llmwiki``
entirely, the same posture as ``config/`` (plan §19.2). Each skill is a
SKILL.md-format file: YAML frontmatter (``name``, ``description``) plus a
markdown body that becomes the system prompt when the query agent chooses it
(``agent/query.py``).

Discovery is deliberately permissive, unlike ``config/ops.py``'s startup
validation: a stray or malformed file is logged and skipped rather than
raising. The routing config fails a whole process at startup because a bad
row is a deployment mistake; a bad skill file should not stop the query agent
from answering with whatever skills *did* parse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    """One discovered skill: the frontmatter plus the body that becomes a system prompt."""

    name: str
    description: str
    body: str
    path: Path


def discover_skills(skills_dir: Path) -> dict[str, Skill]:
    """Return every valid skill under ``skills_dir``, keyed by ``name``.

    An absent (or empty) directory is not an error - an empty result is the
    fallback switch the caller (``QueryAgent.answer``) uses to fall back to
    the fixed ``answer_query`` prompt, exactly the way an absent
    ``config/providers.py`` selects the single-provider fallback in
    ``routing_config.load_routing_config``.
    """
    if not skills_dir.is_dir():
        return {}

    skills: dict[str, Skill] = {}
    for path in sorted(skills_dir.glob("*.md")):
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("agent skill %s: could not be parsed, skipping", path)
            continue
        raw_name = post.metadata.get("name")
        if not raw_name:
            logger.warning("agent skill %s: missing required 'name' frontmatter, skipping", path)
            continue
        name = str(raw_name)
        if name in skills:
            logger.warning(
                "agent skill %s: name %r already used by %s, skipping",
                path, name, skills[name].path,
            )
            continue
        skills[name] = Skill(
            name=name,
            description=str(post.metadata.get("description", "")),
            body=post.content.strip(),
            path=path,
        )
    return skills
