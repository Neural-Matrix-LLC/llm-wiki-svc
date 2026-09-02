"""One contract, run against every ObjectStore implementation.

The R2 adapter is exercised here through a stub S3 client rather than the
network, so the fake cannot silently drift from the real adapter's behaviour.
The live round trip lives in ``tests/integration``.
"""

from __future__ import annotations

import pytest

from llmwiki.storage.base import ObjectNotFound
from llmwiki.storage.local import LocalObjectStore


class _StubS3:
    """Minimal in-memory stand-in for the boto3 S3 client surface R2ObjectStore uses."""

    class exceptions:  # noqa: N801 - mirrors botocore's attribute layout
        class NoSuchKey(Exception):
            pass

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803 - boto3 casing
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": _Body(self.objects[Key])}

    def head_object(self, Bucket, Key):  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)

    def get_paginator(self, name):
        objects = self.objects

        class _Paginator:
            def paginate(self, Bucket, Prefix):  # noqa: N803
                yield {"Contents": [{"Key": k} for k in sorted(objects) if k.startswith(Prefix)]}

        return _Paginator()


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest.fixture(params=["local", "r2"])
def any_store(request, tmp_path):
    if request.param == "local":
        return LocalObjectStore(root=tmp_path)
    from llmwiki.storage.r2 import R2ObjectStore

    store = R2ObjectStore.__new__(R2ObjectStore)
    store.bucket = "test"
    store._client = _StubS3()
    return store


def test_put_then_get_round_trips(any_store) -> None:
    any_store.put("raw/abc/meta.json", b'{"a": 1}', "application/json")
    assert any_store.get("raw/abc/meta.json") == b'{"a": 1}'


def test_get_missing_raises_object_not_found(any_store) -> None:
    with pytest.raises(ObjectNotFound):
        any_store.get("raw/nope/meta.json")


def test_exists_reflects_writes_and_deletes(any_store) -> None:
    assert any_store.exists("wiki/index.md") is False
    any_store.put("wiki/index.md", b"# Index")
    assert any_store.exists("wiki/index.md") is True
    any_store.delete("wiki/index.md")
    assert any_store.exists("wiki/index.md") is False


def test_delete_is_idempotent(any_store) -> None:
    any_store.delete("wiki/never-existed.md")


def test_put_overwrites(any_store) -> None:
    any_store.put("k", b"one")
    any_store.put("k", b"two")
    assert any_store.get("k") == b"two"


def test_list_returns_keys_under_the_prefix_only(any_store) -> None:
    any_store.put("raw/a/meta.json", b"1")
    any_store.put("raw/b/meta.json", b"2")
    any_store.put("wiki/index.md", b"3")

    keys = any_store.list("raw/")
    assert sorted(keys) == ["raw/a/meta.json", "raw/b/meta.json"]


def test_list_of_an_empty_prefix_is_empty(any_store) -> None:
    assert any_store.list("nothing/here/") == []
