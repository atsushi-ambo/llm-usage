from llm_usage.pricing import estimate_cost, lookup_price


def test_lookup_sonnet():
    p = lookup_price("claude-sonnet-4-6")
    assert p is not None
    assert p.input_per_m == 3.0


def test_estimate_cost():
    cost = estimate_cost("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0)
    assert cost == 3.0


def test_synthetic_ignored():
    assert lookup_price("<synthetic>") is None
