"""Wiki-first retrieval with RAG fallback, answering with citations.

The order is the design's claim (design doc 2): the compiled wiki is the primary
surface because it is synthesized and dense; the chunk index is the fallback for
specifics the wiki has not absorbed.  Falling back always would waste the
compilation entirely, so the fallback is conditional and measured -
``test_agent_wiki_first`` asserts the chunk index is not touched when the wiki
already answers.
"""

from __future__ import annotations

import logging

from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.config import Settings
from llmwiki.embedding.base import Embedder
from llmwiki.llm.base import LLMClient
from llmwiki.models.chunk import SearchHit
from llmwiki.models.plan import Answer, Citation
from llmwiki.storage.base import ObjectStore
from llmwiki.storage.layout import raw_meta
from llmwiki.vector.base import VectorStore
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.pages import read_page

# Below this similarity the wiki is not actually answering the question.
WIKI_CONFIDENCE = 0.35
MAX_CONTEXT_CHARS = 12_000

logger = logging.getLogger(__name__)


class QueryAgent:
    """Answers questions from the compiled wiki, falling back to raw chunks."""

    def __init__(
        self,
        store: ObjectStore,
        vectors: VectorStore,
        embedder: Embedder,
        llm: LLMClient,
        settings: Settings,
    ) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.llm = llm
        self.settings = settings

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        """Gist index first; chunk index only if the wiki result is weak."""
        vector = self.embedder.embed([query])[0]
        hits = self.vectors.query(self.settings.vectorize_gists_index, vector, k=k)
        if hits and hits[0].score >= WIKI_CONFIDENCE:
            return hits
        chunks = self.vectors.query(self.settings.vectorize_chunks_index, vector, k=k)
        return (hits + chunks)[:k] if hits else chunks

    def answer(self, query: str, k: int = 5) -> Answer:
        """Retrieve, ground, answer, and verify that every citation resolves."""
        vector = self.embedder.embed([query])[0]
        wiki_hits = self.vectors.query(self.settings.vectorize_gists_index, vector, k=k)
        strong = [hit for hit in wiki_hits if hit.score >= WIKI_CONFIDENCE]

        used_fallback = False
        chunk_hits: list[SearchHit] = []
        if not strong:
            used_fallback = True
            chunk_hits = self.vectors.query(self.settings.vectorize_chunks_index, vector, k=k)

        context, citations = self._build_context(strong or wiki_hits, chunk_hits)
        if not context.strip():
            return Answer(
                text="Nothing in the knowledge base addresses that question yet.",
                citations=[],
                used_rag_fallback=used_fallback,
            )

        logger.debug("answer_query: used_rag_fallback=%s", used_fallback)
        response = self.llm.complete(
            op="answer_query",
            system=load_prompt("answer_query"),
            prompt=f"# Question\n\n{query}\n\n# Retrieved context\n\n{context}",
        )
        text = response.text or (response.data or {}).get("text", "")
        resolved = [c for c in citations if self.source_exists(c.source_id)]
        return Answer(text=text, citations=resolved, used_rag_fallback=used_fallback)

    def source_exists(self, source_id: str) -> bool:
        """True if a citation points at a real object under ``raw/``.

        Used both here and by the smoke script: a citation that does not resolve
        is the failure mode this system cannot tolerate.
        """
        try:
            return self.store.exists(raw_meta(source_id))
        except ValueError:
            return False

    def _build_context(
        self, wiki_hits: list[SearchHit], chunk_hits: list[SearchHit]
    ) -> tuple[str, list[Citation]]:
        manifest = gists_mod.load_gists(self.store)
        blocks: list[str] = []
        citations: dict[str, Citation] = {}
        budget = MAX_CONTEXT_CHARS

        for hit in wiki_hits:
            slug = hit.slug or hit.id
            gist = manifest.get(slug)
            page = read_page(self.store, slug, gist.type if gist else "concept")
            if page is None:
                continue
            block = f"## Wiki page [[{slug}]]\n\n{page.body[:budget]}"
            blocks.append(block)
            budget -= len(block)
            for source_id in page.front_matter.sources:
                citations.setdefault(
                    source_id,
                    Citation(source_id=source_id, title=page.front_matter.title, slug=slug),
                )
            if budget <= 0:
                break

        for hit in chunk_hits:
            if budget <= 0:
                break
            source_id = hit.source_id or hit.metadata.get("source_id", "")
            text = hit.text or hit.metadata.get("text", "")
            block = f"## Source chunk {source_id}\n\n{text[:budget]}"
            blocks.append(block)
            budget -= len(block)
            if source_id:
                citations.setdefault(
                    source_id,
                    Citation(
                        source_id=source_id,
                        title=hit.metadata.get("title", ""),
                        url=hit.metadata.get("url"),
                    ),
                )

        return "\n\n".join(blocks), list(citations.values())
