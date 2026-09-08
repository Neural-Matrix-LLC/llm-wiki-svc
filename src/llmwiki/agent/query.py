"""Wiki-first retrieval with RAG fallback, answering with citations.

The order is the design's claim (design doc 2): the compiled wiki is the primary
surface because it is synthesized and dense; the chunk index is the fallback for
specifics the wiki has not absorbed.  Falling back always would waste the
compilation entirely, so the fallback is conditional and measured -
``test_agent_wiki_first`` asserts the chunk index is not touched when the wiki
already answers.

Skill invocation (design v1.4 §4.8.2, plan §19.5, R5) sits *after* retrieval and
context-building, and *before* the citation-resolution filter - so it changes
only which system prompt(s) produce the answer text, never what counts as a
valid citation.  When ``Settings.agent_skills_dir`` holds no discoverable
skills - the default in this repository and in every existing test, via
``conftest.py``'s isolation fixture - ``answer()`` is byte-for-byte the
pre-R5 fixed-prompt call.
"""

from __future__ import annotations

import logging

from llmwiki.agent.skills import Skill, discover_skills
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
        skills = discover_skills(self.settings.agent_skills_dir)
        if skills:
            text = self._answer_with_skills(query, context, skills)
        else:
            text = self._answer_with_fixed_prompt(query, context)

        resolved = [c for c in citations if self.source_exists(c.source_id)]
        return Answer(text=text, citations=resolved, used_rag_fallback=used_fallback)

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
        of ``LLMClient`` itself.
        """
        chosen = self._select_skills(query, skills)
        if chosen is None:
            logger.warning(
                "answer_query: skill selection named nothing valid after %d attempt(s); "
                "falling back to the fixed answer_query skill",
                SKILL_SELECTION_ATTEMPTS,
            )
            return self._answer_with_fixed_prompt(query, context)

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
