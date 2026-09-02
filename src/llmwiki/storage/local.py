"""Filesystem object store: offline development, unit tests, and Docker volumes."""

from __future__ import annotations

import threading
from pathlib import Path

from llmwiki.storage.base import ObjectNotFound


class LocalObjectStore:
    """Maps the object keyspace onto a directory tree under ``root``.

    ``.data/wiki/`` opens directly as an Obsidian vault, which is the point of
    keeping this adapter around beyond testing.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"key escapes the store root: {key!r}")
        return target

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if base.is_dir():
            root = base
        else:
            root = base.parent
        if not root.is_dir():
            return []
        keys = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix == ".tmp":
                continue
            key = path.relative_to(self.root).as_posix()
            if key.startswith(prefix):
                keys.append(key)
        return sorted(keys)

    def delete(self, key: str) -> None:
        path = self._path(key)
        with self._lock:
            path.unlink(missing_ok=True)
