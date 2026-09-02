"""Behaviour specific to the filesystem adapter."""

from __future__ import annotations

import pytest

from llmwiki.storage.local import LocalObjectStore


def test_key_cannot_escape_the_root(tmp_path) -> None:
    store = LocalObjectStore(root=tmp_path / "data")
    with pytest.raises(ValueError):
        store.put("../escaped.txt", b"nope")


def test_writes_are_atomic_and_leave_no_temp_files(tmp_path) -> None:
    store = LocalObjectStore(root=tmp_path)
    store.put("wiki/concepts/rag.md", b"# RAG")

    leftovers = list(tmp_path.rglob("*.tmp"))
    assert not leftovers, f"temporary files left behind: {leftovers}"


def test_wiki_prefix_is_a_readable_obsidian_vault(tmp_path) -> None:
    """The point of this adapter beyond testing: .data/wiki opens as a vault."""
    store = LocalObjectStore(root=tmp_path)
    store.put("wiki/concepts/rag.md", b"---\ntitle: RAG\n---\n\nbody\n")

    assert (tmp_path / "wiki" / "concepts" / "rag.md").read_text().startswith("---")
