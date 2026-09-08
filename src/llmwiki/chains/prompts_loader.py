"""Load prompt templates from ``chains/prompts/``.

Templates are files, not string literals, so they can be diffed and reviewed on
their own.  They are cached after first read: the text is the cache-stable prefix
of every LLM call, and re-reading it per call would be pointless I/O.

Each file carries YAML frontmatter (``name``, ``description``) that makes it a
valid Agent Skill on its own - discoverable by an external harness (Claude
Code, the Claude Agent SDK, an MCP client), not only by this codebase's own
loader (design v1.4 §4.8.2, plan §19.4). ``load_prompt`` parses and discards
the frontmatter, returning the body only - exactly what every existing caller
already expects. A file with no frontmatter still loads (back-compat during
the migration): ``frontmatter.loads`` returns empty metadata and the whole
text as content when there is no ``---`` block.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import frontmatter

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """Return the template body named ``name`` (without the ``.md`` suffix).

    Frontmatter, if present, is parsed here and never reaches the caller - the
    body is, and always has been, the whole of this function's contract.
    """
    path = PROMPT_DIR / f"{name}.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"no prompt template named {name!r} in {PROMPT_DIR}") from exc
    return frontmatter.loads(raw).content.strip()
