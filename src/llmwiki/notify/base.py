"""Notifier protocol (Phase 2, plan §21.2 C4): one short message, best effort."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Deliver one human-readable alert. Never raises into the caller's path."""

    def send(self, text: str) -> bool:
        """True when delivered (or logged), False when the backend refused."""
        ...
