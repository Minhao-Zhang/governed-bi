"""curator/gap_signals.py: the gap signals that cost no database read.

Split out of ``tests/curator/test_gaps.py`` alongside the module itself. These are the tests that
need nothing but two strings — which is the property the split exists to make visible: the
identifier measurement is a pure function, so the sweep that chose
``NEAR_DUPLICATE_SIMILARITY`` against BIRD-Obfuscation's decoy manifest could be run without a
connector, a corpus, or a ledger. Everything that needs a row measured stays in ``test_gaps.py``.
"""

from __future__ import annotations


def test_similarity_is_computed_on_characters_not_words() -> None:
    """The root cause this fixes: a word list is a language. Both measures read the case-folded
    alphanumeric character run, so ``stadt``/``stadtname`` and ``city``/``city_name`` score
    identically and neither is privileged."""
    from governed_bi.curator.gap_signals import name_similarity

    assert name_similarity("stadt", "stadtname") == name_similarity("city", "city_name")
    assert name_similarity("email", "email_adresse") == name_similarity("email", "email_address")


def test_the_two_measures_cover_the_two_shapes_a_duplicate_takes() -> None:
    """Neither measure alone reaches both, which is why the signal is their maximum.

    Containment (``email`` inside ``email_adresse``) is what a longest-common-substring ratio
    sees and a trigram overlap barely registers; reordering
    (``aktueller_einzelhandelspreis`` vs ``einzelhandel_preis_aktuell``) is the reverse.
    """
    from governed_bi.curator.gap_signals import (
        NEAR_DUPLICATE_SIMILARITY,
        _longest_common_run_ratio,
        _trigram_dice,
        name_similarity,
    )

    assert _trigram_dice("email", "email_adresse") < NEAR_DUPLICATE_SIMILARITY
    assert _longest_common_run_ratio("email", "email_adresse") == 1.0

    reordered = ("aktueller_einzelhandelspreis", "einzelhandel_preis_aktuell")
    assert _longest_common_run_ratio(*reordered) < NEAR_DUPLICATE_SIMILARITY
    assert _trigram_dice(*reordered) >= NEAR_DUPLICATE_SIMILARITY

    for pair in (("email", "email_adresse"), reordered):
        assert name_similarity(*pair) >= NEAR_DUPLICATE_SIMILARITY, pair


def test_unrelated_names_score_below_the_gate() -> None:
    from governed_bi.curator.gap_signals import NEAR_DUPLICATE_SIMILARITY, name_similarity

    for a, b in (("kunde_id", "kreditkartennummer"), ("vorname", "telefonnummer"),
                 ("order_id", "shipped_at")):
        assert name_similarity(a, b) < NEAR_DUPLICATE_SIMILARITY, (a, b)


def test_a_short_name_inside_a_long_one_is_not_a_key_into_that_table() -> None:
    """``betriebsstandorte.ort`` scored 1.0 against its own table name because ``ort`` is three
    characters that happen to sit inside it — the coincidence ``_MIN_KEY_NAME_RUN`` was added
    for, measured doing damage on real ``beer_factory``. Kept scoped to this predicate: the same
    floor inside ``name_similarity`` would cost the real ``playstore.App``/``app_name`` decoy.
    """
    from types import SimpleNamespace

    from governed_bi.curator.gap_signals import identifies_rows, name_similarity

    column = SimpleNamespace(physical_name="ort")
    table = SimpleNamespace(physical_name="betriebsstandorte")
    assert name_similarity(column.physical_name, table.physical_name) == 1.0
    assert identifies_rows(column, table) is False
    assert identifies_rows(SimpleNamespace(physical_name="kunde_id"),
                           SimpleNamespace(physical_name="kunden")) is True


def test_evidence_strength_discounts_a_tiny_denominator() -> None:
    """The measured failure: ``3 of 3`` and ``6 305 of 6 312`` are both ~100% and ranking them
    by share alone put the three-row table first. The lower bound separates them by two thirds,
    and it agrees with the raw share once the denominator is large enough to trust."""
    from governed_bi.curator.gap_signals import evidence_strength

    tiny, headline = evidence_strength(3, 3), evidence_strength(6305, 6312)
    assert tiny < 0.5 < headline
    assert headline > 0.99
    assert abs(headline - 6305 / 6312) < 0.01, "the discount vanishes on a large denominator"


def test_evidence_strength_is_monotone_in_both_arguments() -> None:
    """More of the same evidence is stronger; a smaller share of it is weaker. Both directions,
    because a ranking key that is not monotone in either is not a ranking of severity."""
    from governed_bi.curator.gap_signals import evidence_strength

    same_share = [evidence_strength(n, n) for n in (3, 24, 554, 6312)]
    assert same_share == sorted(same_share)
    same_rows = [evidence_strength(d, 100) for d in (1, 10, 50, 99)]
    assert same_rows == sorted(same_rows)


def test_no_evidence_scores_zero_rather_than_raising() -> None:
    """Records with no measured sentence sort last within their tier, so the sort key has to
    have a value for them."""
    from governed_bi.curator.gap_signals import evidence_strength

    assert evidence_strength(0, 0) == 0.0
    assert evidence_strength(0, 100) == 0.0
