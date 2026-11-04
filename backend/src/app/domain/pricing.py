"""Versioned model price table used only to ESTIMATE cost; provider billing is the source of truth."""
from dataclasses import dataclass

# Bump on every change to PRICES. Recorded next to each estimate so old estimates stay explainable.
PRICING_VERSION = '2026-11-04.1'


@dataclass(frozen=True, slots=True)
class Price:
    """USD per 1M tokens. `cached_input=None` means cached tokens are billed at the input price."""
    input: float
    output: float
    cached_input: float | None = None
    reference_date: str = ''


# Reference values; confirm at https://developers.openai.com/api/docs/pricing before trusting any estimate.
# gpt-6-luna: standard tier as listed on 2026-10-17 (long prompts >272k tokens bill higher, not modelled).
# Models absent from this table yield `None` ("unknown cost"), never 0. Add the entry and bump PRICING_VERSION.
PRICES: dict[str, Price] = {
    'gpt-6-luna': Price(input=0.10, output=0.50, cached_input=0.01, reference_date='2026-10-17'),
    'text-embedding-3-small': Price(input=0.02, output=0.0, reference_date='2025-01'),
    'gpt-4o-mini': Price(input=0.15, output=0.60, cached_input=0.075, reference_date='2025-01'),
}


def estimate_cost_usd(model: str, usage: dict | None) -> float | None:
    """Estimated USD cost of one call, or None when the model has no price or usage is incomplete."""
    price = PRICES.get(model)
    if not price or not usage:
        return None
    tokens_in = usage.get('input_tokens', usage.get('prompt_tokens'))
    tokens_out = usage.get('output_tokens', usage.get('completion_tokens'))
    if tokens_in is None or (tokens_out is None and price.output):
        return None
    details = usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
    cached = min(details.get('cached_tokens') or 0, tokens_in)
    cached_price = price.input if price.cached_input is None else price.cached_input
    cost = (tokens_in - cached) * price.input + cached * cached_price + (tokens_out or 0) * price.output
    return cost / 1_000_000
