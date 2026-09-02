"""USD rates per million tokens, keyed by model id.

Cost is measured, never estimated - the ledger in ``wiki/_meta/cost.jsonl`` is
what the project's cost guardrails are built on, so a made-up number there is
worse than no number.  A model absent from :data:`RATES` therefore records its
real token counts with ``cost_usd = 0.0``: a zero that means "not priced here",
not "free".  Add the model to the table rather than inventing a default.

Only the Anthropic rates are listed today because those are the only ones this
repository has ever billed against.  The table is deliberately provider-neutral
so the LangChain-backed providers can be priced as they get used.

Anthropic bills cache reads at 0.1x input and cache writes at 1.25x.  Providers
without a cache tier report zero in those fields, so one formula covers all.
"""

from __future__ import annotations

from llmwiki.models.plan import CostRecord

RATES: dict[str, tuple[float, float]] = {
    # --- Anthropic --- (input, output) USD per million tokens
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
}


def price(model: str, usage: CostRecord, *, fallback: str | None = None) -> float:
    """Measured cost of one call in USD, or ``0.0`` for an unpriced model.

    ``usage.input_tokens`` must already exclude the cached tokens, which are
    billed separately at their own multipliers.  ``fallback`` names a model
    whose rates stand in for an unknown id; used only where every model comes
    from one provider whose prices sit in the same band.
    """
    rates = RATES.get(model)
    if rates is None and fallback is not None:
        rates = RATES.get(fallback)
    if rates is None:
        return 0.0
    rate_in, rate_out = rates
    return (
        usage.input_tokens * rate_in
        + usage.cache_read_tokens * rate_in * 0.1
        + usage.cache_write_tokens * rate_in * 1.25
        + usage.output_tokens * rate_out
    ) / 1_000_000
