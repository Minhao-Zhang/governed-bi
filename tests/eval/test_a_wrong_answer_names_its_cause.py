"""``error_type`` was ``None`` on all 78 answered-but-wrong rows of the arm that motivated this.

The engine recorded *that* an answer was wrong and nothing about *why*, so a treatment could be
aimed at a failure class holding 8 of 78 rows with no way to know it in advance. Those figures come
from RyanChenJung/governed-bi-utkuai@12c3e15, the fork this module was taken from -- see
``eval/attribution.py``'s docstring, which says which numbers are theirs and why they are kept.
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


def test_a_row_that_could_not_be_graded_has_no_cause() -> None:
    """``correct: None`` means the instrument had no answer key -- ``grade_turn``'s third value,
    which ``harness.py`` propagates rather than coercing precisely so a ``missing_gold`` row is
    not counted as a wrong answer.

    The implementation contradicted its own docstring: it promises ``None`` for a row that is
    "not answered-and-wrong", and a truthiness test on ``correct`` treated ``None`` as wrong. So
    a question with no gold was classified anyway -- ``unparseable``, because the empty
    ``gold_sql`` fails to parse -- and the artifact blamed the model for a missing answer key."""
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
    """In sqlglot a CTE *reference* parses to ``exp.Table``, so ``find_all(exp.Table)`` collected
    ``ranked`` here and the row read as querying a table gold never touches. Both statements read
    only ``sales``; restructuring a query into a CTE is not a wrong table choice.

    This mislabelled 9 of the fork's 23 ``table_set_differs`` rows, which was enough to make that
    bucket look like the largest and license a curation arm against it."""
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
    a corpus-curation arm exists to fix -- and must not be hidden behind a projection label."""
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


def test_the_harness_row_carries_the_cause_in_its_own_field() -> None:
    """The cause goes in ``failure_cause``, so counting the next arm's target during the run
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
    row = project_turn(state, question=question, arm="test")

    assert row["correct"] is False
    assert row["failure_cause"] == FailureCause.projection_extra.value


def test_the_classifier_never_writes_error_type() -> None:
    """``register/record.py`` declares ``error_type`` as the exception CLASS of a turn that
    raised, so a consumer reading ``error_type is not None`` as "this turn raised" is reading the
    declaration. Writing taxonomy labels there took the crashed count from 0 to 78 on the fork's
    own baseline, which is why the taxonomy gets its own field here.

    An answered-and-wrong row must therefore leave ``error_type`` null while naming its cause,
    and a row that really did raise must keep the class name it arrived with."""
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
        arm="test",
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
        arm="test",
    )

    assert raised["error_type"] == "ValueError"
    assert raised["failure_cause"] is None


def test_a_run_concurrently_crash_never_reaches_the_classifier(tmp_path: Path) -> None:
    """``_run_concurrently``'s exception handler (``harness.py``'s ``run_index``) builds its
    own minimal row dict inline -- ``error_type`` set to the exception's class name -- and
    returns it straight from ``run_arm``. This row never calls ``project_turn`` at all, so it
    carries no ``failure_cause`` key and the classifier cannot reach it. Pinned here because
    ``test_the_row_names_its_configuration.py`` already drives this exact branch (``workers=2``,
    an exploding ``compiled.invoke``) but never asserts on ``error_type``."""
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

    class _Exploding:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("provider went away")

    # `compile_durable`, not `compile_graph`: ADR 0014 gave the harness a durable checkpointer,
    # opened on the graph's own pinned loop, and renamed the compiler that opens it along with it.
    original = harness.compile_durable
    harness.compile_durable = lambda *a, **k: _Exploding()  # type: ignore[assignment]
    try:
        rows = harness.run_arm(
            [{"question_id": "q1", "question": "how many customers", "db_id": "main"}],
            stub_arm(connector=connector),
            workers=2,
            connector_factory=lambda: connector,
        )
    finally:
        harness.compile_durable = original  # type: ignore[assignment]

    assert rows[0]["error_type"] == "RuntimeError"
