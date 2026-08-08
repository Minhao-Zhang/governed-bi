"""``find_schema_leak`` — shape-based detection of a raw identifier in
human-facing clarification text (Gap 2, utku-ai-deployment-targets.md)."""

from __future__ import annotations

from governed_bi.serve.schema_term_guard import find_schema_leak


def test_the_actual_leaked_example_is_caught() -> None:
    """The exact real leak found in ask_user's own docstring (RESUME.md)."""
    leak = find_schema_leak(
        "does revenue mean payments.amount or line_items.unit_price?"
    )
    assert leak in ("payments.amount", "line_items.unit_price")


def test_snake_case_column_names_are_caught() -> None:
    assert find_schema_leak("should we exclude pct_delivered from the total?")


def test_pascal_case_column_names_are_caught() -> None:
    assert find_schema_leak("is CaneSugar recorded as TRUE/FALSE or 1/0?")
    assert find_schema_leak("does price mean CurrentRetailPrice or PurchasePrice?")


def test_plain_business_language_is_not_flagged() -> None:
    clean = [
        "Should cancelled orders be excluded from total revenue?",
        "Does active mean transacted in the last 30 days?",
        "What does the abbreviation on the receipt mean?",
        "Is the discount applied before or after tax?",
        "Which nights make the most money?",
    ]
    for text in clean:
        assert find_schema_leak(text) is None, text


def test_checks_every_text_argument_not_just_the_first() -> None:
    assert find_schema_leak("plain question", "why: payments.amount vs line_items.unit_price")
    assert find_schema_leak("plain question", "plain why") is None


def test_known_limitation_a_camelcase_shaped_proper_noun_still_trips_it() -> None:
    """Documented, not silently absorbed (see module docstring): shape detection
    cannot tell a schema leak from a camelCase-shaped brand name apart -- both
    ``PurchasePrice`` and ``PowerKiosk`` are two capitalized English words
    concatenated with no space. Accepted tradeoff: costs one rephrase retry on
    the rare question that names a compound-capitalized brand, never a
    correctness failure, and dotted-path/snake_case (the two shapes that matter
    most, since they cannot occur in ordinary English at all) have no such
    false-positive mode.
    """
    assert find_schema_leak("Ask PowerKiosk what they'd like to see") == "PowerKiosk"
