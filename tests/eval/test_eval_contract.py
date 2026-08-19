"""Acceptance tests for Parcel G — authored against the plan, not the impl.

Effects asserted with hand-built fixtures. Do not re-derive gate logic here.

**Split, 2026-08-19**, at 44 lines under ADR 0005 §6's hard cap of 1,000. What moved is the
post-hoc half — the ``eval/datalake.py`` functions that measure a *finished* run's population
(``table_coverage``, ``routing_recall``, ``retrieval_funnel``, ``dataset_qid_lists``) — to
``test_the_funnel_denominator_never_shrinks_silently.py``. What stayed is "run an arm and grade
what it did". The two rules above apply to both halves.
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


def test_the_oracle_arm_is_unmeasured_without_an_independent_gold(tmp_path: Path) -> None:
    """Not 1.000, and not 0.000. There is nothing to claim, so it claims nothing.

    The branch this replaces called ``grade_results`` with ``gold_columns=pred[0],
    gold_rows=pred[1]`` — the executed gold fingerprinted **against itself** — so it returned
    ``correct=True`` for any statement at all, including ``SELECT 'garbage' AS wrong``. No
    producer in the repository supplies ``gold_fingerprint`` or ``gold_columns``+``gold_rows``,
    so this was the branch every run took. The predecessor of this test asserted
    ``ex.value == 1.0`` and thereby made the construction the contract.

    What it cost: the arm exists to establish that the grader is not the bottleneck, it could
    establish nothing, and it was cited as having established it — while the grader *was* a
    bottleneck, comparing every Postgres ``numeric`` cell as a string.

    ``correct=None`` is the representation, because ``Population.count`` already reads an
    absent outcome as unmeasured rather than as a zero.
    """
    _, connector = _fixture_db(tmp_path)
    row = oracle_grade(_questions()[0], connector)
    assert row["outcome"] == "answered"
    assert row["correct"] is None
    assert row["crashed"] is False
    assert row["grade_detail"].startswith("no_independent_gold")
    assert row["pred_fingerprint"], (
        "the gold statement did execute, and its digest is what a later run needs in order "
        "to become measurable"
    )

    rows = run_arm(_questions(), oracle_arm(connector=connector))
    ex = headline_ex(arm_population(rows, label="oracle"))
    assert not ex.is_measured, f"an arm with no independent gold reported {ex.value}"
    assert "correct" in ex.why


def test_the_oracle_arm_measures_against_an_independent_gold(tmp_path: Path) -> None:
    """With a reference fingerprint it is a real measurement, and it can fail.

    This is the arm doing its job: a disagreement here is the grader, the engine or the
    harness, never the model — there is no model on this path.
    """
    from governed_bi.eval.grade import result_fingerprint

    _, connector = _fixture_db(tmp_path)
    question = dict(_questions()[0])

    columns, rows, _ = connector.execute(question["gold_sql"])
    truth = result_fingerprint(list(columns), [list(r) for r in rows])

    matching = oracle_grade({**question, "gold_fingerprint": truth}, connector)
    assert matching["correct"] is True
    assert matching["grade_detail"] == "match"

    wrong = oracle_grade({**question, "gold_fingerprint": "0" * 64}, connector)
    assert wrong["correct"] is False, "the arm must be able to fail, or it is not a baseline"
    assert wrong["grade_detail"] == "result_mismatch"


def test_one_unexecutable_gold_statement_does_not_end_the_oracle_arm(tmp_path: Path) -> None:
    """It did. The arm was one list comprehension, so the exception escaped ``run_arm``.

    Every row already computed went with it — on the 1 351-question dataset that is hours of
    execution discarded by one bad statement, and the symptom is a shorter output file rather
    than an error attributable to a question.

    A gold that does not run is ``crashed`` with ``correct=None``, not ``correct=False``: it is
    a defect in the dataset or the engine, and scoring it as a wrong answer would charge the
    model for it.
    """
    _, connector = _fixture_db(tmp_path)
    questions = [
        _questions()[0],
        {"question_id": "bad", "question": "?", "db_id": "main",
         "gold_sql": "SELECT * FROM no_such_table_at_all"},
        _questions()[1],
    ]
    streamed: list[str] = []
    rows = run_arm(
        questions,
        oracle_arm(connector=connector),
        on_row=lambda _i, r: streamed.append(str(r["question_id"])),
    )

    assert [r["question_id"] for r in rows] == ["q1", "bad", "q2"]
    assert streamed == ["q1", "bad", "q2"], "on_row was ignored on the oracle path"
    bad = rows[1]
    assert bad["crashed"] is True
    assert bad["correct"] is None
    assert bad["grade_detail"].startswith("gold_exec_failed:")
    assert bad["error_type"]


def test_context_hash_is_an_existence_check_not_a_treatment_test() -> None:
    """Rewritten 2026-08-11 for audit D9. It used to assert the opposite of the second case.

    Identical hashes on every shared question no longer fail: distinctness measured retrieval
    nondeterminism, not treatment change, and passed at 0.9993 on a seed-only null pair. The
    treatment judgement moved to ``report.knobs_comparable``, which reads declared knobs.

    What this gate still owes a caller is coverage — a shared question where either arm
    assembled no context cannot be compared on that question.
    """
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
    assert context_hashes_distinct(a, b).verdict is Verdict.passed
    assert context_hashes_distinct(a, same).verdict is Verdict.passed

    thin = arm_population(
        [_clean_row(f"q{i}", context_hash=None) for i in range(20)],
        label="arm_thin",
    )
    assert context_hashes_distinct(a, thin).verdict is Verdict.cannot_evaluate


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
    ok, _results_a, results_b, _ctx, _knobs = comparison_quotable(a, b)
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
    # `no_sql` is what the stub arm produces: `agent_core._stub` finishes the loop having
    # executed nothing, and since 2026-08-18 that is its own outcome rather than `answered`.
    # The set stays a set because this test's subject is that the arm reached serve at all.
    assert rows[0]["outcome"] in {"answered", "refused", "crashed", "no_sql"}
    assert rows[0]["crashed"] == (rows[0]["outcome"] == "crashed")
    assert "question_id" in rows[0]


# ── what a refused row says, and what an abstention would have answered ───────


def _governed_arm(connector: SqliteConnector, sql_by_qid: dict[str, str]):
    """A scripted arm with a real corpus, so ``check()`` runs instead of raising.

    ``licensed`` is empty on this path — there is no index, so routing licenses nothing — which
    makes every statement naming a table refuse at ``Layer.TABLES``. That is the population the
    two tests below need: a turn that abstained while holding a statement.
    """
    from governed_bi.corpus.analyst import for_analyst
    from governed_bi.corpus.schema import ColumnAsset, TableAsset
    from governed_bi.eval.arms import scripted_arm

    assets = [
        TableAsset(
            id="main.customers", schema="main", physical_name="customers",
            summary="customers", columns=("main.customers.id",),
        ),
        ColumnAsset(
            id="main.customers.id", schema="main", parent_table="customers",
            physical_name="id", summary="id", physical_type="INTEGER",
        ),
    ]
    return scripted_arm(
        gold_sql_by_qid=sql_by_qid,
        connector=connector,
        assets_by_id={a.id: a for a in assets},
        corpus=for_analyst(assets),
    )


def test_a_measured_row_says_which_layer_refused_each_attempt(tmp_path: Path) -> None:
    """``refused`` names *that* governance declined; ``attempts`` names which layer.

    ``CheckVerdict`` has carried ``failed_layer`` and ``reason_code`` all along and they stopped
    at the turn record. Reading the 2026-08-09 run therefore meant replaying every refused
    statement through ``check()`` offline to learn that 18 of 21 were ``r_table_not_licensed`` —
    a *retrieval* failure the analysis had until then attributed to a guardrail false-positive.
    Those two findings ask for opposite work.

    Three questions producing three different verdicts, asserted as the whole list. A trace that
    is empty, or constant, cannot separate them — and "empty" is what the field silently
    degrades to, because every reader of it treats no attempts as a turn that attempted nothing.
    """
    _, connector = _fixture_db(tmp_path)
    questions = [
        {"question_id": "unlicensed", "question": "how many customers", "db_id": "main"},
        {"question_id": "no_table", "question": "the answer", "db_id": "main"},
        {"question_id": "not_a_read", "question": "delete them", "db_id": "main"},
    ]
    rows = run_arm(
        questions,
        _governed_arm(
            connector,
            {
                "unlicensed": "SELECT COUNT(*) AS n FROM customers",
                # Names no table, so the licensing layer has nothing to refuse: this one passes.
                "no_table": "SELECT 999 AS n",
                "not_a_read": "DROP TABLE customers",
            },
        ),
    )
    trace = {str(r["question_id"]): r["attempts"] for r in rows}

    assert trace["unlicensed"] == [
        {"layer": "TABLES", "reason_code": "r_table_not_licensed", "passed": False,
         "path": "agent"}
    ], trace["unlicensed"]
    assert trace["not_a_read"] == [
        {"layer": "NO_WRITE", "reason_code": "r_not_a_read", "passed": False, "path": "agent"}
    ], trace["not_a_read"]
    # The passing attempt, so the field is not just a list of refusals: a turn that answered
    # still says how it got there, and `layer` is null because no layer objected.
    assert trace["no_table"] == [
        {"layer": None, "reason_code": "passed", "passed": True, "path": "agent"}
    ], trace["no_table"]


def test_an_abstained_turn_is_priced_without_being_scored(tmp_path: Path) -> None:
    """``computed_correct`` — what the last statement *would* have answered, never counted.

    A capped or refused turn keeps ``correct=False``: an engine that would not commit to a
    statement gets no credit for it, and that rule stays. But the rule has a price, and until
    this field existed nobody knew what it was — of the 2026-08-09 full run's 133 capped turns,
    23 held the correct answer. Keeping the policy and pricing it are only separable if the number
    is on the row.

    Four rows covering every branch of ``_abstained_fingerprint``, because the field's whole
    content is *when* it is set: a constant ``None`` is indistinguishable from an engine that
    never abstains with a statement in hand, and that is precisely the reading the field exists
    to refuse.
    """
    _, connector = _fixture_db(tmp_path)
    gold = "SELECT COUNT(*) AS n FROM customers"
    columns, gold_rows, _ = connector.execute(gold)
    gold_fingerprint = result_fingerprint(list(columns), [list(r) for r in gold_rows])

    questions = [
        {"question_id": qid, "question": qid, "db_id": "main",
         "gold_sql": gold, "gold_fingerprint": gold_fingerprint}
        for qid in ("refused_right", "refused_wrong", "refused_unrunnable", "answered")
    ]
    rows = run_arm(
        questions,
        _governed_arm(
            connector,
            {
                # Refused for naming an unlicensed table -- and right anyway.
                "refused_right": gold,
                # Same refusal, wrong answer.
                "refused_wrong": "SELECT 999 AS n FROM customers",
                # Refused and would not have run, so there is nothing to price.
                "refused_unrunnable": "DROP TABLE customers",
                # Names no table, so it passes: `grade` already holds this one's verdict.
                "answered": "SELECT 999 AS n",
            },
        ),
    )
    by_qid = {str(r["question_id"]): r for r in rows}

    right = by_qid["refused_right"]
    assert right["outcome"] == "refused", right["outcome"]
    assert right["computed_correct"] is True, (
        "a refused turn holding the right answer is priced at nothing, so the cost of the "
        f"abstention policy cannot be read off the artifact: {right['computed_correct']!r}"
    )
    assert right["correct"] is False, (
        "the price was folded into the score; an engine that refuses now gets credit for it"
    )

    wrong = by_qid["refused_wrong"]
    assert wrong["outcome"] == "refused"
    assert wrong["computed_correct"] is False, (
        "every abstention prices as unknown, which reads the same as none of them being "
        f"pricable: {wrong['computed_correct']!r}"
    )

    # The two genuine absences, so the field is not merely `correct` under another name.
    assert by_qid["refused_unrunnable"]["computed_fingerprint"] is None
    assert by_qid["refused_unrunnable"]["computed_correct"] is None
    assert by_qid["answered"]["outcome"] == "answered"
    assert by_qid["answered"]["computed_correct"] is None, (
        "an answered turn is graded by `grade_turn`; a second verdict beside it invites the "
        "merge the field exists to prevent"
    )


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
    xhigh arm: 5% of answerable-but-wrong turns were exactly this.  [retired]
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


def test_a_numeric_cell_is_compared_as_a_number() -> None:
    """The six pairs that graded ``result_mismatch`` while being the same answer.

    ``_cell``'s fallback was ``return str(value)`` and the type test above it was
    ``isinstance(value, (int, float))``. ``Decimal`` is neither, so **every Postgres
    ``numeric`` cell was compared as a string** — and the artifact recorded
    ``correct=False`` with ``detail="result_mismatch"``, which is indistinguishable from a
    genuinely wrong answer.

    Every EX number this repository produced before the fix is therefore an underestimate,
    and because the size of the underestimate is a function of the schema's numeric-column
    density, the cross-schema comparisons did not hold either.

    All six are accepted by the comparators shipped with the benchmark being graded
    (``pipeline/_db.py``'s ``normalise_result``).
    """
    from decimal import Decimal

    from governed_bi.eval.grade import grade_results

    pairs = [
        (Decimal("0.5"), 0.5),
        (Decimal("100.00"), Decimal("100.0")),
        (Decimal(100), 100),
        (1.0, 1),
        ("abc ", "abc"),  # CHAR padding
        ("ABC", "abc"),
    ]
    for pred, gold in pairs:
        verdict = grade_results(
            pred_columns=["c"], pred_rows=[[pred]], gold_columns=["c"], gold_rows=[[gold]]
        )
        assert verdict["correct"] is True, f"{pred!r} vs {gold!r}: {verdict['detail']}"

    # The paired negative: loosening the cell comparison must not make a wrong number pass.
    assert (
        grade_results(
            pred_columns=["c"],
            pred_rows=[[Decimal("100.01")]],
            gold_columns=["c"],
            gold_rows=[[Decimal("100.00")]],
        )["correct"]
        is False
    )


def test_the_fingerprint_is_the_benchmarks_own_hash() -> None:
    """Byte-identical to ``hash_normalised_result``, not merely equivalent to it.

    This is what makes ``gold_fingerprint`` a usable field: a fingerprint computed by
    BIRD-Obfuscation's ``pipeline/_db.py`` can be put in a question row and compared here
    without re-executing the gold statement. "Aligned with BIRD's own EX" was asserted in a
    docstring for the whole of v2 and was never checked against BIRD's own code; the
    predecessor sorted rows by ``json.dumps`` and wrapped them in ``{"rows": ...}``, so it
    produced a different digest for the same rows and nothing ever noticed.

    ``normalise_result`` is transcribed here rather than imported: the benchmark is a
    separate repository that is not a dependency of this one, and a test that skips when it
    is absent is a test that does not run.
    """
    import hashlib
    import json as _json
    import math as _math
    from decimal import Decimal

    from governed_bi.eval.grade import result_fingerprint

    def normalise_result(rows):  # pipeline/_db.py, verbatim
        if rows is None:
            return []

        def coerce(v):
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return str(v).strip().lower()
            if _math.isnan(f):
                return "\x00nan"
            if _math.isinf(f):
                return "\x00inf" if f > 0 else "\x00-inf"
            return f

        def cell_key(v):
            if v is None:
                return (0, 0.0, "")
            if isinstance(v, float):
                return (1, v, "")
            return (2, 0.0, v)

        normalised = [tuple(coerce(c) for c in row) for row in rows]
        return sorted(normalised, key=lambda row: tuple(cell_key(c) for c in row))

    def hash_normalised_result(rows):
        payload = _json.dumps(
            [list(r) for r in normalise_result(rows)], separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    rowsets = [
        [],
        [[1]],
        [[Decimal("1.50"), "Ada"], [None, "grace "], [2, "ZOE"]],
        [[float("nan")], [float("inf")], [float("-inf")]],
        [["x"], [None], [3.0]],
        [["café"], ["CAFÉ "]],  # ensure_ascii=False and the fold, together
    ]
    for rows in rowsets:
        width = len(rows[0]) if rows else 1
        ours = result_fingerprint([f"c{i}" for i in range(width)], rows)
        assert ours == hash_normalised_result(rows), rows
