"""The query graph's evidence-gathering tools (Phase 1-D, design §4.9).

Real LangChain tools (``langchain_core.tools.StructuredTool``) so that their
``name``/``description``/``args`` are the single source of truth for the
action schema the model chooses from (``action_schema``) *and* so they can be
bound natively to a chat model later, when the N7 ``get_llm()`` surface lands.
Today the graph dispatches to them itself (``graph.py``'s ``tools`` node)
because the model's choice arrives through ``LLMClient.complete(schema=...)``
rather than through a provider's tool-calling API - decision D2.

Each tool returns a :class:`ToolResult` rather than a string: the model sees
``observation``; the graph merges ``context_block`` and ``citations`` into
state exactly as ``QueryAgent._build_context`` does for the first retrieval,
which is what keeps the answer-with-citations contract a property of *what
was retrieved* regardless of who retrieved it (D5). ``search_web`` populates
``external_refs`` instead of ``citations`` and never touches the context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool

from llmwiki.models.plan import Citation, ExternalRef
from llmwiki.wiki import gists as gists_mod
from llmwiki.wiki.pages import read_page

if TYPE_CHECKING:
    from llmwiki.agent.query import QueryAgent

logger = logging.getLogger(__name__)

ANSWER_ACTION = "answer"
WEB_TOOL = "search_web"


@dataclass
class ToolResult:
    """What one tool call produced, split by who consumes it."""

    observation: str
    context_block: str = ""
    citations: list[Citation] = field(default_factory=list)
    external_refs: list[ExternalRef] = field(default_factory=list)


def build_tools(agent: QueryAgent) -> list[BaseTool]:
    """Construct the tool set for one agent. ``search_web`` only if a searcher exists."""

    def _scopes(domain: str) -> list:
        # A domain the model names is used as given (unknown → the tool says
        # so, plan §21.2 A6); an empty string means the run's own scopes. A
        # plain ``str`` (not Optional) keeps the action schema a simple enum-
        # free string field that small models fill correctly.
        if domain:
            from llmwiki.wiki.domains import DomainScope, require_domain

            return [DomainScope(require_domain(agent.registry, domain))]
        return list(agent._run_scopes)

    def search_wiki(query: str, k: int = 5, domain: str = "") -> ToolResult:
        """Search the compiled wiki (page gists) for a different phrasing of the question.
        Returns the matching pages' bodies. `domain` restricts the search to one domain."""
        vector = agent.embedder.embed([query])[0]
        hits = agent.retrieve_layer("gists", query, vector, _scopes(domain), k)
        block, citations = agent._build_context(hits, [])
        if not block:
            return ToolResult(observation="No wiki page matched that query.")
        return ToolResult(observation=block, context_block=block, citations=citations)

    def search_chunks(query: str, k: int = 5, domain: str = "") -> ToolResult:
        """Search the raw source chunks (verbatim text) for a specific figure, quote or
        detail that a synthesized wiki page would not preserve. `domain` restricts it."""
        vector = agent.embedder.embed([query])[0]
        hits = agent.retrieve_layer("chunks", query, vector, _scopes(domain), k)
        block, citations = agent._build_context([], hits)
        if not block:
            return ToolResult(observation="No source chunk matched that query.")
        return ToolResult(observation=block, context_block=block, citations=citations)

    def get_page(slug: str, domain: str = "general") -> ToolResult:
        """Read one wiki page in full by its slug - use it to follow a [[wikilink]] seen in
        an already-retrieved page. `domain` is the domain the linking page was in."""
        from llmwiki.wiki.domains import require_domain

        domain = require_domain(agent.registry, domain or None)
        manifest = gists_mod.load_gists(agent.store, domain)
        gist = manifest.get(slug)
        page = read_page(agent.store, slug, gist.type if gist else "concept", domain)
        if page is None:
            return ToolResult(observation=f"No wiki page with slug {slug!r}.")
        where = "" if domain == "general" else f" (domain: {domain})"
        block = f"## Wiki page [[{slug}]]{where}\n\n{page.body}"
        citations = [
            Citation(source_id=source_id, title=page.front_matter.title, slug=slug,
                     domain=domain)
            for source_id in page.front_matter.sources
        ]
        return ToolResult(observation=block, context_block=block, citations=citations)

    tools: list[BaseTool] = [
        StructuredTool.from_function(search_wiki, name="search_wiki"),
        StructuredTool.from_function(search_chunks, name="search_chunks"),
        StructuredTool.from_function(get_page, name="get_page"),
    ]

    if agent.web_searcher is not None:
        searcher = agent.web_searcher

        def search_web(query: str) -> ToolResult:
            """Search the public web. Only when the knowledge base clearly lacks the
            topic. Results are shown to the reader as external and are never citations."""
            refs = searcher.search(query, k=5)
            if not refs:
                return ToolResult(observation="The web search returned nothing.")
            listing = "\n".join(f"- {r.title or r.url} <{r.url}>: {r.snippet}" for r in refs)
            return ToolResult(
                observation=f"External results (not in the knowledge base):\n{listing}",
                external_refs=refs,
            )

        tools.append(StructuredTool.from_function(search_web, name=WEB_TOOL))

    return tools


def offered_tools(
    tools: list[BaseTool], *, used_rag_fallback: bool, web_calls: int, policy: str, max_web: int
) -> list[BaseTool]:
    """Apply the web-search policy. Every other tool is always offered.

    The gate lives here, where the action schema is built, so the model can
    never pick a tool the policy withholds - a code guarantee, not a prompt
    request (design §4.9).
    """
    offered: list[BaseTool] = []
    for tool in tools:
        if tool.name != WEB_TOOL:
            offered.append(tool)
            continue
        if policy == "off" or web_calls >= max_web:
            continue
        if policy == "weak" and not used_rag_fallback:
            continue
        offered.append(tool)
    return offered


def action_schema(tools: list[BaseTool]) -> dict:
    """The forced-output schema for one ``agent_step`` call.

    ``action`` is an enum over the offered tool names plus ``answer``; ``args``
    is the union of every offered tool's argument schema, so one schema serves
    whichever tool the model picks. The per-tool argument names are also
    listed in the prompt (``describe_tools``) so the model knows which apply.
    """
    properties: dict[str, Any] = {}
    for tool in tools:
        for name, spec in tool.args.items():
            properties.setdefault(name, spec)
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [t.name for t in tools] + [ANSWER_ACTION]},
            "args": {"type": "object", "properties": properties},
            "reason": {"type": "string"},
        },
        "required": ["action", "reason"],
    }


def describe_tools(tools: list[BaseTool]) -> str:
    """The tool listing shown to the model in the ``agent_step`` prompt."""
    lines = []
    for tool in tools:
        args = ", ".join(f"{n}: {s.get('type', 'any')}" for n, s in tool.args.items())
        description = " ".join(tool.description.split())
        lines.append(f"- {tool.name}({args}): {description}")
    lines.append(f"- {ANSWER_ACTION}: stop gathering evidence and answer from what was retrieved")
    return "\n".join(lines)


def dispatch(tools: list[BaseTool], name: str, args: dict) -> ToolResult:
    """Run one tool by name with the model's arguments, never raising.

    Argument validation is LangChain's (``args_schema``); a bad argument set
    or a tool failure becomes an observation the model can react to, the same
    posture as a malformed skill choice in R5.
    """
    by_name: dict[str, BaseTool] = {t.name: t for t in tools}
    tool = by_name.get(name)
    if tool is None:
        return ToolResult(observation=f"No such tool {name!r}.")
    try:
        result = tool.invoke(args)
    except Exception as exc:  # validation error or backend failure
        logger.warning("query graph: tool %s(%r) failed: %s", name, args, exc)
        return ToolResult(observation=f"{name} failed: {exc}")
    if not isinstance(result, ToolResult):  # a foreign tool that returned text
        return ToolResult(observation=str(result))
    return result
