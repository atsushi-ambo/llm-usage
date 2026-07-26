from llm_usage.pricing import PRICES_AS_OF, estimate_cost, lookup_price


def test_prices_as_of_is_iso_date():
    assert len(PRICES_AS_OF) == 10
    assert PRICES_AS_OF[4] == "-" and PRICES_AS_OF[7] == "-"


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


# Current Anthropic model ids must resolve to specific list prices — not just
# "some price". The previous suite only checked non-None, which let Opus 4.6+
# silently fall through to the legacy claude-opus-4 $15/$75 row (~3× too high).
_CURRENT_MODEL_PRICES: list[tuple[str, float, float, float | None, float | None]] = [
    # model_id, input, output, cache_read, cache_write
    ("claude-opus-5", 5.0, 25.0, 0.50, 6.25),
    ("claude-opus-4-8", 5.0, 25.0, 0.50, 6.25),
    ("claude-opus-4-7", 5.0, 25.0, 0.50, 6.25),
    ("claude-opus-4-6", 5.0, 25.0, 0.50, 6.25),
    ("claude-opus-4-5", 5.0, 25.0, 0.50, 6.25),
    ("claude-opus-4-1", 15.0, 75.0, 1.50, 18.75),
    ("claude-opus-4", 15.0, 75.0, 1.50, 18.75),
    ("claude-opus-4-20250514", 15.0, 75.0, 1.50, 18.75),
    ("claude-fable-5", 10.0, 50.0, 1.0, 12.50),
    ("claude-fable-5-20260609", 10.0, 50.0, 1.0, 12.50),
    ("claude-sonnet-5", 3.0, 15.0, 0.30, 3.75),
    ("claude-sonnet-4-6", 3.0, 15.0, 0.30, 3.75),
    ("claude-haiku-4-5", 1.0, 5.0, 0.10, 1.25),
]


def test_current_model_ids_resolve_to_expected_prices():
    for model, inp, out, cache_r, cache_w in _CURRENT_MODEL_PRICES:
        p = lookup_price(model)
        assert p is not None, f"{model}: no price"
        assert p.input_per_m == inp, f"{model}: input {p.input_per_m} != {inp}"
        assert p.output_per_m == out, f"{model}: output {p.output_per_m} != {out}"
        assert p.cache_read_per_m == cache_r, f"{model}: cache_read"
        assert p.cache_write_per_m == cache_w, f"{model}: cache_write"


def test_opus_46_estimate_not_legacy_triple():
    # 1M input + 1M output at modern rates = $5 + $25 = $30, not $15+$75=$90.
    cost = estimate_cost(
        "claude-opus-4-6",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == 30.0
