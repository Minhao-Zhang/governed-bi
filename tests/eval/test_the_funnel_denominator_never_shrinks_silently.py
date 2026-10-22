"""A miscount must raise, stay excluded, or stay in the denominator. A real-looking zero is a silent shrink."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_table_coverage_refuses_rows_that_do_not_carry_retrieved_tables() -> None:
    """A missing ``retrieved_tables`` key must raise. An empty list is a real zero."""
    from governed_bi.eval.datalake import table_coverage

    gold = {"q1": "SELECT * FROM restaurant.generalinfo"}

    with pytest.raises(KeyError, match="retrieved_tables"):
        table_coverage([{"question_id": "q1", "retrieved_tables_schemas": ["restaurant"]}], gold)

    empty = table_coverage([{"question_id": "q1", "retrieved_tables": []}], gold)
    assert empty["all_gold_tables_retrieved_tables"] == 0.0, "an empty retrieved-table list is a real zero"
    assert empty["n"] == 1

    covered = table_coverage(
        [{"question_id": "q1", "retrieved_tables": ["restaurant.generalinfo"]}], gold
    )
    assert covered["all_gold_tables_retrieved_tables"] == 1.0


def test_routing_recall_rows_carry_what_table_coverage_reads() -> None:
    """The two functions' shapes are locked together, not merely documented as compatible.

    Asserted over the *keys*, because the defect above was a spelling mismatch between one
    module's producer and its consumer — the kind a comment cannot hold shut.
    """
    import inspect

    from governed_bi.eval import datalake

    source = inspect.getsource(datalake.routing_recall)
    assert '"retrieved_tables": retrieved_tables' in source, (
        "routing_recall must publish the table ids under `retrieved_tables`; table_coverage reads "
        "that key and nothing else"
    )


def test_a_gold_statement_that_reads_no_table_is_not_a_coverage_miss() -> None:
    """A gold that reads no table leaves the denominator and is counted in its own field."""
    from governed_bi.eval.datalake import table_coverage

    folded = 'SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")'
    rows = [
        {"question_id": "folded", "retrieved_tables": ["restaurant.generalinfo"]},
        {"question_id": "real", "retrieved_tables": ["restaurant.generalinfo"]},
    ]
    out = table_coverage(
        rows, {"folded": folded, "real": "SELECT * FROM restaurant.generalinfo"}
    )

    assert out["gold_reads_no_table"] == 1
    assert out["n"] == 1, "the table-less question must leave the denominator"
    assert out["all_gold_tables_retrieved_tables"] == 1.0, (
        "the one scorable question was fully covered; the folded one must not drag it to 0.5"
    )
    assert out["none_retrieved_tables"] == 0.0


def test_connect_seeds_its_tree_deterministically() -> None:
    """The tree must not depend on set iteration order. Those points enter ``retrieved_tables``."""
    import random

    from governed_bi.retrieve.connect import connect

    edges = {
        tuple(sorted(e))
        for e in {
            ("a", "h1"), ("h1", "b"), ("b", "h2"), ("h2", "c"), ("c", "h3"), ("h3", "a"),
            ("a", "h4"), ("h4", "c"), ("d", "h5"), ("h5", "b"), ("d", "h6"), ("h6", "c"),
        }
    }
    terminals = ["a", "b", "c", "d"]

    # Shuffle terminals and edges. Edge order reaches adjacency insertion, a second tie break.
    results = []
    for _ in range(12):
        shuffled_terms = terminals[:]
        random.shuffle(shuffled_terms)
        shuffled_edges = list(edges)
        random.shuffle(shuffled_edges)
        results.append(
            tuple(
                sorted(
                    connect(set(shuffled_terms), edges=set(shuffled_edges), max_points=10).added
                )
            )
        )

    assert len(set(results)) == 1, (
        f"connect returned {len(set(results))} different Steiner sets for one terminal set: "
        f"{sorted(set(results))}. The tree must not depend on set iteration order -- those "
        "points enter `licensed`, which table_coverage reads as the table ids."
    )


def test_the_dataset_exclusion_lists_are_read_by_their_real_names(tmp_path: Path) -> None:
    """An empty exclusion set looks like a dataset that declares none. A missing key must not read as that."""
    from governed_bi.eval.datalake import dataset_qid_lists

    (tmp_path / "order_sensitive_qids.json").write_text(
        '{"note": "n", "order_sensitive": ["7", 8], "exec_failed": ["train_9"],'
        ' "counts": {"order_sensitive": 2}}',
        encoding="utf-8",
    )
    lists = dataset_qid_lists(tmp_path)
    assert lists["order_sensitive"] == {"7", "8"}, "ids are compared as strings elsewhere"
    assert lists["exec_failed"] == {"train_9"}


def test_a_file_with_no_recognised_list_raises_instead_of_excluding_nothing(
    tmp_path: Path,
) -> None:
    """The defect, made unrepresentable. A silent empty set is the whole bug."""
    from governed_bi.eval.datalake import dataset_qid_lists

    (tmp_path / "order_sensitive_qids.json").write_text(
        '{"question_ids": ["7"]}', encoding="utf-8"
    )
    with pytest.raises(KeyError, match="none of"):
        dataset_qid_lists(tmp_path)


def test_no_file_at_all_is_a_real_absence_and_not_an_error(tmp_path: Path) -> None:
    """A dataset need not ship the list; only a *misread* one is a defect."""
    from governed_bi.eval.datalake import dataset_qid_lists

    assert dataset_qid_lists(tmp_path) == {"order_sensitive": set(), "exec_failed": set()}


def test_the_shipped_dataset_declares_exclusions_if_it_is_present() -> None:
    """Guards the real file against a rename. Skips when the sibling repo is absent."""
    from governed_bi.eval.datalake import dataset_qid_lists

    dataset = Path(__file__).resolve().parents[2].parent / "BIRD-Data-Obfuscation" / "eval_dataset"
    if not (dataset / "order_sensitive_qids.json").exists():
        pytest.skip("BIRD-Data-Obfuscation not checked out beside this repo")
    lists = dataset_qid_lists(dataset)
    assert lists["order_sensitive"], "the shipped dataset declares 97 order-sensitive golds"
    assert lists["exec_failed"], "the shipped dataset declares 10 degenerate golds"


def _funnel_rows():
    """Four rows, each lost at a different stage, so every conditional is exercised."""
    return [
        # Wrong schema entirely.
        {
            "question_id": "1",
            "db_id": "sales",
            "retrieved_tables_schemas": ["ops"],
            "retrieved_tables": ["ops.things"],
            "outcome": "answered",
            "correct": False,
        },
        # Right schema, but the gold table did not survive to `retrieved_tables`.
        {
            "question_id": "2",
            "db_id": "sales",
            "retrieved_tables_schemas": ["sales"],
            "retrieved_tables": ["sales.other"],
            "outcome": "answered",
            "correct": False,
        },
        # Everything retrieved_tables, model answered, wrong result. This is a *generation* loss.
        {
            "question_id": "3",
            "db_id": "sales",
            "retrieved_tables_schemas": ["sales"],
            "retrieved_tables": ["sales.customers"],
            "outcome": "answered",
            "correct": False,
        },
        # Everything retrieved_tables and correct.
        {
            "question_id": "4",
            "db_id": "sales",
            "retrieved_tables_schemas": ["sales"],
            "retrieved_tables": ["sales.customers"],
            "outcome": "answered",
            "correct": True,
        },
    ]


def test_the_funnel_separates_routing_from_table_selection_from_generation() -> None:
    """Side-by-side rates cannot tell a wrong schema from a dropped table."""
    from governed_bi.eval.datalake import retrieval_funnel

    gold_sql = {str(i): "SELECT 1 FROM sales.customers" for i in range(1, 5)}
    out = retrieval_funnel(_funnel_rows(), gold_sql)
    counts, cond = out["counts"], out["conditional"]

    assert counts["scorable"] == 4
    assert counts["schema_routed"] == 3, "row 1 routed to the wrong schema"
    assert counts["tables_in_routed_schemas"] == 3
    assert counts["all_gold_tables_retrieved_tables"] == 2, "row 2 lost the table after routing"
    assert counts["correct"] == 1

    # Each stage is conditional on the one above, and each carries its own denominator.
    assert cond["schema_routed"] == {"rate": 0.75, "n": 3, "of": 4, "why": None}
    assert cond["all_gold_tables_retrieved_tables"]["of"] == 3, (
        "the table-selection rate must be measured over questions that were routed correctly, "
        "not over every question — that conflation is the whole defect"
    )
    assert cond["all_gold_tables_retrieved_tables"]["rate"] == pytest.approx(2 / 3, abs=1e-4)
    # The generation stage: two answerable, one right.
    assert cond["correct"] == {"rate": 0.5, "n": 1, "of": 2, "why": None}
    assert out["end_to_end"] == {"rate": 0.25, "n": 1, "of": 4, "why": None}


def test_an_empty_stage_is_unmeasured_and_not_a_rate_of_zero() -> None:
    """A stage nothing reached is unmeasured, not 0.0."""
    from governed_bi.eval.datalake import retrieval_funnel

    rows = [
        {
            "question_id": "1",
            "db_id": "sales",
            "retrieved_tables_schemas": ["ops"],
            "retrieved_tables": [],
            "outcome": "answered",
            "correct": False,
        }
    ]
    out = retrieval_funnel(rows, {"1": "SELECT 1 FROM sales.customers"})
    stage = out["conditional"]["all_gold_tables_retrieved_tables"]
    assert stage["rate"] is None, "a stage nothing reached must not report 0.0"
    assert stage["of"] == 0
    assert stage["why"], "an absence without a reason is a forgotten assignment"


def test_a_row_with_no_gold_sql_is_counted_rather_than_dropped() -> None:
    """A row with no gold is counted, not dropped from the denominator."""
    from governed_bi.eval.datalake import retrieval_funnel

    rows = _funnel_rows() + [{"question_id": "99", "db_id": "sales", "retrieved_tables": []}]
    out = retrieval_funnel(rows, {str(i): "SELECT 1 FROM sales.customers" for i in range(1, 5)})
    assert out["counts"]["no_gold_sql"] == 1
    assert out["counts"]["rows"] == 5
    assert out["counts"]["scorable"] == 4


def test_table_less_gold_leaves_the_funnel_denominator() -> None:
    """A ``VALUES`` gold reads no table, so it is not a coverage miss."""
    from governed_bi.eval.datalake import retrieval_funnel

    rows = [{"question_id": "1", "db_id": "sales", "retrieved_tables": [], "outcome": "answered"}]
    out = retrieval_funnel(rows, {"1": 'SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")'})
    assert out["counts"]["gold_reads_no_table"] == 1
    assert out["counts"]["scorable"] == 0


def test_the_table_less_population_is_published_with_its_own_ex() -> None:
    """Leaving the denominator is not leaving the report. An empty set is unmeasured, not zero."""
    from governed_bi.eval.datalake import retrieval_funnel

    folded = 'SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")'
    rows = [
        {"question_id": "a", "db_id": "sales", "retrieved_tables": [], "outcome": "answered",
         "correct": True},
        {"question_id": "b", "db_id": "sales", "retrieved_tables": [], "outcome": "answered",
         "correct": False},
        {"question_id": "c", "db_id": "sales", "retrieved_tables": [], "outcome": "answered",
         "correct": False},
        # Answered but ungradeable: it must not count as a wrong answer here either.
        {"question_id": "d", "db_id": "sales", "retrieved_tables": [], "outcome": "answered",
         "correct": None},
    ]
    out = retrieval_funnel(rows, dict.fromkeys("abcd", folded))

    assert out["counts"]["gold_reads_no_table"] == 4
    assert out["counts"]["gold_reads_no_table_graded"] == 3, "the ungradeable row is not wrong"
    assert out["gold_reads_no_table"] == {
        "rate": pytest.approx(1 / 3, abs=1e-4), "n": 1, "of": 3, "why": None
    }
    # And with nothing in the set, an absence rather than an EX of zero.
    empty = retrieval_funnel(
        [{"question_id": "1", "db_id": "sales", "retrieved_tables": ["sales.customers"],
          "outcome": "answered", "correct": True}],
        {"1": "SELECT 1 FROM sales.customers"},
    )
    assert empty["gold_reads_no_table"]["rate"] is None
    assert empty["gold_reads_no_table"]["why"]


def test_an_unparseable_gold_is_not_a_gold_that_reads_no_table() -> None:
    """An unparseable gold is not a gold that reads no table. The two want different follow-ups."""
    from governed_bi.eval.datalake import retrieval_funnel

    out = retrieval_funnel(
        [
            {"question_id": "junk", "db_id": "sales", "retrieved_tables": [], "outcome": "answered"},
            {"question_id": "folded", "db_id": "sales", "retrieved_tables": [], "outcome": "answered"},
        ],
        {
            "junk": "NOT SQL AT ALL ((( ;",
            "folded": 'SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")',
        },
    )
    assert out["counts"]["gold_sql_unparsed"] == 1
    assert out["counts"]["gold_reads_no_table"] == 1
