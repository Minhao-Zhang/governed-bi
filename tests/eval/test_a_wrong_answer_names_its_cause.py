"""``error_type`` was ``None`` on all 78 answered-but-wrong rows of experiment 008.

The engine recorded *that* an answer was wrong and nothing about *why*, so the Setup Wizard
arm could be aimed at a failure class holding 8 of 78 rows with no way to know it in advance.
These tests pin the categories that let a future arm state its target before it runs.

**Attribution never changes a grade.** Every row here arrives already graded; the classifier
explains ``correct: false`` and cannot set it.
"""

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


def test_an_unanswered_row_has_no_cause() -> None:
    """A refusal and a clarification are outcomes, not wrong answers. Counting them as
    failures is how 008's two denominators (all-questions vs attempted-only) drifted apart."""
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


def test_decoy_contact_is_read_from_the_row_not_the_sql() -> None:
    """``touched_decoy`` is computed against BIRD-Obfuscation's manifest, which the SQL text
    cannot reveal. Ranked first because a decoy column is a *semantic-layer* failure -- the
    class ``curator/`` exists to fix -- and must not be hidden behind a projection label."""
    row = _row("SELECT a, b FROM t", "SELECT a FROM t", touched_decoy=True)

    assert attribute(row) is FailureCause.decoy_contact


def test_an_aggregation_that_gold_does_not_have() -> None:
    row = _row("SELECT COUNT(a) FROM t", "SELECT a FROM t")

    assert attribute(row) is FailureCause.aggregation_differs


def test_a_filter_on_a_column_gold_does_not_filter_on() -> None:
    row = _row("SELECT a FROM t WHERE b = 1", "SELECT a FROM t WHERE c = 1")

    assert attribute(row) is FailureCause.filter_differs


def test_shapes_agree_and_the_answer_is_still_wrong() -> None:
    """The honest bucket. Same tables, same arity, same aggregates, same filter columns, and
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
    """All 8 baseline rows in ``unparseable`` actually had ``generated_sql: null`` -- the
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


def test_the_harness_row_carries_the_cause() -> None:
    """``error_type`` already exists on the row and was ``None`` on all 78 of 008's wrong
    answers. Populating it here is what makes the next arm's target countable *during* the
    run rather than in a script afterwards."""
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
    row = project_turn(state, question=question, arm="test")

    assert row["correct"] is False
    assert row["error_type"] == FailureCause.projection_extra.value


def test_a_crashed_rows_error_type_survives_the_classifier() -> None:
    """The serve graph can stamp ``error_type`` onto a row before ``project_turn`` ever sees
    it: ``serve/wrap.py``'s ``wrap_node`` turns a node's raised exception into
    ``state["failure"]`` rather than letting it escape ``compiled.invoke``, and
    ``serve/nodes/stamp.py`` copies that into ``record["error_type"]``. Overwriting a value
    ``project_turn`` did not compute itself would report that engine failure as a projection
    defect -- the collision the guard in ``project_turn`` exists to prevent."""
    from governed_bi.eval.harness import project_turn

    state = {
        "answer": {
            "outcome": "answered",
            "record": {"error_type": "ValueError", "generated_sql": "SELECT a, b FROM t"},
        }
    }
    question = {
        "question_id": "q2",
        "gold_sql": "SELECT a FROM t",
        "gold_columns": ["a"],
        "gold_rows": [[1]],
    }
    row = project_turn(state, question=question, arm="test")

    assert row["error_type"] == "ValueError"


def test_a_run_concurrently_crash_never_reaches_the_classifier(tmp_path: Path) -> None:
    """``_run_concurrently``'s exception handler (``harness.py``'s ``run_index``) builds its
    own minimal row dict inline -- ``error_type`` set to the exception's class name -- and
    returns it straight from ``run_arm``. This row never calls ``project_turn`` at all, which
    is why the classifier cannot corrupt it and why ``project_turn``'s guard is not what
    protects it. Pinned here because ``test_the_row_names_its_configuration.py`` already
    drives this exact branch (``workers=2``, an exploding ``compiled.invoke``) but never
    asserts on ``error_type``."""
    import sqlite3

    import governed_bi.eval.harness as harness
    from governed_bi.datasource.sqlite import SqliteConnector
    from governed_bi.eval.arms import stub_arm

    db = tmp_path / "customers.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER)")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)
    connector._connect()  # noqa: SLF001

    class _Exploding:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("provider went away")

    original = harness.compile_graph
    harness.compile_graph = lambda *a, **k: _Exploding()  # type: ignore[assignment]
    try:
        rows = harness.run_arm(
            [{"question_id": "q1", "question": "how many customers", "db_id": "main"}],
            stub_arm(connector=connector),
            workers=2,
            connector_factory=lambda: connector,
        )
    finally:
        harness.compile_graph = original  # type: ignore[assignment]

    assert rows[0]["error_type"] == "RuntimeError"
