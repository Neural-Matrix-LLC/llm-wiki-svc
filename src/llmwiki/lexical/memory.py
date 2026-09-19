"""In-process BM25 lexical index for tests and offline runs (Phase 2, plan §21.2 B1)."""

from __future__ import annotations

import math
import threading
from collections import Counter

from llmwiki.lexical.base import hit_from_row, tokenize
from llmwiki.models.chunk import SearchHit

K1 = 1.5
B = 0.75
_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s")


def _stem(token: str) -> str:
    """A light suffix stemmer - enough for tests to behave like FTS5's porter tokenizer."""
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def _terms(text: str) -> list[str]:
    return [_stem(token) for token in tokenize(text)]


class MemoryLexicalIndex:
    """Okapi BM25 over a dict. Fine to a few thousand documents."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict]] = {}

    def _index(self, index: str) -> dict[str, dict]:
        return self._data.setdefault(index, {})

    def upsert(self, index: str, ids: list[str], texts: list[str], metadata: list[dict]) -> None:
        if not (len(ids) == len(texts) == len(metadata)):
            raise ValueError("ids, texts and metadata must be the same length")
        with self._lock:
            docs = self._index(index)
            for doc_id, text, meta in zip(ids, texts, metadata, strict=True):
                tokens = _terms(text)
                docs[doc_id] = {"tokens": Counter(tokens), "length": len(tokens),
                                "text": text, "meta": dict(meta)}

    def query(self, index: str, text: str, k: int = 5) -> list[SearchHit]:
        terms = _terms(text)
        with self._lock:
            docs = dict(self._data.get(index, {}))
        if not docs or not terms:
            return []
        n = len(docs)
        avg_len = sum(d["length"] for d in docs.values()) / n or 1.0
        df: Counter = Counter()
        for doc in docs.values():
            for term in set(terms):
                if doc["tokens"].get(term):
                    df[term] += 1
        scored: list[tuple[float, str]] = []
        for doc_id, doc in docs.items():
            score = 0.0
            for term in terms:
                tf = doc["tokens"].get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                denom = tf + K1 * (1 - B + B * doc["length"] / avg_len)
                score += idf * tf * (K1 + 1) / denom
            if score > 0:
                scored.append((score, doc_id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [hit_from_row(doc_id, score, docs[doc_id]["text"], docs[doc_id]["meta"])
                for score, doc_id in scored[:k]]

    def delete_by_source(self, index: str, source_id: str) -> int:
        with self._lock:
            docs = self._index(index)
            doomed = [doc_id for doc_id, doc in docs.items()
                      if doc["meta"].get("source_id") == source_id]
            for doc_id in doomed:
                del docs[doc_id]
        return len(doomed)

    def reset(self, index: str) -> None:
        with self._lock:
            self._data[index] = {}

    def count(self, index: str) -> int:
        return len(self._data.get(index, {}))

    def index_names(self) -> list[str]:
        return sorted(self._data)
