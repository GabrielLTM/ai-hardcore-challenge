import pytest
from app.domain.pricing import PRICES, PRICING_VERSION, estimate_cost_usd


def test_embedding_cost_uses_input_tokens_only():
    assert estimate_cost_usd('text-embedding-3-small', {'prompt_tokens': 1_000_000}) == pytest.approx(0.02)


def test_cached_tokens_are_billed_at_cached_price():
    usage = {'input_tokens': 1_000_000, 'output_tokens': 1_000_000, 'input_tokens_details': {'cached_tokens': 400_000}}
    price = PRICES['gpt-4o-mini']
    expected = 0.6 * price.input + 0.4 * price.cached_input + price.output
    assert estimate_cost_usd('gpt-4o-mini', usage) == pytest.approx(expected)


@pytest.mark.parametrize('model, usage', [
    ('modelo-sem-preco', {'input_tokens': 10, 'output_tokens': 5}),  # no price in the table
    ('modelo-inexistente', {'input_tokens': 10, 'output_tokens': 5}),
    ('gpt-4o-mini', None),
    ('gpt-4o-mini', {}),
    ('gpt-4o-mini', {'output_tokens': 5}),  # input missing
    ('gpt-4o-mini', {'input_tokens': 5}),  # priced output missing: partial, so unknown
])
def test_unknown_or_incomplete_is_none_never_zero(model, usage):
    assert estimate_cost_usd(model, usage) is None


def test_pricing_version_is_declared():
    assert PRICING_VERSION
