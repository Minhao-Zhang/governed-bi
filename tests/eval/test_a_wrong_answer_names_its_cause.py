"""``error_type`` was ``None`` on all 78 answered-but-wrong rows of experiment 008.

The engine recorded *that* an answer was wrong and nothing about *why*, so the Setup Wizard
arm could be aimed at a failure class holding 8 of 78 rows with no way to know it in advance.
These tests pin the categories that let a future arm state its target before it runs.

**Attribution never changes a grade.** Every row here arrives already graded; the classifier
explains ``correct: false`` and cannot set it.
"""

from __future__ import annotations

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
