"""Domain routing: which domain a source is filed under, and which a question should search.

Phase 2 (design v1.4 §4.10.1, plan §21.2 A4-A6). One forced-schema call,
``op="route_domain"``, routed to the cheapest model. The router never invents
a domain: the schema's enum is the registry, and a poor fit lands in
``general`` with the model's ``suggested_domain`` recorded for a human to act
on. Callers short-circuit *before* reaching this module whenever no call is
needed (an explicit ``domain=``, a registry with only ``general``, or
``DOMAIN_ROUTING=off``), so a single-domain wiki pays nothing here.
"""

from __future__ import annotations

import logging

from llmwiki.chains.prompts_loader import load_prompt
from llmwiki.llm.base import LLMClient
from llmwiki.models.page import DomainRegistry
from llmwiki.models.plan import CostRecord
from llmwiki.models.source import DomainAssignment, ExtractedDoc
from llmwiki.storage.layout import GENERAL, check_domain, slugify

logger = logging.getLogger(__name__)

#: How much of the extracted text the source router sees - the title plus the
#: opening is enough for a curated registry, and keeps the call cheap.
ROUTE_HEAD_CHARS = 1_500


def _registry_listing(registry: DomainRegistry) -> str:
    lines = []
    for name in registry.names():
        if name == GENERAL:
            continue
        lines.append(f"- {name}: {registry.domains[name].description or '(no description)'}")
    lines.append(f"- {GENERAL}: anything that fits none of the above")
    return "\n".join(lines)


def _source_schema(names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "enum": names},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "suggested_domain": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["domain", "confidence"],
    }


def _query_schema(names: list[str], max_domains: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "domains": {
                "type": "array",
                "items": {"type": "string", "enum": names},
                "minItems": 1,
                "maxItems": max_domains,
            },
        },
        "required": ["domains"],
    }


class DomainRouter:
    """Routes sources and questions against one registry snapshot."""

    def __init__(
        self, llm: LLMClient, registry: DomainRegistry, *, min_confidence: float = 0.6,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.min_confidence = min_confidence

    def route_source(self, doc: ExtractedDoc) -> tuple[DomainAssignment, list[CostRecord]]:
        """One call. Returns the decision and the usage record(s) it cost.

        Anything the model returns that is not a registered name, or that it
        is not confident about, becomes ``general`` - the model's own pick is
        kept as ``suggested_domain`` so nothing is silently lost.
        """
        names = self.registry.names()
        prompt = (
            "# Registered domains\n\n" + _registry_listing(self.registry)
            + f"\n\n# Source\n\nTitle: {doc.title or '(untitled)'}\n"
            + (f"URL: {doc.url}\n" if doc.url else "")
            + f"\n{doc.text[:ROUTE_HEAD_CHARS]}\n"
        )
        response = self.llm.complete(
            op="route_domain",
            system=load_prompt("route_domain_source"),
            prompt=prompt,
            schema=_source_schema(names),
        )
        data = response.data or {}
        picked = str(data.get("domain") or GENERAL)
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        suggested = _clean_suggestion(str(data.get("suggested_domain") or ""), self.registry)
        reason = str(data.get("reason") or "")

        if picked != GENERAL and not self.registry.has(picked):
            # An unregistered name is a suggestion, never a destination.
            suggested = suggested or _clean_suggestion(picked, self.registry)
            reason = reason or f"model named unregistered domain {picked!r}"
            picked = GENERAL
        elif picked != GENERAL and confidence < self.min_confidence:
            suggested = suggested or picked
            reason = reason or f"confidence {confidence:.2f} below {self.min_confidence:.2f}"
            picked = GENERAL

        assignment = DomainAssignment(
            domain=picked, confidence=confidence, suggested_domain=suggested, reason=reason,
        )
        logger.info("route_domain: source_id=%s -> %s (confidence=%.2f suggested=%r)",
                    doc.source_id, picked, confidence, suggested)
        return assignment, [response.usage] if response.usage is not None else []

    def route_query(
        self, question: str, *, max_domains: int = 2,
    ) -> tuple[list[str], list[CostRecord]]:
        """Which registered domains to search for ``question``, most relevant first.

        Returns an empty list (meaning "search everything") when the model
        names nothing usable, so a bad decision costs recall, never an answer.
        """
        names = self.registry.names()
        prompt = (
            "# Registered domains\n\n" + _registry_listing(self.registry)
            + f"\n\n# Question\n\n{question}\n"
        )
        response = self.llm.complete(
            op="route_domain",
            system=load_prompt("route_domain_query"),
            prompt=prompt,
            schema=_query_schema(names, max_domains),
        )
        raw = (response.data or {}).get("domains") or []
        chosen: list[str] = []
        for name in raw:
            if isinstance(name, str) and self.registry.has(name) and name not in chosen:
                chosen.append(name)
        chosen = chosen[:max_domains]
        logger.debug("route_domain(query): %r -> %s", question[:60], chosen)
        return chosen, [response.usage] if response.usage is not None else []


def _clean_suggestion(text: str, registry: DomainRegistry) -> str:
    """Normalise a suggested name to a valid slug; drop it if it is registered or reserved."""
    if not text.strip():
        return ""
    candidate = slugify(text)[:32].strip("-")
    if not candidate or registry.has(candidate):
        return ""
    try:
        check_domain(candidate, allow_general=False)
    except ValueError:
        return ""
    return candidate
