"""One contract, run against the memory store and the Vectorize adapter.

Vectorize is exercised through a transport stub, so its request shape - ndjson
upsert, ``topK``, metadata filters - is asserted without a network call.
"""

from __future__ import annotations

import json

import httpx
import pytest

from llmwiki.vector.memory import MemoryVectorStore

DIM = 8


def _stub_transport(state: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/upsert"):
            assert request.headers["content-type"] == "application/x-ndjson", (
                "Vectorize upsert takes ndjson, not a JSON array"
            )
            for line in request.content.decode().splitlines():
                row = json.loads(line)
                state[row["id"]] = row
            return httpx.Response(200, json={"success": True, "result": {"mutationId": "m1"}})
        if path.endswith("/query"):
            body = json.loads(request.content)
            rows = list(state.values())
            where = body.get("filter") or {}
            for key, clause in where.items():
                rows = [r for r in rows if r["metadata"].get(key) == clause["$eq"]]
            matches = [
                {"id": r["id"], "score": 0.9, "metadata": r["metadata"]}
                for r in rows[: body["topK"]]
            ]
            return httpx.Response(200, json={"success": True, "result": {"matches": matches}})
        if path.endswith("/delete_by_ids"):
            for vector_id in json.loads(request.content)["ids"]:
                state.pop(vector_id, None)
            return httpx.Response(200, json={"success": True, "result": {}})
        # Index lifecycle (Phase 2 ensure_index): a per-transport registry of
        # created indexes and their metadata indexes, keyed off the state dict.
        created = state.setdefault("__indexes__", {})
        if path.endswith("/indexes") and request.method == "POST":
            body = json.loads(request.content)
            created[body["name"]] = {"config": body["config"], "metadata": []}
            return httpx.Response(201, json={"success": True, "result": created[body["name"]]})
        if path.endswith("/metadata_index/create"):
            name = path.split("/indexes/")[1].split("/")[0]
            prop = json.loads(request.content)["propertyName"]
            if prop in created.setdefault(name, {"metadata": []})["metadata"]:
                return httpx.Response(400, json={"success": False,
                                                 "errors": [{"message": "already exists"}]})
            created[name]["metadata"].append(prop)
            return httpx.Response(200, json={"success": True, "result": {}})
        if "/indexes/" in path and request.method == "GET":
            name = path.rsplit("/", 1)[1]
            if name in created:
                return httpx.Response(200, json={"success": True, "result": created[name]})
            return httpx.Response(404, json={"success": False, "errors": [{"message": path}]})
        return httpx.Response(404, json={"success": False, "errors": [{"message": path}]})

    return httpx.MockTransport(handler)


@pytest.fixture(params=["memory", "vectorize"])
def any_vectors(request):
    if request.param == "memory":
        return MemoryVectorStore(dim=DIM)
    from llmwiki.vector.vectorize import VectorizeStore

    store = VectorizeStore.__new__(VectorizeStore)
    store.account_id = "acct"
    store.probe_dim = DIM
    store._client = httpx.Client(
        base_url="https://api.cloudflare.com/client/v4/accounts/acct/vectorize/v2",
        transport=_stub_transport({}),
    )
    return store


def _vector(seed: float) -> list[float]:
    return [seed] + [0.0] * (DIM - 1)


def test_upsert_then_query_returns_the_vector(any_vectors) -> None:
    any_vectors.upsert("idx", ["a:0"], [_vector(1.0)], [{"source_id": "abc", "text": "hello"}])

    hits = any_vectors.query("idx", _vector(1.0), k=5)
    assert [hit.id for hit in hits] == ["a:0"]
    assert hits[0].source_id == "abc"


def test_query_on_an_empty_index_returns_nothing(any_vectors) -> None:
    assert any_vectors.query("idx", _vector(1.0), k=5) == []


def test_upsert_is_idempotent_by_id(any_vectors) -> None:
    any_vectors.upsert("idx", ["a:0"], [_vector(1.0)], [{"source_id": "abc", "text": "first"}])
    any_vectors.upsert("idx", ["a:0"], [_vector(1.0)], [{"source_id": "abc", "text": "second"}])

    hits = any_vectors.query("idx", _vector(1.0), k=5)
    assert len(hits) == 1
    assert hits[0].metadata["text"] == "second"


def test_metadata_filter_narrows_results(any_vectors) -> None:
    any_vectors.upsert("idx", ["a:0", "b:0"], [_vector(1.0), _vector(1.0)],
                       [{"source_id": "aaa"}, {"source_id": "bbb"}])

    hits = any_vectors.query("idx", _vector(1.0), k=5, where={"source_id": "aaa"})
    assert [hit.id for hit in hits] == ["a:0"]


def test_delete_by_source_removes_only_that_source(any_vectors) -> None:
    any_vectors.upsert("idx", ["a:0", "b:0"], [_vector(1.0), _vector(1.0)],
                       [{"source_id": "aaa"}, {"source_id": "bbb"}])

    assert any_vectors.delete_by_source("idx", "aaa") == 1
    remaining = any_vectors.query("idx", _vector(1.0), k=5)
    assert [hit.id for hit in remaining] == ["b:0"]


def test_gist_vectors_are_tagged_as_wiki_origin(any_vectors) -> None:
    any_vectors.upsert("gists", ["rag"], [_vector(1.0)], [{"slug": "rag", "text": "gist"}])

    hits = any_vectors.query("gists", _vector(1.0), k=1)
    assert hits[0].origin == "wiki" and hits[0].slug == "rag"


def test_ensure_index_is_idempotent_and_reports_creation(any_vectors) -> None:
    """Phase 2 (plan §21.2 A2): registering a domain creates its indexes exactly once."""
    from llmwiki.vector.vectorize import VectorizeStore

    if isinstance(any_vectors, VectorizeStore):
        assert any_vectors.ensure_index("llmwiki-gists-ml", wait=False) is True
        assert any_vectors.ensure_index("llmwiki-gists-ml", wait=False) is False
        described = any_vectors.describe_index("llmwiki-gists-ml")
        assert described["config"] == {"dimensions": DIM, "metric": "cosine"}
        assert set(described["metadata"]) == {"source_id", "slug", "type"}, (
            "metadata indexes must exist before the first filtered insert"
        )
    else:
        assert any_vectors.ensure_index("llmwiki-gists-ml") is True
        assert any_vectors.ensure_index("llmwiki-gists-ml") is False
        assert "llmwiki-gists-ml" in any_vectors.index_names()
