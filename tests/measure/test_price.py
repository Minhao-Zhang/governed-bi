"""What ``measure/price.py`` must do, asserted on effects.

Authoring rules, from ``tests/conformance/test_register_closure.py`` and
``docs/lessons-from-v1.md`` §7:

* Assert on the **effect** — does the cost come out unmeasured, does the arithmetic
  land on the right number — never on the presence of a constant.
* **Never assert a module against its own constant.** No test here reads a rate out
  of ``PRICE_TABLE`` and multiplies it: every expected number is a literal computed
  by hand in the comment above it, against rates this file chose. A test that
  re-derived the arithmetic from the table would pass with the table empty, and would
  keep passing if the multiplication were inverted.
* **Drive the real function.** Nothing here reimplements pricing.

The rate fixtures below are deliberately *not* real prices. Real ones would tie the
arithmetic assertions to a table that is supposed to change when providers reprice,
and the point of a hand-computed literal is that it does not move.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from governed_bi.measure.price import (
    PRICE_TABLE,
    PRICE_VALIDITY_DAYS,
    Price,
    estimate_cost,
    estimate_run_cost,
    price_age_days,
)
from governed_bi.register.quantity import Measured, NotMeasured

OBSERVED = date(2026, 1, 1)
PRICED_ON = date(2026, 1, 15)

#: $3.00 / $15.00 / $0.30 per million. Round numbers so the expected costs below can
#: be checked with a calculator by a reader who does not trust the test.
FIXTURE = {
    "fixture-model": Price(
        "fixture-model",
        Measured.of(3.00), Measured.of(15.00), Measured.of(0.30),
        "USD", OBSERVED.isoformat(), "tests/measure/test_price.py",
    )
}

#: One million input, half a million output, nothing cached — chosen so every
#: expected figure is exact in binary-free arithmetic: $1.00 in, $1.00 out.
ROUND_RATES = {
    "round-model": Price(
        "round-model",
        Measured.of(1.00), Measured.of(2.00), Measured.of(0.10),
        "USD", OBSERVED.isoformat(), "tests/measure/test_price.py",
    )
}
ROUND_USAGE = {"input_tokens": 1_000_000, "output_tokens": 500_000, "cache_read_tokens": 0}

#: The shape a real record has on disk, from
#: ``runs/datalake/langsmith-debug/20260802T164111Z/generations.baseline.jsonl``.
REAL_USAGE = {
    "input_tokens": 9601,
    "output_tokens": 1411,
    "total_tokens": 11012,
    "cache_read_tokens": 3986,
}


# ── 1. an unknown model is never zero, and never renders as a number ───────────


def test_unknown_model_yields_no_number_at_all() -> None:
    """The single most important behaviour in the module.

    v1 returned ``None`` here, which two ladders then reported as no USD; the
    failure before that returned a stale number. Asserting on ``render()`` rather
    than on the state is deliberate — the artifact a reader quotes contains the
    rendered string, and the property that matters is that no digit in it can be
    mistaken for a cost. The probe model id is digit-free so that a digit in the
    output could only have come from a fabricated value.
    """
    cost = estimate_cost("unpriced-model", REAL_USAGE, asof=PRICED_ON)

    assert not cost.is_measured
    rendered = cost.render()
    assert not any(char.isdigit() for char in rendered), rendered
    assert "unpriced-model" in rendered
    with pytest.raises(NotMeasured):
        _ = cost.value


def test_unknown_model_is_not_rescued_by_a_prefix_match() -> None:
    """v1 fell back to ``model.startswith(key)``. A dated or suffixed variant is a
    different model at a possibly different price, and guessing is the failure this
    file exists to prevent."""
    known = next(iter(PRICE_TABLE))
    cost = estimate_cost(f"{known}-some-unknown-variant", REAL_USAGE, asof=date(2026, 8, 3))
    assert not cost.is_measured


# ── 2. partial usage is not zero usage ────────────────────────────────────────


def test_missing_output_tokens_makes_the_total_unmeasured_and_names_the_field() -> None:
    partial = {"input_tokens": 9601, "cache_read_tokens": 3986}
    cost = estimate_cost("fixture-model", partial, asof=PRICED_ON, table=FIXTURE)

    assert not cost.is_measured
    assert "output_tokens" in cost.why, cost.why
    assert "output_tokens" in cost.render()


def test_a_usage_record_of_zeros_is_not_a_free_call() -> None:
    """v1 priced this shape at a *measured* ``0.0`` on live turns making two real
    model calls, and it then passed every null check downstream."""
    cost = estimate_cost(
        "fixture-model",
        {"input_tokens": 0, "output_tokens": 0},
        asof=PRICED_ON,
        table=FIXTURE,
    )
    assert not cost.is_measured


def test_a_non_integer_token_count_is_not_coerced() -> None:
    cost = estimate_cost(
        "fixture-model",
        {"input_tokens": "9601", "output_tokens": 1411},
        asof=PRICED_ON,
        table=FIXTURE,
    )
    assert not cost.is_measured
    assert "input_tokens" in cost.why


# ── 3. the arithmetic, against a hand-computed literal ────────────────────────


def test_a_complete_record_is_priced_and_the_arithmetic_is_right() -> None:
    """Hand computation, at $3.00 / $15.00 / $0.30 per million tokens:

    * fresh input  = 9601 - 3986 = 5615  ->  5615 * 3.00 / 1e6  = 0.016845
    * cache reads  = 3986               ->  3986 * 0.30 / 1e6  = 0.0011958
    * output       = 1411               ->  1411 * 15.00 / 1e6 = 0.021165
    * total                                                     = 0.0392058
    """
    cost = estimate_cost("fixture-model", REAL_USAGE, asof=PRICED_ON, table=FIXTURE)

    assert cost.is_measured
    assert cost.value == pytest.approx(0.0392058)


def test_the_cost_is_published_as_a_floor_not_a_point_estimate() -> None:
    """Cache writes and surcharge tiers are unmodelled, both understating, so the
    estimate bounds the bill from below. A bound rendered as a point estimate is the
    false-precision claim ``Relation`` exists to prevent."""
    cost = estimate_cost("fixture-model", REAL_USAGE, asof=PRICED_ON, table=FIXTURE)
    assert cost.render(4).startswith(">=")


def test_a_run_sums_its_records() -> None:
    """Two identical records at $1.00 in + $1.00 out each -> $4.00."""
    total = estimate_run_cost(
        "round-model", [ROUND_USAGE, ROUND_USAGE], asof=PRICED_ON, table=ROUND_RATES
    )
    assert total.is_measured
    assert total.value == pytest.approx(4.00)


def test_one_unpriceable_record_makes_the_whole_run_unpriceable() -> None:
    """v1 summed a cost table where one model was missing and published the total,
    which was the sum of the models it happened to know."""
    total = estimate_run_cost(
        "round-model",
        [ROUND_USAGE, {"input_tokens": 100}],
        asof=PRICED_ON,
        table=ROUND_RATES,
    )
    assert not total.is_measured
    assert "output_tokens" in total.why


def test_a_run_with_no_records_is_not_a_free_run() -> None:
    """``sum_token_usage([])`` returned a dict of zeros and priced a whole run as
    free; the dict is truthy, so the guard beside it never fired."""
    total = estimate_run_cost("round-model", [], asof=PRICED_ON, table=ROUND_RATES)
    assert not total.is_measured


# ── 4. cache reads are priced once, at the cache rate ─────────────────────────


def test_cache_reads_are_not_charged_at_the_input_rate_as_well() -> None:
    """A record where the difference is impossible to miss: the entire input is
    cached.

    At $3.00 input / $0.30 cache-read per million and 1,000,000 fully cached input
    tokens, the only correct answer is **$0.30**. Double-charging both rates gives
    $3.30; ignoring the cache tier gives $3.00. Asserting the exact figure rules out
    both, which a ``<`` comparison against the uncached cost would not.
    """
    fully_cached = {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 1_000_000,
    }
    cost = estimate_cost("fixture-model", fully_cached, asof=PRICED_ON, table=FIXTURE)

    assert cost.is_measured
    assert cost.value == pytest.approx(0.30)


def test_the_same_input_uncached_costs_the_full_input_rate() -> None:
    """The complement. Without it, a function that priced *everything* at the cache
    rate would pass the test above — the cheap way to make a cost look right is to
    apply the cheap rate everywhere.
    """
    uncached = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0}
    cost = estimate_cost("fixture-model", uncached, asof=PRICED_ON, table=FIXTURE)

    assert cost.is_measured
    assert cost.value == pytest.approx(3.00)


def test_an_absent_cache_field_prices_as_nothing_cached() -> None:
    """Providers that report no cache tier measured ``cache_read`` 0 across 49.4M
    input tokens on this repo's Anthropic path, so absent means zero cached here —
    and that must give the same answer as an explicit zero."""
    without = {"input_tokens": 1_000_000, "output_tokens": 0}
    with_zero = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_tokens": 0}

    a = estimate_cost("fixture-model", without, asof=PRICED_ON, table=FIXTURE)
    b = estimate_cost("fixture-model", with_zero, asof=PRICED_ON, table=FIXTURE)
    assert a.value == pytest.approx(b.value)


def test_more_cached_than_input_refuses_rather_than_clamping() -> None:
    """The subset relation holds in every record on disk. Where it does not, the
    fresh share is underivable and v1's clamp would answer confidently and wrongly."""
    contradictory = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_tokens": 900,
    }
    cost = estimate_cost("fixture-model", contradictory, asof=PRICED_ON, table=FIXTURE)

    assert not cost.is_measured
    assert "cache_read_tokens" in cost.why


# ── 5. the staleness window, at both boundaries ───────────────────────────────


def test_a_price_exactly_at_the_window_edge_is_still_a_price() -> None:
    on_the_edge = OBSERVED + timedelta(days=PRICE_VALIDITY_DAYS)
    cost = estimate_cost("round-model", ROUND_USAGE, asof=on_the_edge, table=ROUND_RATES)
    assert cost.is_measured


def test_a_price_one_day_past_the_window_stops_being_a_measurement() -> None:
    past_the_edge = OBSERVED + timedelta(days=PRICE_VALIDITY_DAYS + 1)
    cost = estimate_cost("round-model", ROUND_USAGE, asof=past_the_edge, table=ROUND_RATES)

    assert not cost.is_measured
    assert OBSERVED.isoformat() in cost.why, cost.why


def test_the_window_is_symmetric_so_a_future_rate_does_not_price_an_old_run() -> None:
    """gpt-5.6 Luna moved -80% in a day. Pricing a run from before a repricing at the
    rate observed after it understates by a factor of five, which is the same defect
    as a stale rate running the other direction."""
    long_before = OBSERVED - timedelta(days=PRICE_VALIDITY_DAYS + 1)
    just_before = OBSERVED - timedelta(days=PRICE_VALIDITY_DAYS)

    assert not estimate_cost(
        "round-model", ROUND_USAGE, asof=long_before, table=ROUND_RATES
    ).is_measured
    assert estimate_cost(
        "round-model", ROUND_USAGE, asof=just_before, table=ROUND_RATES
    ).is_measured


def test_the_age_is_reportable_beside_the_cost() -> None:
    age = price_age_days("round-model", asof=OBSERVED + timedelta(days=7), table=ROUND_RATES)
    assert age.value == 7
    assert not price_age_days("unpriced-model", asof=PRICED_ON).is_measured


# ── the table itself: rows, and the states a rate may be in ───────────────────


def test_a_rate_with_no_date_or_no_source_cannot_be_constructed() -> None:
    """Enforced in ``__post_init__``, so it also covers a row a caller injects
    through ``table=`` — which a scan over the module's own table would not."""
    for undated in ({"observed": ""}, {"source": ""}):
        kwargs = {
            "model": "probe",
            "input_per_mtok": Measured.of(1.0),
            "output_per_mtok": Measured.of(1.0),
            "cache_read_per_mtok": Measured.of(1.0),
            "currency": "USD",
            "observed": "2026-01-01",
            "source": "tests/measure/test_price.py",
            **undated,
        }
        with pytest.raises(ValueError):
            Price(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        Price(
            "probe",
            Measured.of(1.0), Measured.of(1.0), Measured.of(1.0),
            "USD", "the third of August", "tests/measure/test_price.py",
        )


def test_a_rate_nobody_sourced_makes_the_cost_unmeasured_not_cheap() -> None:
    """The shape the brief calls for: a table that admits it does not know a price.
    A plausible invented number here is the nine-fold overstatement happening
    again."""
    unsourced = {
        "unknown-rate-model": Price(
            "unknown-rate-model",
            Measured.unmeasured("no published rate found for this model"),
            Measured.of(15.00),
            Measured.of(0.30),
            "USD", OBSERVED.isoformat(), "tests/measure/test_price.py",
        )
    }
    cost = estimate_cost("unknown-rate-model", REAL_USAGE, asof=PRICED_ON, table=unsourced)

    assert not cost.is_measured
    assert "no published rate" in cost.why


def test_an_inapplicable_rate_contributes_zero_without_poisoning_the_total() -> None:
    """An embeddings call has no completion tokens *by construction*, so a zero
    output charge is a declared fact rather than a defaulted one — and a record with
    no ``output_tokens`` field must still price.

    Hand computation at $1.00 per million input, 400,000 tokens: $0.40.
    """
    embeddings = {
        "embedder": Price(
            "embedder",
            Measured.of(1.00),
            Measured.inapplicable("an embeddings response has no completion tokens"),
            Measured.inapplicable("no cached-input tier is published"),
            "USD", OBSERVED.isoformat(), "tests/measure/test_price.py",
        )
    }
    cost = estimate_cost(
        "embedder", {"input_tokens": 400_000}, asof=PRICED_ON, table=embeddings
    )

    assert cost.is_measured
    assert cost.value == pytest.approx(0.40)


def test_the_model_that_produced_no_usd_at_all_is_now_priceable() -> None:
    """The regression test for the second half of the incident: both 2026-07
    Anthropic ladders reported no dollar figure because no Claude model was in the
    table. Driven against the shipped table on purpose — an empty table fails it —
    but asserting only that a cost exists and is positive, never what it equals.
    """
    cost = estimate_cost("Claude-Opus-4.8", REAL_USAGE, asof=date(2026, 8, 3))

    assert cost.is_measured, cost.render(6)
    assert cost.value > 0.0


def test_model_ids_are_matched_case_insensitively() -> None:
    """Run manifests spell Anthropic ids in mixed case and the SDK spells them
    lowercase-hyphenated; a case mismatch must not read as an unknown model."""
    cost = estimate_cost("claude-opus-4.8", REAL_USAGE, asof=date(2026, 8, 3))
    assert cost.is_measured
