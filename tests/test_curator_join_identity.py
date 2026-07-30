"""A join asset's identity includes its ON clause.

Regression for silent join-coverage loss found on 2026-07-29. The id was
``join_{schema}_{left}_{right}``, so a *second* relationship between the same pair
of tables overwrote the first with no error and no validate finding. Measured over
the 57 benchmark train schemas: ``soccer_2016`` kept 32 of 54 gold-derived edges,
``mondial_geo`` 67 of 87, and 33 of 57 schemas lost at least one. All of it happens
in ``_apply_seed``, before the curator agent runs, so the ``seeded``, ``curated``
and ``curated_sme`` arms were affected equally.

It also produced a wrong diagnosis: ``run_manifest.json`` reported seed *calls*
while the corpus held *assets*, and differencing the two looked like the agent had
deleted joins.
"""

from __future__ import annotations

import pytest

from governed_bi.corpus import ids
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.curator.asset_bag import AssetBag, on_clause_digest


def _col(name: str) -> Column:
    return Column(
        physical_name=name,
        physical_type="INTEGER",
        logical_type=LogicalType.integer,
        nullable=True,
        is_unique=False,
    )


@pytest.fixture
def bag() -> AssetBag:
    """Two tables with three plausible relationships between them.

    Modelled on the real ``soccer_2016`` case: a match row references a team three
    times (home, away, winner), and all three are legitimate joins.
    """
    return AssetBag.from_tables(
        "s",
        [
            TableAsset(
                id="tbl_s_mannschaft",
                schema="s",
                physical_name="mannschaft",
                columns=[_col("mannschaft_id")],
            ),
            TableAsset(
                id="tbl_s_spiel",
                schema="s",
                physical_name="spiel",
                columns=[_col("mannschaft_1"), _col("mannschaft_2"), _col("spiel_gewinner")],
            ),
        ],
    )


def test_distinct_relationships_between_one_pair_coexist(bag: AssetBag):
    """The bug: three upserts, one surviving asset, last write wins."""
    for right_col in ("mannschaft_1", "mannschaft_2", "spiel_gewinner"):
        msg = bag.upsert_join(
            "mannschaft", "spiel", f"mannschaft.mannschaft_id = spiel.{right_col}"
        )
        assert msg.startswith("ok:"), msg
    assert len(bag.joins) == 3, "each distinct ON clause is its own edge"
    ons = {j.on for j in bag.joins.values()}
    assert len(ons) == 3
    assert len({j.id for j in bag.joins.values()}) == 3


def test_reproposing_the_same_edge_still_upserts(bag: AssetBag):
    """Identity must not become so fine-grained that a genuine re-proposal
    accumulates duplicates — the agent is told to verify seeded joins."""
    on = "mannschaft.mannschaft_id = spiel.mannschaft_1"
    bag.upsert_join("mannschaft", "spiel", on, confidence=0.55)
    bag.upsert_join("mannschaft", "spiel", on, confidence=0.9)
    assert len(bag.joins) == 1
    assert next(iter(bag.joins.values())).confidence == 0.9


@pytest.mark.parametrize(
    "a,b",
    [
        # An equality is unordered.
        ("a.x = b.y", "b.y = a.x"),
        # Whitespace and case are not identity.
        ("a.x = b.y", "  A.X   =  B.Y  "),
        # The conjuncts of a composite key are unordered.
        ("a.x = b.y AND a.p = b.q", "a.p = b.q AND a.x = b.y"),
        # ... and each conjunct is independently unordered.
        ("a.x = b.y AND a.p = b.q", "b.q = a.p and b.y = a.x"),
    ],
)
def test_normalisation_treats_these_as_the_same_edge(a: str, b: str):
    assert on_clause_digest(a) == on_clause_digest(b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("a.x = b.y", "a.x = b.z"),
        ("a.x = b.y", "a.x = b.y AND a.p = b.q"),
        # A non-equality keeps its written order, so it must not collapse onto the
        # equality that shares its operands.
        ("a.x = b.y", "a.x < b.y"),
    ],
)
def test_normalisation_keeps_these_apart(a: str, b: str):
    assert on_clause_digest(a) != on_clause_digest(b)


def test_join_id_still_satisfies_the_ci_id_convention(bag: AssetBag):
    """``ID_PATTERNS['join']`` is regex-checked in CI and by ``validate_corpus``
    (finding ``bad-id``). A hex digest suffix must not break it."""
    bag.upsert_join("mannschaft", "spiel", "mannschaft.mannschaft_id = spiel.mannschaft_1")
    for jid in bag.joins:
        assert ids.is_valid_id("join", jid), jid


def test_seed_reports_calls_and_assets_separately():
    """``joins_ok`` counts successful calls; ``joins_written`` counts assets. The
    gap is the collapse, and reporting only the first is what made a phantom
    "the agent deleted 21 joins" finding."""
    from governed_bi.curator.pipeline import _apply_seed
    from governed_bi.curator.seed import JoinCandidate, SeedBundle

    b = AssetBag.from_tables(
        "s",
        [
            TableAsset(id="tbl_s_a", schema="s", physical_name="a", columns=[_col("k")]),
            TableAsset(id="tbl_s_b", schema="s", physical_name="b", columns=[_col("k")]),
        ],
    )
    dupe = JoinCandidate(
        left_table="a", right_table="b", on="a.k = b.k", source_sql="SELECT 1"
    )
    stats = _apply_seed(b, SeedBundle(joins=[dupe, dupe], metrics=[]))
    assert stats["joins_ok"] == 2, "both calls succeeded"
    assert stats["joins_written"] == 1, "and collapsed onto one asset"
