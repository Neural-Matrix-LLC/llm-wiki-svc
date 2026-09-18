"""Wiki-first retrieval with RAG fallback, answering with citations.

The order is the design's claim (design doc 2): the compiled wiki is the primary
surface because it is synthesized and dense; the chunk index is the fallback for
specifics the wiki has not absorbed.  Falling back always would waste the
compilation entirely, so the fallback is conditional and measured -
``test_agent_wiki_first`` asserts the chunk index is not touched when the wiki
already answers.

Since Phase 1-D (design §4.9) ``answer()`` runs a LangGraph ``StateGraph``
(``agent/graph.py``): the same wiki-first retrieval first, then a *bounded*
tool loop in which the model may gather more evidence (``search_wiki``,
``search_chunks``, ``get_page``, optionally ``search_web``), then skill
selection and generation, then citation resolution. With
``AGENT_MAX_TOOL_CALLS=0`` the loop is skipped and the call sequence is the
pre-graph one exactly. This class keeps its public surface (``search``,
``answer``, ``source_exists``) and owns the pieces the graph's nodes reuse.

Skill invocation (design v1.4 §4.8.2, plan §19.5, R5) sits *after* retrieval
(and after the tool loop), and *before* the citation-resolution filter - so it
changes only which system prompt(s) produce the answer text, never what counts
as a valid citation.  When ``Settings.agent_skills_dir`` holds no discoverable
skills - the default in this repository and in every existing test, via
``conftest.py``'s isolation fixture - generation is the fixed-prompt call.
"""

from __future__ import annotations

import logging
import uuid
from functools import cached_property
from typing import TYPE_CHECKING, Any

from llmwiki.agent.skills import Skill
from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.config import Settings
from llmwiki.embedding.base import Embedder
from llmwiki.llm.base import LLMClient
from llmwiki.models.chunk import SearchHit
from llmwiki.models.plan import Answer, Citation
from llmwiki.storage.base import ObjectStore
from llmwiki.storage.layout import raw_meta
from llmwiki.vector.base import VectorStore
from llmwiki.websearch.base import WebSearcher
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.pages import read_page

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# Below this similarity the wiki is not actually answering the question.
WIKI_CONFIDENCE = 0.35
# Shared across the first retrieval and every tool result (design §4.9, D4).
MAX_CONTEXT_CHARS = 12_000

# A skill-selection call that names nothing valid is retried once before
# falling back to the fixed answer_query skill (design v1.4 §4.8.2, plan
# §19.9 item 2) - a malformed tool call or a hallucinated name must not be a
# hard failure on a user-facing query.
SKILL_SELECTION_ATTEMPTS = 2
# A chain longer than this is almost certainly the model padding its own
# tool calls, not a genuinely multi-step answer; capped rather than trusted.
MAX_SKILL_CHAIN = 3

SKILL_SELECTION_SYSTEM = (
    "You are choosing which skill, or short ordered chain of skills, should "
    "answer a research question from a compiled knowledge base. Pick from the "
    "skills listed below only. Chain more than one skill only when a later "
    "skill genuinely needs an earlier skill's output - most questions need "
    "exactly one."
)

logger = logging.getLogger(__name__)


class QueryAgent:
    """Answers questions from the compiled wiki, falling back to raw chunks."""

    wiki_confidence = WIKI_CONFIDENCE
    max_context_chars = MAX_CONTEXT_CHARS

    def __init__(
        self,
        store: ObjectStore,
        vectors: VectorStore,
        embedder: Embedder,
        llm: LLMClient,
        settings: Settings,
        web_searcher: WebSearcher | None = None,
    ) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.llm = llm
        self.settings = settings
        # None means the search_web tool is never even constructed (factory
        # returns None for WEB_SEARCH_BACKEND=none).
        self.web_searcher = web_searcher

    @cached_property
    def graph(self) -> CompiledStateGraph:
        """The compiled query graph - built once per agent, on first use."""
        from llmwiki.agent.graph import build_query_graph

        return build_query_graph(self)

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        """Gist index first; chunk index only if the wiki result is weak."""
        vector = self.embedder.embed([query])[0]
        hits = self.vectors.query(self.settings.vectorize_gists_index, vector, k=k)
        if hits and hits[0].score >= WIKI_CONFIDENCE:
            return hits
        chunks = self.vectors.query(self.settings.vectorize_chunks_index, vector, k=k)
        return (hits + chunks)[:k] if hits else chunks

    def answer(self, query: str, k: int = 5) -> Answer:
        """Retrieve, (optionally) gather more, ground, answer, and verify citations.

        One graph invocation. When LangSmith tracing is on the root run's id is
        returned as ``Answer.run_id`` so the answer can be scored or corrected
        later (``POST /feedback``); it is ``None`` otherwise.
        """
        from langchain_core.runnables import RunnableConfig

        from llmwiki.agent.graph import recursion_limit

        # The root run id is minted here and handed to LangGraph, so with
        # tracing on it is exactly the LangSmith run ``POST /feedback`` will
        # find - no collector, no guessing which traced run was the root.
        run_id = uuid.uuid4()
        config = RunnableConfig(
            run_name="answer_query",
            run_id=run_id,
            recursion_limit=recursion_limit(self.settings.agent_max_tool_calls),
            metadata={
                "agent_max_tool_calls": self.settings.agent_max_tool_calls,
                "agent_web_search_policy": self.settings.agent_web_search_policy,
            },
        )
        initial: dict[str, Any] = {"query": query, "k": k}
        final = self.graph.invoke(initial, config=config)

        result: Answer = final["answer"]
        result.run_id = str(run_id) if self.settings.langsmith_tracing else None
        logger.debug(
            "answer_query: used_rag_fallback=%s tool_calls=%d external_refs=%d",
            result.used_rag_fallback, len(result.steps), len(result.external_refs),
        )
        return result

    # --- generation helpers, reused by the graph's nodes ----------------------

    def _answer_with_fixed_prompt(self, query: str, context: str) -> str:
        """The pre-R5 behaviour: always the same system prompt, one call."""
        response = self.llm.complete(
            op="answer_query",
            system=load_prompt("answer_query"),
            prompt=f"# Question\n\n{query}\n\n# Retrieved context\n\n{context}",
        )
        return response.text or (response.data or {}).get("text", "")

    def _answer_with_skills(self, query: str, context: str, skills: dict[str, Skill]) -> str:
        """Let the model pick, and chain, among the discovered skills (§19.5).

        Reuses the ``answer_query`` op and the existing forced-``schema`` call
        shape (the same mechanism the compiler already uses for structured
        output) rather than adding a new op or a new LLM-protocol surface -
        this is deliberately scoped, additive plumbing, not a new capability
        of ``LLMClient`` itself. The graph runs the two halves as separate
        nodes (``select_skills``, ``generate``); this method is the one-shot
        form.
        """
        chosen = self._select_skills(query, skills)
        if chosen is None:
            logger.warning(
                "answer_query: skill selection named nothing valid after %d attempt(s); "
                "falling back to the fixed answer_query skill",
                SKILL_SELECTION_ATTEMPTS,
            )
            return self._answer_with_fixed_prompt(query, context)
        return self._run_skill_chain(query, context, skills, chosen)

    def _run_skill_chain(
        self, query: str, context: str, skills: dict[str, Skill], chosen: list[str]
    ) -> str:
        """Run the chosen skill(s) in order; each step sees the previous step's text."""
        logger.debug("answer_query: skill chain=%s", chosen)
        step_text = ""
        for i, name in enumerate(chosen):
            prompt = f"# Question\n\n{query}\n\n# Retrieved context\n\n{context}"
            if i > 0:
                prompt += f"\n\n# Output of the previous skill ({chosen[i - 1]})\n\n{step_text}"
            response = self.llm.complete(op="answer_query", system=skills[name].body, prompt=prompt)
            step_text = response.text or (response.data or {}).get("text", "")
        return step_text

    def _select_skills(self, query: str, skills: dict[str, Skill]) -> list[str] | None:
        """One tool-use round trip choosing a skill or ordered chain of skills.

        Returns ``None`` (the defined failure mode, §19.9 item 2) if every
        attempt names nothing from the discovered set - a malformed tool call
        or a hallucinated skill name.
        """
        names = sorted(skills)
        schema = {
            "type": "object",
            "properties": {
                "skills": {
                    "type": "array",
                    "items": {"type": "string", "enum": names},
                    "minItems": 1,
                    "maxItems": MAX_SKILL_CHAIN,
                }
            },
            "required": ["skills"],
        }
        listing = "\n".join(f"- {skill.name}: {skill.description}" for skill in skills.values())
        prompt = f"# Question\n\n{query}\n\n# Available skills\n\n{listing}"

        for attempt in range(1, SKILL_SELECTION_ATTEMPTS + 1):
            response = self.llm.complete(
                op="answer_query", system=SKILL_SELECTION_SYSTEM, prompt=prompt, schema=schema,
            )
            picked = [n for n in (response.data or {}).get("skills", []) if n in skills]
            if picked:
                return picked[:MAX_SKILL_CHAIN]
            logger.warning(
                "answer_query: skill selection attempt %d/%d named nothing valid",
                attempt, SKILL_SELECTION_ATTEMPTS,
            )
        return None

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
        self,
        wiki_hits: list[SearchHit],
        chunk_hits: list[SearchHit],
        budget: int = MAX_CONTEXT_CHARS,
    ) -> tuple[str, list[Citation]]:
        manifest = gists_mod.load_gists(self.store)
        blocks: list[str] = []
        citations: dict[str, Citation] = {}

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
