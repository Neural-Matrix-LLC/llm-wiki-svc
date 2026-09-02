"""Embedder protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Text to dense vectors. ``dim`` must match the Vectorize index dimension."""

    @property
    def dim(self) -> int:
        """Vector width. A mismatch surfaces as an opaque Vectorize insert error."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, returning one vector per input in the same order."""
        ...
