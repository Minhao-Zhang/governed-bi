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


def test_a_short_name_inside_a_long_one_is_caught_by_values_and_not_by_a_run_length() -> None:
    """The coincidence class, now answered with evidence instead of a character-count floor.

    ``ort`` scores a perfect 1.0 against ``betriebsstandorte`` because three characters happen to
    sit inside it, and ``maissirup`` scores 0.6 against ``email`` for the same reason (``mai``).
    A minimum-run floor was what caught these, and it also cost ``betrieb_informationen.ort`` /
    ``geografisch.ortschaft`` on ``restaurant`` — a real join, whose whole shared run is also
    three characters. What separates them is not the name at all: the coincidence shares no
    value and the join shares its whole domain.
    """
    from governed_bi.curator.gap_signals import name_similarity, values_overlap

    assert name_similarity("ort", "betriebsstandorte") == 1.0
    assert name_similarity("maissirup", "email") >= 0.6

    assert not values_overlap(("ja", "nein"), ("a@b.de", "c@d.de"))
    assert values_overlap(("alameda", "berkeley"), ("alameda", "albany", "berkeley"))
    assert not values_overlap((), ("alameda",)), "a column nobody read is not evidence"


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


def test_a_frame_sibling_has_to_match_the_frame_as_well_as_the_pair_matches_itself() -> None:
    """The comparison that makes the rule safe, on the three real ``geoposition`` columns that
    force it. ``l_ngengrad``/``laengengrad`` is a manifest decoy pair scoring 0.89 and
    ``breitengrad`` matches their shared ``ngengrad`` at only 0.75 — so it is *not* their
    sibling. The very same ``breitengrad`` **is** a sibling of the
    ``breitengrad``/``l_ngengrad`` pair, which scores 0.67. One fixed threshold cannot give both
    answers; a comparison against the pair's own similarity can.
    """
    from governed_bi.curator.gap_signals import frame_siblings, name_similarity, shared_name_run

    columns = [_column(n, "real") for n in ("breitengrad", "l_ngengrad", "laengengrad")]
    breite, lnge, laenge = columns

    decoy = name_similarity("l_ngengrad", "laengengrad")
    assert name_similarity("breitengrad", shared_name_run("l_ngengrad", "laengengrad")) < decoy
    assert frame_siblings(columns, lnge, laenge, decoy) == []

    parallel = name_similarity("breitengrad", "l_ngengrad")
    assert [c.physical_name for c in frame_siblings(columns, breite, lnge, parallel)] == [
        "laengengrad"
    ]


def test_a_frame_sibling_of_another_type_cannot_be_a_family_member() -> None:
    """``standort`` is what kills the naive version: ``standort_id``, ``standortname``,
    ``standort_nummer`` and ``standort_bezeichnung`` all wear ``standort``, so by names alone the
    ``standort_id``/``standort_nummer`` manifest decoy pair looks exactly like a parallel frame.
    Two of the four are text; a comparison that cannot execute is not a family member.
    """
    from governed_bi.curator.gap_signals import frame_siblings, name_similarity

    columns = [
        _column("standort_id", "bigint"), _column("standort_nummer", "bigint"),
        _column("standortname", "text"), _column("standort_bezeichnung", "text"),
    ]
    pair = name_similarity("standort_id", "standort_nummer")
    assert frame_siblings(columns, columns[0], columns[1], pair) == []


def test_the_shared_run_is_the_frame_two_names_wear() -> None:
    from governed_bi.curator.gap_signals import shared_name_run

    assert shared_name_run("kunde_id", "transaktions_kunde_id") == "kundeid"
    assert shared_name_run("transaktions_id", "transaktions_wurzelbier_id") == "transaktions"
    assert shared_name_run("Content Rating", "content_rating") == "contentrating"
    assert shared_name_run("abc", "xyz") == ""


def _column(name: str, physical_type: str):
    from governed_bi.corpus.schema import ColumnAsset

    return ColumnAsset(
        id=f"s.t.{name}", schema="s", parent_table="s.t", physical_name=name,
        summary=name, physical_type=physical_type,
    )
