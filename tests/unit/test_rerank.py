"""The reranker stage (Phase 2, plan §21.2 B3/B5): backends, cap, fallback, gate unaffected."""

from __future__ import annotations

import json

import httpx

from llmwiki.agent.retrieval import gate_score, rerank_hits, rerank_text, retrieve_layer
from llmwiki.lexical.memory import MemoryLexicalIndex
from llmwiki.models.chunk import SearchHit
from llmwiki.rerank.base import Reranker
from llmwiki.rerank.fake import FakeReranker
from llmwiki.rerank.workers_ai import WorkersAIReranker
from llmwiki.storage.layout import GENERAL
from llmwiki.wiki.domains import DomainScope


def _workers_ai(handler) -> WorkersAIReranker:
    reranker = WorkersAIReranker.__new__(WorkersAIReranker)
    reranker.account_id = "acct"
    reranker.model = "@cf/baai/bge-reranker-base"
    reranker._client = httpx.Client(base_url="https://api.cloudflare.com/client/v4/accounts/acct",
                                    transport=httpx.MockTransport(handler))
    return reranker


def test_both_backends_satisfy_the_protocol() -> None:
    assert isinstance(FakeReranker(), Reranker)
    assert isinstance(_workers_ai(lambda r: httpx.Response(200, json={})), Reranker)


def test_workers_ai_request_and_response_shape() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "result": {"response": [
            {"id": 2, "score": 0.91}, {"id": 0, "score": 0.40}, {"id": 1, "score": 0.05},
        ]}})

    ranked = _workers_ai(handler).rerank("kv cache", ["a", "b", "c"], top_n=2)

    assert seen["path"].endswith("/ai/run/@cf/baai/bge-reranker-base")
    assert seen["body"] == {"query": "kv cache",
                            "contexts": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                            "top_k": 2}
    assert ranked == [(2, 0.91), (0, 0.40)]


def test_workers_ai_surfaces_api_errors_and_skips_empty_input() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "errors": [{"message": "quota"}]})

    reranker = _workers_ai(failing)
    assert reranker.rerank("q", [], top_n=3) == []
    try:
        reranker.rerank("q", ["a"], top_n=3)
    except RuntimeError as exc:
        assert "quota" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an API error must raise (the seam turns it into a fallback)")


def test_fake_reranker_ranks_by_term_overlap_deterministically() -> None:
    ranked = FakeReranker().rerank("paged attention gpu",
                                   ["about chunking", "paged attention on the GPU", "attention"],
                                   top_n=2)
    assert ranked == [(1, 1.0), (2, 1 / 3)]


# --- the retrieval seam ------------------------------------------------------------------


def _hit(hit_id: str, score: float, text: str) -> SearchHit:
    return SearchHit(id=hit_id, score=score, text=text, source_id=hit_id.split(":")[0],
                     metadata={"source_id": hit_id.split(":")[0], "text": text},
                     dense_score=score)


def test_rerank_hits_reorders_and_keeps_the_cosine() -> None:
    candidates = [_hit("a:0", 0.9, "chunking windows"), _hit("b:0", 0.5, "paged attention gpu")]
    reordered = rerank_hits(FakeReranker(), "paged attention", candidates, k=2)
    assert [h.id for h in reordered] == ["b:0", "a:0"]
    assert reordered[0].score == 1.0 and reordered[0].dense_score == 0.5
    assert gate_score(reordered[0]) == 0.5, "the gate still reads the cosine"


def test_rerank_failure_keeps_the_fused_order() -> None:
    class Boom:
        def rerank(self, query, documents, top_n):
            raise RuntimeError("down")

    candidates = [_hit("a:0", 0.9, "x"), _hit("b:0", 0.5, "y")]
    assert [h.id for h in rerank_hits(Boom(), "q", candidates, k=2)] == ["a:0", "b:0"]

    class Empty:
        def rerank(self, query, documents, top_n):
            return []

    assert [h.id for h in rerank_hits(Empty(), "q", candidates, k=2)] == ["a:0", "b:0"]


def test_rerank_text_prefers_hit_text_then_metadata() -> None:
    assert rerank_text(_hit("a:0", 0.1, "body text")) == "body text"
    gist = SearchHit(id="slug", score=0.1, slug="slug", metadata={"title": "T", "text": "gist"})
    assert rerank_text(gist) == "T gist"
    assert rerank_text(SearchHit(id="bare", score=0.1)) == "bare"


class _FixedVectors:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def query(self, index, vector, k=5, where=None):
        self.calls.append((index, k))
        return [h.model_copy() for h in self.hits[:k]]


def test_layer_reranks_at_most_the_candidate_cap(settings) -> None:
    """Load-bearing scale guard (plan §21.10): the cross-encoder never sees more than the cap."""
    cfg = settings.model_copy(update={"hybrid_pool_k": 50, "rerank_max_candidates": 7})
    hits = [_hit(f"s{i}:0", 1.0 - i / 100, f"doc {i}") for i in range(50)]
    reranker = FakeReranker()

    top = retrieve_layer("chunks", "doc 3", [0.0], [DomainScope(GENERAL)], 3,
                         vectors=_FixedVectors(hits), settings=cfg, reranker=reranker)

    assert reranker.calls == [("doc 3", 7, 3)], "7 candidates offered, 3 asked for"
    assert [h.id for h in top][0] == "s3:0", "the exact match rose to the top"
    assert len(top) == 3


def test_reranker_without_lexical_still_pools_and_reranks(settings) -> None:
    hits = [_hit("a:0", 0.9, "chunking"), _hit("b:0", 0.8, "paged attention gpu")]
    top = retrieve_layer("chunks", "paged attention", [0.0], [DomainScope(GENERAL)], 1,
                         vectors=_FixedVectors(hits), settings=settings, reranker=FakeReranker())
    assert [h.id for h in top] == ["b:0"] and top[0].dense_score == 0.8


def test_hybrid_plus_rerank_end_to_end_over_two_scopes(settings) -> None:
    lexical = MemoryLexicalIndex()
    base = settings.vectorize_chunks_index
    lexical.upsert(f"{base}-ml", ["m:0"], ["XK-7781 controller firmware"],
                   [{"source_id": "m", "text": "XK"}])
    vectors = _FixedVectors([_hit("g:0", 0.7, "general chunk about firmware updates")])
    top = retrieve_layer("chunks", "XK-7781 firmware", [0.0],
                         [DomainScope(GENERAL), DomainScope("ml")], 2,
                         vectors=vectors, settings=settings, lexical=lexical,
                         reranker=FakeReranker())
    assert [(h.id, h.domain) for h in top][0] == ("m:0", "ml")
    assert top[0].lexical_score is not None
