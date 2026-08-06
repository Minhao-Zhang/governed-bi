"""Acceptance tests for Parcel G — authored against the plan, not the impl.

Effects asserted with hand-built fixtures. Do not re-derive gate logic here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from governed_bi.datasource.sqlite import SqliteConnector
from governed_bi.eval.arms import oracle_arm, stub_arm
from governed_bi.eval.grade import grade_turn, result_fingerprint
from governed_bi.eval.harness import run_arm, run_comparison
from governed_bi.eval.oracle import oracle_grade
from governed_bi.eval.report import (
    arm_population,
    comparison_quotable,
    context_hashes_distinct,
    headline_ex,
    paired_ex,
    summarise,
)
from governed_bi.measure.gates import Verdict
from governed_bi.measure.stats import mcnemar


def _fixture_db(tmp_path: Path) -> tuple[Path, SqliteConnector]:
    db = tmp_path / "customers.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO customers VALUES (1, 'a'), (2, 'b')")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)
    connector._connect()  # noqa: SLF001
    return db, connector


def _questions() -> list[dict]:
    return [
        {
            "question_id": "q1",
            "question": "how many customers",
            "db_id": "main",
            "gold_sql": "SELECT COUNT(*) AS n FROM customers",
        },
        {
            "question_id": "q2",
            "question": "list customer ids",
            "db_id": "main",
            "gold_sql": "SELECT id FROM customers ORDER BY id",
        },
    ]


def _clean_row(qid: str, **overrides) -> dict:
    row = {
        "question_id": qid,
        "correct": True,
        "crashed": False,
        "context_hash": f"hash-{qid}-a",
        "facet_channels": {"schema": "ran"},
        "facet_degraded": False,
        "guardrail_error": False,
        "re_served": False,
        "negative_failed_open": False,
        "outcome": "answered",
    }
    row.update(overrides)
    return row


def test_crash_stays_crashed_not_refused() -> None:
    grade = grade_turn(outcome="crashed")
    assert grade["correct"] is False
    assert grade["detail"] == "crashed"
    refused = grade_turn(outcome="refused")
    assert refused["detail"] == "refused"
    assert grade["detail"] != refused["detail"]


def test_oracle_grades_gold_sql_ex_one(tmp_path: Path) -> None:
    _, connector = _fixture_db(tmp_path)
    q = _questions()[0]
    row = oracle_grade(q, connector)
    assert row["outcome"] == "answered"
    assert row["correct"] is True
    assert row["crashed"] is False

    arm = oracle_arm(connector=connector)
    rows = run_arm(_questions(), arm)
    assert all(r["correct"] for r in rows)
    pop = arm_population(rows, label="oracle")
    ex = headline_ex(pop)
    assert ex.is_measured and ex.value == 1.0


def test_context_hash_distinctness_pass_and_fail() -> None:
    a = arm_population(
        [_clean_row(f"q{i}", context_hash=f"a-{i}") for i in range(20)],
        label="arm_a",
    )
    b = arm_population(
        [_clean_row(f"q{i}", context_hash=f"b-{i}") for i in range(20)],
        label="arm_b",
    )
    same = arm_population(
        [_clean_row(f"q{i}", context_hash=f"a-{i}") for i in range(20)],
        label="arm_same",
    )
    ok = context_hashes_distinct(a, b)
    assert ok.verdict is Verdict.passed
    bad = context_hashes_distinct(a, same)
    assert bad.verdict is Verdict.failed


def test_mcnemar_uses_same_population_as_headline() -> None:
    rows_a = [_clean_row(f"q{i}", correct=(i % 2 == 0)) for i in range(10)]
    rows_b = [_clean_row(f"q{i}", correct=True) for i in range(10)]
    a = arm_population(rows_a, label="a")
    b = arm_population(rows_b, label="b")
    shared = a.units & b.units
    a_s = a.restrict(lambda r: str(r["question_id"]) in shared, "shared questions")
    b_s = b.restrict(lambda r: str(r["question_id"]) in shared, "shared questions")
    head_a = headline_ex(a_s)
    head_b = headline_ex(b_s)
    result = paired_ex(a_s, b_s)
    again = mcnemar(a_s, b_s, "correct")
    assert again.n_pairs == result.n_pairs == a_s.n
    assert again.only_a == result.only_a and again.only_b == result.only_b
    assert head_a.is_measured and head_b.is_measured
    assert result.delta.is_measured
    assert result.delta.value == pytest.approx(head_b.value - head_a.value)


def test_quotable_false_when_crash_rate_positive() -> None:
    a = arm_population(
        [_clean_row(f"q{i}", context_hash=f"a{i}") for i in range(10)], label="clean"
    )
    b = arm_population(
        [
            _clean_row("q0", correct=False, crashed=True, outcome="crashed", context_hash="b0"),
            *[_clean_row(f"q{i}", context_hash=f"b{i}") for i in range(1, 10)],
        ],
        label="crashy",
    )
    ok, _results_a, results_b, _ctx = comparison_quotable(a, b)
    assert not ok
    assert any(r.field == "outcome" and r.verdict is Verdict.failed for r in results_b)


def test_eval_imports_one_mcnemar() -> None:
    import governed_bi.eval.report as report_mod
    import governed_bi.measure.stats as stats_mod

    assert report_mod.mcnemar is stats_mod.mcnemar


def test_stub_arm_invokes_serve(tmp_path: Path) -> None:
    _, connector = _fixture_db(tmp_path)
    rows = run_arm(_questions()[:1], stub_arm(connector=connector))
    assert len(rows) == 1
    assert rows[0]["outcome"] in {"answered", "refused", "crashed"}
    assert rows[0]["crashed"] == (rows[0]["outcome"] == "crashed")
    assert "question_id" in rows[0]


def test_result_fingerprint_order_insensitive() -> None:
    a = result_fingerprint(["id"], [[2], [1]], order_sensitive=False)
    b = result_fingerprint(["id"], [[1], [2]], order_sensitive=False)
    assert a == b
    c = result_fingerprint(["id"], [[2], [1]], order_sensitive=True)
    d = result_fingerprint(["id"], [[1], [2]], order_sensitive=True)
    assert c != d


def test_summarise_pair_runs(tmp_path: Path) -> None:
    _, connector = _fixture_db(tmp_path)
    questions = _questions()
    arms = run_comparison(
        questions,
        [oracle_arm(connector=connector), stub_arm(connector=connector)],
    )
    summary = summarise(arms, pair=("oracle", "stub"))
    assert "arms" in summary and "oracle" in summary["arms"]
    assert summary["comparison"]["pair"] == ("oracle", "stub")


def test_a_different_column_alias_is_not_a_wrong_answer() -> None:
    """EX compares **values**, as BIRD's own evaluation does.

    The fingerprint included column names, so ``SELECT COUNT(*) AS paper_count`` graded wrong
    against a gold of ``SELECT COUNT(*)`` with both returning 100 — and the penalty tracked
    how verbose the model was about aliasing rather than whether it was right. Measured on the
    xhigh arm: 5% of answerable-but-wrong turns were exactly this.
    """
    from governed_bi.eval.grade import grade_results, result_fingerprint

    assert result_fingerprint(["paper_count"], [[100]]) == result_fingerprint(["count"], [[100]])
    verdict = grade_results(
        pred_columns=["paper_count"],
        pred_rows=[[100]],
        gold_columns=["count"],
        gold_rows=[[100]],
    )
    assert verdict["correct"] is True


def test_the_relaxation_stops_at_names() -> None:
    """The paired negatives. Loosening the comparison must not make a wrong answer pass.

    Over-answering is still wrong: an extra column makes a longer row tuple, which is how
    BIRD catches it. And element order **within** a row still matters — ``(url, 2028)`` and
    ``(2028, url)`` answer different questions, and this exact pair appeared in the arm.
    """
    from governed_bi.eval.grade import result_fingerprint

    assert result_fingerprint(["a"], [[1]]) != result_fingerprint(["a", "b"], [[1, 2]]), (
        "an extra column must not compare equal -- that is over-answering"
    )
    assert result_fingerprint(["a", "b"], [["url", 2028]]) != result_fingerprint(
        ["b", "a"], [[2028, "url"]]
    ), "swapping the values within a row is a different answer"
    assert result_fingerprint(["a"], [[1]]) != result_fingerprint(["a"], [[2]]), (
        "different values must not compare equal"
    )
    # Row order is the one thing relaxed, and only when the question allows it.
    assert result_fingerprint(["a"], [[1], [2]]) == result_fingerprint(["a"], [[2], [1]])
    assert result_fingerprint(["a"], [[1], [2]], order_sensitive=True) != result_fingerprint(
        ["a"], [[2], [1]], order_sensitive=True
    )


def test_table_coverage_refuses_rows_that_do_not_carry_licensed() -> None:
    """The EX ceiling must not read 0.000 because the producer named the field differently.

    ``routing_recall`` published ``licensed_schemas`` and not ``licensed``, and
    ``table_coverage`` reads exactly ``licensed`` — so the free harness fed to the function
    this module documents as *"the EX ceiling"* reported ``all_gold_tables_licensed: 0.0`` for
    two arms whose schema recall was 0.851 and 0.877, with ``reached_gold`` in the very same
    rows proving the tables had been licensed. A zero is a publishable number; a ``KeyError``
    is not, and that asymmetry is the whole point.

    Absent and empty stay different facts: a row that carries ``licensed: []`` licensed
    nothing, which is a measurement this counts.
    """
    from governed_bi.eval.datalake import table_coverage

    gold = {"q1": "SELECT * FROM restaurant.generalinfo"}

    with pytest.raises(KeyError, match="licensed"):
        table_coverage([{"question_id": "q1", "licensed_schemas": ["restaurant"]}], gold)

    empty = table_coverage([{"question_id": "q1", "licensed": []}], gold)
    assert empty["all_gold_tables_licensed"] == 0.0, "licensed nothing is a real zero"
    assert empty["n"] == 1

    covered = table_coverage(
        [{"question_id": "q1", "licensed": ["restaurant.generalinfo"]}], gold
    )
    assert covered["all_gold_tables_licensed"] == 1.0


def test_routing_recall_rows_carry_what_table_coverage_reads() -> None:
    """The two functions' shapes are locked together, not merely documented as compatible.

    Asserted over the *keys*, because the defect above was a spelling mismatch between one
    module's producer and its consumer — the kind a comment cannot hold shut.
    """
    import inspect

    from governed_bi.eval import datalake

    source = inspect.getsource(datalake.routing_recall)
    assert '"licensed": licensed' in source, (
        "routing_recall must publish the table ids under `licensed`; table_coverage reads "
        "that key and nothing else"
    )


def test_a_gold_statement_that_reads_no_table_is_not_a_coverage_miss() -> None:
    """13 of 114 sampled questions have a constant-folded gold statement.

    ``SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")`` reads nothing. It fell through
    ``needed and hits == len(needed)`` into the ``none`` bucket, so it counted as "no gold table
    licensed" on every arm -- an unconditional miss no corpus change could fix, holding the
    ceiling at 101/114 = 0.886 and deflating every published coverage figure by a fixed 11.4%.

    Excluded from the denominator, the way ``gold_sql_unparsed`` already handles a statement the
    metric cannot read, and **counted** in its own field, because a silently smaller denominator
    is the same defect pointing the other way.
    """
    from governed_bi.eval.datalake import table_coverage

    folded = 'SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")'
    rows = [
        {"question_id": "folded", "licensed": ["restaurant.generalinfo"]},
        {"question_id": "real", "licensed": ["restaurant.generalinfo"]},
    ]
    out = table_coverage(
        rows, {"folded": folded, "real": "SELECT * FROM restaurant.generalinfo"}
    )

    assert out["gold_reads_no_table"] == 1
    assert out["n"] == 1, "the table-less question must leave the denominator"
    assert out["all_gold_tables_licensed"] == 1.0, (
        "the one scorable question was fully covered; the folded one must not drag it to 0.5"
    )
    assert out["none_licensed"] == 0.0


def test_connect_seeds_its_tree_deterministically() -> None:
    """``next(iter(set_of_strings))`` made the Steiner tree depend on the process hash seed.

    Python randomises string hashing per process, so the greedy builder started from a different
    terminal in every run and added different -- equally valid -- Steiner points. Those points
    enter ``licensed``, which is what ``table_coverage`` reads, so every cross-session coverage
    comparison carried it as noise: one question of 114 was observed, and a direct probe produced
    three distinct Steiner sets across five hash seeds.

    Asserted as "the seed is the sorted-first terminal" rather than by re-running under two hash
    seeds, which a test in one process cannot do.
    """
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

    # Both the terminal order AND the edge order are shuffled. Shuffling only the terminals
    # exercises the tree seed, and a fix to the seed alone passed that while the probe across
    # real hash seeds still produced three distinct Steiner sets -- the queue order and the
    # neighbour order in the BFS are two further places an equal-length tie is broken. Edge order
    # is what varies the `adj` set insertion order, so it is what reaches those two.
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
        "points enter `licensed`, which table_coverage reads."
    )


def test_the_dataset_exclusion_lists_are_read_by_their_real_names(tmp_path: Path) -> None:
    """Both drivers asked for ``question_ids``, a key this file has never carried.

    So ``order_sensitive_qids.json`` yielded ``set()`` on every run and the 97
    order-sensitive plus 10 degenerate golds the dataset says to exclude were graded as
    ordinary engine misses. The ``or []`` is what let it survive for so long: an empty
    exclusion set reads exactly like a dataset that declares no exclusions.
    """
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
            "licensed_schemas": ["ops"],
            "licensed": ["ops.things"],
            "outcome": "answered",
            "correct": False,
        },
        # Right schema, but the gold table did not survive to `licensed`.
        {
            "question_id": "2",
            "db_id": "sales",
            "licensed_schemas": ["sales"],
            "licensed": ["sales.other"],
            "outcome": "answered",
            "correct": False,
        },
        # Everything licensed, model answered, wrong result. This is a *generation* loss.
        {
            "question_id": "3",
            "db_id": "sales",
            "licensed_schemas": ["sales"],
            "licensed": ["sales.customers"],
            "outcome": "answered",
            "correct": False,
        },
        # Everything licensed and correct.
        {
            "question_id": "4",
            "db_id": "sales",
            "licensed_schemas": ["sales"],
            "licensed": ["sales.customers"],
            "outcome": "answered",
            "correct": True,
        },
    ]


def test_the_funnel_separates_routing_from_table_selection_from_generation() -> None:
    """The measurement the repo could not make, and the reason a day was spent on the wrong fix.

    ``summarise_routing`` reports schema recall over all rows and ``table_coverage`` reports
    gold-table coverage over all rows; nothing joined them, so "coverage 0.70 against recall@3
    0.85" could not distinguish a routing failure from a table-selection failure. Those want
    opposite work.
    """
    from governed_bi.eval.datalake import retrieval_funnel

    gold_sql = {str(i): "SELECT 1 FROM sales.customers" for i in range(1, 5)}
    out = retrieval_funnel(_funnel_rows(), gold_sql)
    counts, cond = out["counts"], out["conditional"]

    assert counts["scorable"] == 4
    assert counts["schema_routed"] == 3, "row 1 routed to the wrong schema"
    assert counts["tables_in_routed_schemas"] == 3
    assert counts["all_gold_tables_licensed"] == 2, "row 2 lost the table after routing"
    assert counts["correct"] == 1

    # Each stage is conditional on the one above, and each carries its own denominator.
    assert cond["schema_routed"] == {"rate": 0.75, "n": 3, "of": 4, "why": None}
    assert cond["all_gold_tables_licensed"]["of"] == 3, (
        "the table-selection rate must be measured over questions that were routed correctly, "
        "not over every question — that conflation is the whole defect"
    )
    assert cond["all_gold_tables_licensed"]["rate"] == pytest.approx(2 / 3, abs=1e-4)
    # The generation stage: two answerable, one right.
    assert cond["correct"] == {"rate": 0.5, "n": 1, "of": 2, "why": None}
    assert out["end_to_end"] == {"rate": 0.25, "n": 1, "of": 4, "why": None}


def test_an_empty_stage_is_unmeasured_and_not_a_rate_of_zero() -> None:
    """``or 1`` elsewhere in this module turns a zero-row population into a real-looking 0.000.

    ``Measured.rate`` refuses that, and the reason survives into the artifact rather than being
    rendered as a string that sorts like a number.
    """
    from governed_bi.eval.datalake import retrieval_funnel

    rows = [
        {
            "question_id": "1",
            "db_id": "sales",
            "licensed_schemas": ["ops"],
            "licensed": [],
            "outcome": "answered",
            "correct": False,
        }
    ]
    out = retrieval_funnel(rows, {"1": "SELECT 1 FROM sales.customers"})
    stage = out["conditional"]["all_gold_tables_licensed"]
    assert stage["rate"] is None, "a stage nothing reached must not report 0.0"
    assert stage["of"] == 0
    assert stage["why"], "an absence without a reason is a forgotten assignment"


def test_a_row_with_no_gold_sql_is_counted_rather_than_dropped() -> None:
    """``table_coverage`` does ``if not sql: continue`` with no counter — a silent denominator
    shrink, which is the same defect as counting the row wrongly but quieter."""
    from governed_bi.eval.datalake import retrieval_funnel

    rows = _funnel_rows() + [{"question_id": "99", "db_id": "sales", "licensed": []}]
    out = retrieval_funnel(rows, {str(i): "SELECT 1 FROM sales.customers" for i in range(1, 5)})
    assert out["counts"]["no_gold_sql"] == 1
    assert out["counts"]["rows"] == 5
    assert out["counts"]["scorable"] == 4


def test_table_less_gold_leaves_the_funnel_denominator() -> None:
    """A constant-folded ``VALUES`` gold reads no table, so it cannot be a coverage miss.

    127 of the 1 351 test golds are this shape; counting them as misses deflated every
    coverage figure by a fixed ~9.4% until 2026-08-05.
    """
    from governed_bi.eval.datalake import retrieval_funnel

    rows = [{"question_id": "1", "db_id": "sales", "licensed": [], "outcome": "answered"}]
    out = retrieval_funnel(rows, {"1": 'SELECT "v"."c0" FROM (VALUES (121.0)) AS "v"("c0")'})
    assert out["counts"]["gold_reads_no_table"] == 1
    assert out["counts"]["scorable"] == 0
