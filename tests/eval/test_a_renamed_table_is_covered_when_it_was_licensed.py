"""Coverage compares asset ids to asset ids, not asset ids to engine spellings.

``gold_tables()`` returns the table names a benchmark statement *writes* — the engine's
identifiers, ``airline."Air Carriers"``. ``licensed`` carries **asset ids**, and an id is a key
rather than a name (ADR 0008 D1): the corpus stores that table as
``id: airline.Air_Carriers_66c534`` with ``physical_name: Air Carriers``. Comparing the two
strings, case-insensitively or otherwise, compares two different nouns.

It agrees anyway for 655 of the 656 table assets in ``../BIRD-corpus``, because
``corpus.identity.slug`` returns a bare identifier unchanged — so the metric has been right by a
property of one corpus rather than by construction. On the one table where the two spellings
differ it is wrong in one direction only: the gold names a table that *was* licensed and the
turn is scored as a coverage miss, so the published ceiling is too low, never too high. Five of
the 1 351 rows in ``runs/eval/proxy_v4_corpus30872d3.jsonl`` are that table, and in all five it
is the only miss.

The recovery rule is ``slug`` itself, not string tidying: the ``_66c534`` tail is a digest of the
exact name and exists so ``a b`` and ``a_b`` cannot collide, so mapping spaces to underscores
would both fail to reproduce the id and start matching decoys apart. The negative test at the
bottom of this file is what holds that shut.
"""

from __future__ import annotations

#: The one table in the shipped BIRD corpus whose id is not its physical name.
AIR_CARRIERS_ID = "airline.Air_Carriers_66c534"

#: A gold statement as the dataset writes it: the physical name, quoted because it has a space.
GOLD = (
    'SELECT "T1"."Description" FROM "airline"."Air Carriers" AS "T1" '
    'JOIN "airline"."Airlines" AS "T2" ON "T1"."Code" = "T2"."OP_CARRIER_AIRLINE_ID"'
)


def test_a_gold_naming_a_physical_name_is_covered_when_its_asset_is_licensed() -> None:
    """The EX ceiling must not count a licensed table as unlicensed because its id is a slug.

    ``table_coverage`` is documented as *"the EX ceiling"* — the split between "was this question
    answerable under this retrieval" and "did the model convert it". A turn that licensed
    ``airline.Air_Carriers_66c534`` was allowed to read ``airline."Air Carriers"``; scoring it as
    a partial covers the question in the wrong bucket and lowers the ceiling below what the
    retrieval actually delivered.
    """
    from governed_bi.eval.datalake import table_coverage

    out = table_coverage(
        [{"question_id": "q", "licensed": [AIR_CARRIERS_ID, "airline.Airlines"]}],
        {"q": GOLD},
    )

    assert out["n"] == 1
    assert out["all_gold_tables_licensed"] == 1.0, (
        "both gold tables were licensed; `Air Carriers` is the physical name of "
        f"{AIR_CARRIERS_ID}"
    )
    assert out["some_licensed"] == 0.0


def test_the_funnel_counts_the_same_licensed_table_the_coverage_metric_does() -> None:
    """``retrieval_funnel`` compares the same two nouns at its own stage, and must agree.

    Two places deciding "was this gold table licensed" by two rules is the drift this module's
    own prose warns about for ``degenerate``. The funnel's stage is conditional, so a false miss
    here also removes the row from the denominators of ``answered``, ``graded`` and ``correct``.
    """
    from governed_bi.eval.datalake import retrieval_funnel

    row = {
        "question_id": "q",
        "db_id": "airline",
        "licensed": [AIR_CARRIERS_ID, "airline.Airlines"],
        "licensed_schemas": ["airline"],
        "outcome": "answered",
        "correct": True,
    }
    out = retrieval_funnel([row], {"q": GOLD}, {"q": "airline"})

    counts = out["counts"]
    assert counts["scorable"] == 1
    assert counts["tables_in_routed_schemas"] == 1
    assert counts["all_gold_tables_licensed"] == 1, (
        "the turn licensed both gold tables, so the funnel must not lose the row here"
    )
    assert counts["correct"] == 1


def test_the_return_path_does_not_file_a_licensed_table_as_missing() -> None:
    """The same comparison decides what an operator is told, not only what a report prints.

    ``feedback_import`` writes ``missing_tables`` onto an :class:`Observation` and buckets the row
    as ``coverage_miss``, whose reader-facing sentence is "the turn was not allowed to read this".
    Said about a table the turn *was* allowed to read, it sends somebody to curate an asset that
    already exists.
    """
    from governed_bi.eval.feedback_import import _missing_tables

    row = {
        "question_id": "q",
        "gold_sql": GOLD,
        "licensed": [AIR_CARRIERS_ID, "airline.Airlines"],
    }

    assert _missing_tables(row) == set()


def test_a_slug_without_its_digest_is_not_the_same_table() -> None:
    """Normalising spaces to underscores is not the fix, and must not become one.

    ``slug`` appends a six-hex digest of the exact name precisely because ``a b`` and ``a_b``
    sanitise alike. ``airline.Air_Carriers`` is therefore a *different* table from
    ``airline."Air Carriers"`` — on the obfuscated benchmark it is the shape a rename decoy takes
    — and licensing it covers nothing.
    """
    from governed_bi.eval.datalake import table_coverage

    out = table_coverage(
        [{"question_id": "q", "licensed": ["airline.Air_Carriers", "airline.Airlines"]}],
        {"q": GOLD},
    )

    assert out["all_gold_tables_licensed"] == 0.0
    assert out["some_licensed"] == 1.0, "`airline.Airlines` was licensed and `Air Carriers` was not"
