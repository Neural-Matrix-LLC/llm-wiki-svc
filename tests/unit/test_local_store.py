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


def test_list_treats_the_prefix_as_a_string_not_a_folder(tmp_path) -> None:
    """S3 semantics: ``raw/ab`` must match ``raw/abcd-slug/...`` - capture's dedup probe."""
    store = LocalObjectStore(root=tmp_path)
    store.put("raw/abcd-one/meta.json", b"1")
    store.put("raw/abcd-one/original.pdf", b"1")
    store.put("raw/abzz-two/meta.json", b"2")
    store.put("raw/zzzz/meta.json", b"3")

    assert store.list("raw/abcd") == ["raw/abcd-one/meta.json", "raw/abcd-one/original.pdf"]
    assert store.list("raw/ab") == [
        "raw/abcd-one/meta.json", "raw/abcd-one/original.pdf", "raw/abzz-two/meta.json"
    ]
    assert store.list("raw/abcd-one/") == ["raw/abcd-one/meta.json", "raw/abcd-one/original.pdf"]
    assert store.list("raw/nope") == []
    assert store.list("nothing/here") == []


def test_partial_prefix_list_does_not_walk_sibling_folders(tmp_path, monkeypatch) -> None:
    """The dedup probe must stay O(one source) as the corpus grows (design 4.4)."""
    from pathlib import Path

    store = LocalObjectStore(root=tmp_path)
    for i in range(20):
        store.put(f"raw/{i:016x}-page/meta.json", b"x")
    walked: list[Path] = []
    original_rglob = Path.rglob

    def spying_rglob(self: Path, pattern: str):
        walked.append(self)
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", spying_rglob)
    assert len(store.list(f"raw/{5:016x}")) == 1
    assert walked == [tmp_path / "raw" / f"{5:016x}-page"]
