"""Object store protocol. Implementations: local.py (dev/tests) and r2.py (Cloudflare)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class ObjectNotFound(KeyError):
    """Raised by ``get`` when a key does not exist."""


@runtime_checkable
class ObjectStore(Protocol):
    """Bytes in and out of a flat keyspace. Implementations must be thread-safe."""

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Write ``data`` at ``key``, overwriting any existing object."""
        ...

    def get(self, key: str) -> bytes:
        """Return the bytes at ``key``. Raises :class:`ObjectNotFound` if absent."""
        ...

    def exists(self, key: str) -> bool:
        """True if an object exists at ``key``."""
        ...

    def list(self, prefix: str) -> list[str]:
        """Return every key under ``prefix``.

        Expensive by design: the compiler is forbidden from calling this on
        ``wiki/`` (see the plan, 8.3-1).
        """
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``. Absent keys are not an error."""
        ...
