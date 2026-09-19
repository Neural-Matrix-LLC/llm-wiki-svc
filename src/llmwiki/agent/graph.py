"""The query agent as a LangGraph ``StateGraph`` (Phase 1-D, design §4.9).

    retrieve ──(empty)──► no_answer ──► END
       │
       ├──(AGENT_MAX_TOOL_CALLS == 0)──► select_skills
       │
       ▼
     agent ◄──────────┐          one complete(op="agent_step", schema=ACTION) per pass
       │              │
       ├─(tool)──► tools          dispatch → ToolMessage; merge context + citations
       │
       └─(answer | cap | budget)──► select_skills ──► generate ──► resolve_citations ──► END

LangGraph is orchestration only (decision D1): every model call goes through
``LLMClient.complete``, never a ``BaseChatModel``, so routing, cost
accounting, the offline doubles and ``test_layering.py`` are all untouched.
The three bounds - ``AGENT_MAX_TOOL_CALLS``, the shared ``MAX_CONTEXT_CHARS``
budget, and the graph's ``recursion_limit`` - are enforced here in code, never
requested of the model (D4). ``retrieve`` is the pre-graph wiki-first
retrieval verbatim, so wiki-first stays a code guarantee too (D3).

Nodes are closures over one :class:`~llmwiki.agent.query.QueryAgent` and
reuse its methods; the graph is compiled once per agent
(``QueryAgent.graph``). State is a plain ``TypedDict`` - the only reducer is
``add_messages`` on the ReAct transcript, which exists so the LangSmith trace
has the canonical tool-call shape (D2/D9).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from llmwiki.agent import toolkit
from llmwiki.agent.skills import Skill, discover_skills
from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.models.chunk import SearchHit
from llmwiki.models.plan import AgentStep, Answer, Citation, ExternalRef

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from llmwiki.agent.query import QueryAgent

logger = logging.getLogger(__name__)

NO_ANSWER_TEXT = "Nothing in the knowledge base addresses that question yet."
# A decision call that yields no usable action is retried once with a nudge
# before the loop ends - the same retry-once posture as R5's skill selection.
STEP_ATTEMPTS = 2
EXTERNAL_HEADING = (
    "## External web results (NOT in the knowledge base - present as external, never cite)"
)


class QueryState(TypedDict, total=False):
    """Everything one ``answer()`` accumulates. ``total=False``: nodes return deltas."""

    query: str
    k: int
    # Phase 2: the domains this answer searches (plan §21.2 A6), by name.
    scopes: list[str]
    # retrieve
    wiki_hits: list[SearchHit]
    chunk_hits: list[SearchHit]
    used_rag_fallback: bool
    context: str
    citations: dict[str, Citation]
    budget_left: int
    # the bounded tool loop
    messages: Annotated[list[BaseMessage], add_messages]
    tool_calls_made: int
    web_calls: int
    seen_calls: list[str]
    notes: list[str]
    steps: list[AgentStep]
    external_refs: list[ExternalRef]
    stop_reason: str
    # answer
    skills: dict[str, Skill]
    chosen_skills: list[str] | None
    text: str
    answer: Answer


def recursion_limit(max_tool_calls: int) -> int:
    """The graph's hard stop: every node execution is one step, so a full run
    is ``2n + 5`` with ``n`` tool calls; ``2n + 8`` leaves room and no more."""
    return 2 * max_tool_calls + 8


def _call_key(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


def build_query_graph(agent: QueryAgent) -> CompiledStateGraph:
    """Compile the graph for one agent. Called once, from ``QueryAgent.graph``."""
    settings = agent.settings
    tools = toolkit.build_tools(agent)

    # --- nodes ---------------------------------------------------------------

    def retrieve(state: QueryState) -> dict[str, Any]:
        """The pre-graph wiki-first retrieval, verbatim (D3), over the run's scopes.

        The gate reads the dense cosine (``gate_score``), so hybrid retrieval
        and reranking (P2-B) change *which* pages are read, never *whether*
        the chunk fallback runs.
        """
        from llmwiki.agent.retrieval import gate_score
        from llmwiki.wiki.domains import DomainScope

        query, k = state["query"], state.get("k", 5)
        scopes = [DomainScope(name) for name in state.get("scopes") or []] or agent._run_scopes
        vector = agent.embedder.embed([query])[0]
        wiki_hits = agent.retrieve_layer("gists", query, vector, scopes, k)
        strong = [hit for hit in wiki_hits if gate_score(hit) >= agent.wiki_confidence]

        used_fallback = False
        chunk_hits: list[SearchHit] = []
        if not strong:
            used_fallback = True
            chunk_hits = agent.retrieve_layer("chunks", query, vector, scopes, k)

        context, citations = agent._build_context(strong or wiki_hits, chunk_hits)
        return {
            "wiki_hits": wiki_hits,
            "chunk_hits": chunk_hits,
            "used_rag_fallback": used_fallback,
            "context": context,
            "citations": {c.source_id: c for c in citations},
            "budget_left": max(agent.max_context_chars - len(context), 0),
            "tool_calls_made": 0,
            "web_calls": 0,
            "seen_calls": [],
            "notes": [],
            "steps": [],
            "external_refs": [],
            "stop_reason": "",
        }

    def no_answer(state: QueryState) -> dict[str, Any]:
        return {
            "answer": Answer(
                text=NO_ANSWER_TEXT, citations=[],
                used_rag_fallback=state.get("used_rag_fallback", False),
            )
        }

    def agent_step(state: QueryState) -> dict[str, Any]:
        """One tool-or-answer decision. Skips the call entirely when a bound is hit."""
        made = state.get("tool_calls_made", 0)
        if made >= settings.agent_max_tool_calls:
            logger.debug(
                "agent_step: stop - tool-call cap (%d) reached", settings.agent_max_tool_calls,
            )
            return {"stop_reason": f"tool-call cap ({settings.agent_max_tool_calls}) reached"}
        if state.get("budget_left", 0) <= 0:
            logger.debug("agent_step: stop - context budget exhausted")
            return {"stop_reason": "context budget exhausted"}

        offered = toolkit.offered_tools(
            tools,
            used_rag_fallback=state.get("used_rag_fallback", False),
            web_calls=state.get("web_calls", 0),
            policy=settings.agent_web_search_policy,
            max_web=settings.agent_max_web_searches,
        )
        prompt = _render_step_prompt(state, offered, settings.agent_max_tool_calls,
                                     registry=agent.registry)
        schema = toolkit.action_schema(offered)
        valid = {t.name for t in offered} | {toolkit.ANSWER_ACTION}
        action: Any = None
        args: dict = {}
        reason = ""
        for attempt in range(1, STEP_ATTEMPTS + 1):
            response = agent.llm.complete(
                op="agent_step", system=load_prompt("agent_step"), prompt=prompt, schema=schema,
            )
            data = response.data or {}
            action = data.get("action")
            raw_args = data.get("args")
            args = dict(raw_args) if isinstance(raw_args, dict) else {}
            reason = str(data.get("reason") or "")
            if action in valid:
                break
            # Prose instead of the forced tool call - seen with a small model
            # on a long prompt. One nudge, then the loop ends (R5 posture).
            logger.warning(
                "agent_step: attempt %d/%d gave no usable action (raw: %.200r)",
                attempt, STEP_ATTEMPTS, response.text or data,
            )
            prompt += (
                "\n\n# Reminder\n\nReply only through the structured action - "
                "no prose. Choose one listed action, or `answer`."
            )

        if action not in {t.name for t in offered}:
            # "answer", or nothing usable: either way the loop ends. Never a
            # hard failure on a user-facing query - same posture as R5.
            stop = "answer" if action == toolkit.ANSWER_ACTION else "invalid action"
            logger.debug("agent_step: stop - %s (%s)", stop, reason)
            return {"stop_reason": stop, "messages": [AIMessage(content=reason)]}

        call = {"name": str(action), "args": args, "id": f"call_{made + 1}", "type": "tool_call"}
        logger.debug("agent_step: %s(%s) - %s", action, args, reason)
        return {"messages": [AIMessage(content=reason, tool_calls=[call])]}

    def run_tools(state: QueryState) -> dict[str, Any]:
        """Execute the one tool call the last AIMessage asked for; merge its result."""
        last = state["messages"][-1]
        assert isinstance(last, AIMessage) and last.tool_calls
        call = last.tool_calls[0]
        name, args = call["name"], dict(call["args"] or {})

        seen = list(state.get("seen_calls", []))
        key = _call_key(name, args)
        if key in seen:
            result = toolkit.ToolResult(
                observation=f"{name} was already called with these arguments; "
                "choose a different action or answer."
            )
        else:
            result = toolkit.dispatch(tools, name, args)
        seen.append(key)

        budget = state.get("budget_left", 0)
        block = result.context_block[:budget] if budget > 0 else ""
        if result.context_block and not block:
            result.observation = "Context budget exhausted; answer from what has been retrieved."
        elif len(block) < len(result.context_block):
            result.observation = block + "\n\n[truncated: context budget reached]"

        citations = dict(state.get("citations", {}))
        for citation in result.citations:
            citations.setdefault(citation.source_id, citation)

        notes = list(state.get("notes", []))
        if not block:  # an observation that did not become context still matters next step
            notes.append(f"{name}({json.dumps(args, default=str)}): {result.observation[:400]}")

        context = state.get("context", "")
        if block:
            context = f"{context}\n\n{block}" if context else block

        return {
            "messages": [ToolMessage(content=result.observation, tool_call_id=call["id"])],
            "context": context,
            "budget_left": max(budget - len(block), 0),
            "citations": citations,
            "external_refs": list(state.get("external_refs", [])) + list(result.external_refs),
            "steps": list(state.get("steps", []))
            + [AgentStep(tool=name, args=args, chars=len(block))],
            "tool_calls_made": state.get("tool_calls_made", 0) + 1,
            "web_calls": state.get("web_calls", 0) + (1 if name == toolkit.WEB_TOOL else 0),
            "seen_calls": seen,
            "notes": notes,
        }

    def select_skills(state: QueryState) -> dict[str, Any]:
        skills = discover_skills(settings.agent_skills_dir)
        if not skills:
            return {"skills": {}, "chosen_skills": None}
        return {"skills": skills, "chosen_skills": agent._select_skills(state["query"], skills)}

    def generate(state: QueryState) -> dict[str, Any]:
        query = state["query"]
        context = state.get("context", "")
        refs = state.get("external_refs", [])
        if refs:
            listing = "\n".join(f"- {r.title or r.url} <{r.url}>: {r.snippet}" for r in refs)
            context = f"{context}\n\n{EXTERNAL_HEADING}\n\n{listing}"

        skills, chosen = state.get("skills") or {}, state.get("chosen_skills")
        if skills and chosen:
            text = agent._run_skill_chain(query, context, skills, chosen)
        elif skills:
            logger.warning(
                "answer_query: skill selection named nothing valid; "
                "falling back to the fixed answer_query skill",
            )
            text = agent._answer_with_fixed_prompt(query, context)
        else:
            text = agent._answer_with_fixed_prompt(query, context)
        return {"text": text}

    def resolve_citations(state: QueryState) -> dict[str, Any]:
        resolved = [
            c for c in state.get("citations", {}).values() if agent.source_exists(c.source_id)
        ]
        return {
            "answer": Answer(
                text=state.get("text", ""),
                citations=resolved,
                used_rag_fallback=state.get("used_rag_fallback", False),
                steps=list(state.get("steps", [])),
                context=state.get("context", ""),
                external_refs=list(state.get("external_refs", [])),
                domains=list(state.get("scopes") or []),
            )
        }

    # --- edges ---------------------------------------------------------------

    def route_after_retrieve(state: QueryState) -> Literal["no_answer", "agent", "select_skills"]:
        if not state.get("context", "").strip():
            return "no_answer"
        if settings.agent_max_tool_calls <= 0:
            return "select_skills"
        return "agent"

    def route_after_agent(state: QueryState) -> Literal["tools", "select_skills"]:
        if state.get("stop_reason"):
            return "select_skills"
        last = state["messages"][-1] if state.get("messages") else None
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "select_skills"

    graph: StateGraph = StateGraph(QueryState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("no_answer", no_answer)
    graph.add_node("agent", agent_step)
    graph.add_node("tools", run_tools)
    graph.add_node("select_skills", select_skills)
    graph.add_node("generate", generate)
    graph.add_node("resolve_citations", resolve_citations)

    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", route_after_retrieve)
    graph.add_edge("no_answer", END)
    graph.add_conditional_edges("agent", route_after_agent)
    graph.add_edge("tools", "agent")
    graph.add_edge("select_skills", "generate")
    graph.add_edge("generate", "resolve_citations")
    graph.add_edge("resolve_citations", END)
    return graph.compile()


def _render_step_prompt(
    state: QueryState, offered: list, max_calls: int, registry: Any = None,
) -> str:
    """What the model sees when deciding the next action."""
    steps = state.get("steps", [])
    made = state.get("tool_calls_made", 0)
    log = "\n".join(
        f"- {s.tool}({json.dumps(s.args, default=str)}) -> {s.chars} chars added" for s in steps
    ) or "- none yet"
    notes = "\n".join(f"- {n}" for n in state.get("notes", [])) or "- none"
    context = state.get("context", "")
    return (
        f"# Question\n\n{state['query']}\n\n"
        + _render_domains(state, registry)
        + f"# Evidence retrieved so far ({len(context)} chars; "
        f"{state.get('budget_left', 0)} chars of budget left)\n\n{context}\n\n"
        f"# Tool calls made so far ({made} of {max_calls})\n\n{log}\n\n"
        f"# Tool notes (results that added no evidence)\n\n{notes}\n\n"
        f"# Available actions\n\n{toolkit.describe_tools(offered)}"
    )


def _render_domains(state: QueryState, registry: Any) -> str:
    """Tell the model which domains exist and which this run is searching (Phase 2).

    Omitted entirely for a general-only registry, so the pre-Phase-2 prompt is
    unchanged there.
    """
    if registry is None or registry.is_general_only:
        return ""
    searching = ", ".join(state.get("scopes") or ["general"])
    listing = "\n".join(
        f"- {name}: {registry.domains[name].description or '(no description)'}"
        for name in registry.names() if name != "general"
    )
    return (
        f"# Domains\n\nSearching: {searching}. Registered domains (pass `domain` to a search "
        f"tool to look in one specifically; `general` holds everything else):\n{listing}\n\n"
    )
