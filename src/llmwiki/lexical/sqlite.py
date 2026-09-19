"""SQLite FTS5 lexical index - one file per index name (Phase 2, plan §21.2 B2).

A *derived* index: everything in it can be rebuilt from ``raw/*/extracted.md``
and the gist manifests (``llmwiki lexical rebuild``), so it lives on the
service's local volume rather than in object storage. A missing file means
"not built yet": queries return ``[]`` with a warning and retrieval is
dense-only until the rebuild runs. Writes happen under a process-local lock
per index; the ingest worker already serializes a domain's writers.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

from llmwiki.lexical.base import hit_from_row, tokenize
from llmwiki.models.chunk import SearchHit

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    source_id TEXT,
    slug TEXT,
    text TEXT NOT NULL,
    meta TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS docs_source ON docs(source_id);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    text, content='docs', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO fts(fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
    INSERT INTO fts(fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""


def fts5_available() -> bool:
    """Whether this interpreter's sqlite was built with FTS5 (the python:3.11 images are)."""
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression: quoted terms joined by OR.

    Quoting every token neutralises FTS5's own syntax (``"``, ``*``, ``NEAR``,
    column filters) in user input; OR keeps recall high for rank fusion, and
    ``bm25()`` still rewards documents matching more of the terms.
    """
    terms = [term.replace('"', "") for term in tokenize(text)]
    terms = [term for term in terms if term]
    return " OR ".join(f'"{term}"' for term in terms)


class SqliteLexicalIndex:
    """FTS5-backed :class:`LexicalIndex`; ``root/{index}.sqlite`` per index."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def path(self, index: str) -> Path:
        if "/" in index or index.startswith("."):
            raise ValueError(f"not a valid index name: {index!r}")
        return self.root / f"{index}.sqlite"

    def _lock(self, index: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(index, threading.Lock())

    def _connect(self, index: str, *, create: bool) -> sqlite3.Connection | None:
        path = self.path(index)
        if not path.exists():
            if not create:
                return None
            self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        if create:
            conn.executescript(_SCHEMA)
        return conn

    def upsert(self, index: str, ids: list[str], texts: list[str], metadata: list[dict]) -> None:
        if not (len(ids) == len(texts) == len(metadata)):
            raise ValueError("ids, texts and metadata must be the same length")
        if not ids:
            return
        rows = [
            (doc_id, meta.get("source_id"), meta.get("slug"), text, json.dumps(meta, default=str))
            for doc_id, text, meta in zip(ids, texts, metadata, strict=True)
        ]
        with self._lock(index):
            conn = self._connect(index, create=True)
            assert conn is not None
            try:
                with conn:
                    conn.executemany(
                        "INSERT INTO docs(id, source_id, slug, text, meta) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id, "
                        "slug=excluded.slug, text=excluded.text, meta=excluded.meta",
                        rows,
                    )
            finally:
                conn.close()

    def query(self, index: str, text: str, k: int = 5) -> list[SearchHit]:
        expression = fts_query(text)
        if not expression:
            return []
        conn = self._connect(index, create=False)
        if conn is None:
            logger.warning("lexical index %s has not been built; run `llmwiki lexical rebuild`",
                           self.path(index))
            return []
        try:
            rows = conn.execute(
                "SELECT docs.id, docs.text, docs.meta, bm25(fts) AS rank FROM fts "
                "JOIN docs ON docs.rowid = fts.rowid WHERE fts MATCH ? ORDER BY rank LIMIT ?",
                (expression, k),
            ).fetchall()
        finally:
            conn.close()
        # bm25() is "lower is better" and negative; flip it so a bigger score
        # is a better match, like every other score in the system.
        return [hit_from_row(doc_id, -float(rank), doc_text, json.loads(meta))
                for doc_id, doc_text, meta, rank in rows]

    def delete_by_source(self, index: str, source_id: str) -> int:
        with self._lock(index):
            conn = self._connect(index, create=False)
            if conn is None:
                return 0
            try:
                with conn:
                    cursor = conn.execute("DELETE FROM docs WHERE source_id = ?", (source_id,))
                    return int(cursor.rowcount or 0)
            finally:
                conn.close()

    def reset(self, index: str) -> None:
        with self._lock(index):
            path = self.path(index)
            if path.exists():
                path.unlink()

    def count(self, index: str) -> int:
        conn = self._connect(index, create=False)
        if conn is None:
            return 0
        try:
            return int(conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
        finally:
            conn.close()
