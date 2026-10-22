"""Coverage matches asset ids via ``slug``, not the gold's spelling (ADR 0008). Folding spaces would also match a
different table."""

from __future__ import annotations

#: The one table in the shipped BIRD corpus whose id is not its physical name.
AIR_CARRIERS_ID = "airline.Air_Carriers_66c534"

#: A gold statement as the dataset writes it: the physical name, quoted because it has a space.
GOLD = (
    'SELECT "T1"."Description" FROM "airline"."Air Carriers" AS "T1" '
    'JOIN "airline"."Airlines" AS "T2" ON "T1"."Code" = "T2"."OP_CARRIER_AIRLINE_ID"'
)


def test_a_gold_naming_a_physical_name_is_covered_when_its_asset_is_retrieved_tables() -> None:
    """A retrieved slug must count as covered; scoring it as a miss lowers the ceiling."""
    from governed_bi.eval.datalake import table_coverage

    out = table_coverage(
        [{"question_id": "q", "retrieved_tables": [AIR_CARRIERS_ID, "airline.Airlines"]}],
        {"q": GOLD},
    )

    assert out["n"] == 1
    assert out["all_gold_tables_retrieved_tables"] == 1.0, (
        "both gold tables were retrieved; `Air Carriers` is the physical name of "
        f"{AIR_CARRIERS_ID}"
    )
    assert out["some_retrieved_tables"] == 0.0


def test_the_funnel_counts_the_same_retrieved_tables_table_the_coverage_metric_does() -> None:
    """A false miss here also drops the row from later funnel denominators."""
    from governed_bi.eval.datalake import retrieval_funnel

    row = {
        "question_id": "q",
        "db_id": "airline",
        "retrieved_tables": [AIR_CARRIERS_ID, "airline.Airlines"],
        "retrieved_tables_schemas": ["airline"],
        "outcome": "answered",
        "correct": True,
    }
    out = retrieval_funnel([row], {"q": GOLD}, {"q": "airline"})

    counts = out["counts"]
    assert counts["scorable"] == 1
    assert counts["tables_in_routed_schemas"] == 1
    assert counts["all_gold_tables_retrieved_tables"] == 1, (
        "the turn retrieved both gold tables, so the funnel must not lose the row here"
    )
    assert counts["correct"] == 1


def test_a_slug_without_its_digest_is_not_the_same_table() -> None:
    """A slug without the digest is a different table; folding spaces would match it."""
    from governed_bi.eval.datalake import table_coverage

    out = table_coverage(
        [{"question_id": "q", "retrieved_tables": ["airline.Air_Carriers", "airline.Airlines"]}],
        {"q": GOLD},
    )

    assert out["all_gold_tables_retrieved_tables"] == 0.0
    assert out["some_retrieved_tables"] == 1.0, "`airline.Airlines` was retrieved and `Air Carriers` was not"
