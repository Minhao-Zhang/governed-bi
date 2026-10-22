"""A truthiness test on ``correct`` treated a missing gold as a wrong answer."""

from __future__ import annotations

from pathlib import Path

from governed_bi.eval.attribution import FailureCause, attribute


def _row(pred: str, gold: str, **over) -> dict:
    base = {
        "outcome": "answered",
        "correct": False,
        "generated_sql": pred,
        "gold_sql": gold,
        "touched_decoy": False,
    }
    base.update(over)
    return base


def test_a_correct_row_has_no_cause() -> None:
    """The classifier explains failures. Asked about a success it must say nothing, not
    invent a category — a cause attached to a correct answer would pollute every count."""
    row = _row("SELECT a FROM t", "SELECT a FROM t", correct=True)

    assert attribute(row) is None


def test_a_row_that_could_not_be_graded_has_no_cause() -> None:
    """A truthiness test on ``correct`` classified a missing gold as ``unparseable``."""
    row = _row(
        "SELECT a, b FROM t", "", correct=None, grade_detail="missing_gold"
    )

    assert attribute(row) is None


def test_an_unanswered_row_has_no_cause() -> None:
    """A refusal and a clarification are outcomes, not wrong answers. Counting them as
    failures is how that run's two denominators (all-questions vs attempted-only) drifted
    apart."""
    assert attribute(_row("", "SELECT a FROM t", outcome="refused")) is None
    assert attribute(_row("", "SELECT a FROM t", outcome="clarification")) is None


def test_extra_projection_columns() -> None:
    """The most-reported defect across this research line -- J-06, G-05, H-03, K2-a, K2-c,
    B-09, L1-b -- and the one never productized as a check. It is decided by arity alone."""
    row = _row("SELECT name, id, city FROM t", "SELECT name FROM t")

    assert attribute(row) is FailureCause.projection_extra


def test_missing_projection_columns() -> None:
    row = _row("SELECT name FROM t", "SELECT name, id FROM t")

    assert attribute(row) is FailureCause.projection_missing


def test_a_different_table_set() -> None:
    """Ranked above projection: querying the wrong table makes the column list irrelevant."""
    row = _row("SELECT a FROM wrong_table", "SELECT a FROM right_table")

    assert attribute(row) is FailureCause.table_set_differs


def test_a_cte_name_is_not_counted_as_a_table_gold_does_not_have() -> None:
    """A CTE reference parses as ``exp.Table``, so ``find_all`` counted a name gold never touches."""
    row = _row(
        "WITH ranked AS (SELECT id, n FROM sales) SELECT id, n FROM ranked",
        "SELECT id, n FROM sales",
    )

    assert attribute(row) is not FailureCause.table_set_differs
    assert attribute(row) is FailureCause.unattributed


def test_a_cte_does_not_hide_a_table_the_prediction_really_added() -> None:
    """The paired half: subtracting CTE names must not also subtract a real extra table. Without
    it the fix would turn a genuine wrong-table row into `unattributed` and shrink the bucket
    for the opposite reason."""
    row = _row(
        "WITH ranked AS (SELECT id FROM wrong_table) SELECT id FROM ranked",
        "SELECT id FROM sales",
    )

    assert attribute(row) is FailureCause.table_set_differs


def test_decoy_contact_is_read_from_the_row_not_the_sql() -> None:
    """``touched_decoy`` is computed against BIRD-Obfuscation's manifest, which the SQL text
    cannot reveal. Ranked first because a decoy column is a *semantic-layer* failure -- the class
    a corpus-curation config exists to fix -- and must not be hidden behind a projection label."""
    row = _row("SELECT a, b FROM t", "SELECT a FROM t", touched_decoy=True)

    assert attribute(row) is FailureCause.decoy_contact


def test_an_aggregation_that_gold_does_not_have() -> None:
    row = _row("SELECT COUNT(a) FROM t", "SELECT a FROM t")

    assert attribute(row) is FailureCause.aggregation_differs


def test_a_filter_on_a_column_gold_does_not_filter_on() -> None:
    row = _row("SELECT a FROM t WHERE b = 1", "SELECT a FROM t WHERE c = 1")

    assert attribute(row) is FailureCause.filter_differs


def test_shapes_agree_and_the_answer_is_still_wrong() -> None:
    """The bucket. Same tables, same arity, same aggregates, same filter columns, and
    the result still differs -- a literal, a join direction, a gold defect. Task 3's judge
    reads exactly this bucket, and its size decides whether Task 3 runs at all."""
    row = _row("SELECT a FROM t WHERE b = 1", "SELECT a FROM t WHERE b = 2")

    assert attribute(row) is FailureCause.unattributed


def test_sql_that_does_not_parse_is_named_as_such() -> None:
    """Not ``unattributed``: an unparseable statement is a known cause, and folding it into
    the residual would send Task 3's judge to explain a syntax error."""
    row = _row("SELECT FROM WHERE", "SELECT a FROM t")

    assert attribute(row) is FailureCause.unparseable


def test_no_predicted_sql_at_all_is_named_missing_prediction() -> None:
    """All 8 baseline rows in ``unparseable`` had ``generated_sql: null`` -- the
    engine answered with no statement, not with one that fails to parse. Conflating the two
    would hide the population that never queried anything at all."""
    row = _row(None, "SELECT a FROM t")

    assert attribute(row) is FailureCause.missing_prediction


def test_an_empty_string_prediction_is_also_missing_not_unparseable() -> None:
    row = _row("", "SELECT a FROM t")

    assert attribute(row) is FailureCause.missing_prediction


def test_a_whitespace_only_prediction_is_also_missing_not_unparseable() -> None:
    row = _row("   \n\t", "SELECT a FROM t")

    assert attribute(row) is FailureCause.missing_prediction


def test_a_malformed_but_present_prediction_is_still_unparseable() -> None:
    """The regression test for the new branch: a statement that exists but will not parse
    must not fall into ``missing_prediction`` just because it also fails to become a tree."""
    row = _row("SELECT FROM WHERE", "SELECT a FROM t")

    assert attribute(row) is FailureCause.unparseable


def test_the_harness_row_carries_the_cause_in_its_own_field() -> None:
    """The cause goes in ``failure_cause``, so counting the next config's target during the run
    does not cost the meaning of ``error_type``."""
    from governed_bi.eval.harness import project_turn

    state = {
        "answer": {
            "outcome": "answered",
            "record": {"generated_sql": "SELECT a, b FROM t"},
        }
    }
    question = {
        "question_id": "q1",
        "gold_sql": "SELECT a FROM t",
        "gold_columns": ["a"],
        "gold_rows": [[1]],
    }
    row = project_turn(state, question=question, config="test")

    assert row["correct"] is False
    assert row["failure_cause"] == FailureCause.projection_extra.value


def test_the_classifier_never_writes_error_type() -> None:
    """``error_type`` is the exception class of a raised turn; the taxonomy is a separate field."""
    from governed_bi.eval.harness import project_turn

    question = {
        "question_id": "q2",
        "gold_sql": "SELECT a FROM t",
        "gold_columns": ["a"],
        "gold_rows": [[1]],
    }
    wrong = project_turn(
        {"answer": {"outcome": "answered", "record": {"generated_sql": "SELECT a, b FROM t"}}},
        question=question,
        config="test",
    )

    assert wrong["failure_cause"] == FailureCause.projection_extra.value
    assert wrong["error_type"] is None

    raised = project_turn(
        {
            "answer": {
                "outcome": "crashed",
                "record": {"error_type": "ValueError", "generated_sql": None},
            }
        },
        question=question,
        config="test",
    )

    assert raised["error_type"] == "ValueError"
    assert raised["failure_cause"] is None


def test_a_run_concurrently_crash_never_reaches_the_classifier(tmp_path: Path) -> None:
    """A crash row from ``_run_concurrently`` never calls ``project_turn``, so it has no cause."""
    import sqlite3

    import governed_bi.eval.harness as harness
    from governed_bi.datasource.sqlite import SqliteConnector
    from governed_bi.eval.configs import stub_config

    db = tmp_path / "customers.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER)")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)

    class _Exploding:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("provider went away")

    # `compile_durable`, not `compile_graph`: ADR 0014 gave the harness a durable checkpointer,
    # opened on the graph's own pinned loop, and renamed the compiler that opens it along with it.
    original = harness.compile_durable
    harness.compile_durable = lambda *a, **k: _Exploding()  # type: ignore[assignment]
    try:
        rows = harness.run_config(
            [{"question_id": "q1", "question": "how many customers", "db_id": "main"}],
            stub_config(connector=connector),
            workers=2,
            connector_factory=lambda: connector,
        )
    finally:
        harness.compile_durable = original  # type: ignore[assignment]

    assert rows[0]["error_type"] == "RuntimeError"
