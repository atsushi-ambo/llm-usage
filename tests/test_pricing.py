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


def test_short_key_requires_word_boundary():
    # "o1" is a real price key; it should match a real o1 model id...
    assert lookup_price("o1-preview") is not None
    # ...but not fire on an unrelated id that merely contains "o1" as a
    # substring with no separator around it.
    assert lookup_price("gpt-4o15-fake") is None


def test_longest_match_wins_with_boundaries():
    p = lookup_price("claude-3-5-sonnet-20241022")
    assert p is not None
    assert p.input_per_m == 3.0
    assert p.cache_read_per_m == 0.30
