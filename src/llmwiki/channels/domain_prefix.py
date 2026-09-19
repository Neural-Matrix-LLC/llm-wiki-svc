"""The one-token domain hint a capture channel can carry (Phase 2, plan §21.7).

Telegram has no form fields and email has only a subject, so the explicit
``domain=`` every other surface takes is spelled as a leading marker instead:
``#ml-systems <caption or text>`` on Telegram, ``[ml-systems] <subject>`` on
email. The marker is stripped from the title; whether the name is registered
is decided by ``tools.ingest_source`` like everywhere else, never here.
"""

from __future__ import annotations

import re

_HASHTAG = re.compile(r"^\s*#([a-z0-9][a-z0-9-]{0,31})\b[ \t]*")
_BRACKET = re.compile(r"^\s*\[([a-z0-9][a-z0-9-]{0,31})\][ \t]*")


def split_hashtag_domain(text: str) -> tuple[str | None, str]:
    """``"#ml-systems Paged attention"`` → ``("ml-systems", "Paged attention")``."""
    match = _HASHTAG.match(text or "")
    if not match:
        return None, text or ""
    return match.group(1), (text or "")[match.end():]


def split_bracket_domain(text: str) -> tuple[str | None, str]:
    """``"[ml-systems] Paper"`` → ``("ml-systems", "Paper")``."""
    match = _BRACKET.match(text or "")
    if not match:
        return None, text or ""
    return match.group(1), (text or "")[match.end():]
