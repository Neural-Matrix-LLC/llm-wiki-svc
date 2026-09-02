"""Load prompt templates from ``chains/prompts/``.

Templates are files, not string literals, so they can be diffed and reviewed on
their own.  They are cached after first read: the text is the cache-stable prefix
of every LLM call, and re-reading it per call would be pointless I/O.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """Return the template named ``name`` (without the ``.md`` suffix)."""
    path = PROMPT_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"no prompt template named {name!r} in {PROMPT_DIR}") from exc
