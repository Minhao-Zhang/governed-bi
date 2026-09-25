"""EX compares numbers with a tolerance and never equates text with a number.

One case per row of ``docs/open-work.md`` §6.4. The fingerprint stays BIRD's normaliser, so the
last test pins that the verdict and the digest are allowed to disagree.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from governed_bi.eval.grade import grade_results, grade_turn, result_fingerprint


def _correct(pred: object, gold: object) -> bool:
    return bool(
        grade_results(pred_columns=["a"], pred_rows=[[pred]], gold_columns=["a"], gold_rows=[[gold]])[
            "correct"
        ]
    )


@pytest.mark.parametrize(
    ("pred", "gold", "expected"),
    [
        (1 / 3, 0.33333333333333337, True),  # AVG under a different plan
        (0.1 + 0.1 + 0.1, 0.3, True),
        ("00123", 123, False),  # text is not a number
        (True, 1, False),  # a flag is not a count
        ("  5 ", 5, False),
        (Decimal("100.00"), 100.0, True),  # Postgres numeric against a float
        (100.0, 100.001, False),  # a real difference stays one
        (1000000, 1000001, False),  # integers are exact: a count off by one is wrong
        (Decimal("1000000"), Decimal("1000001"), False),
        (Decimal("100.00"), 100, True),  # numeric against an integer, exactly
        (Decimal("NaN"), float("nan"), True),  # one marker for NaN however it arrived
        (1000000, 1000001.0, False),  # a whole-number float is a count, compared exactly
        (1000000.0, 1000001.0, False),
        (1000000, 1000000.0, True),
        (3.0000000000000004, 3, True),  # a fractional float keeps the tolerance
        (2.0**60, 2.0**60 + 256, True),  # above 2**53 adjacent floats are far apart
        (float("nan"), "\x00nan", False),  # no text spells a non-finite value
        (float("nan"), "nan", False),
        (" Paris ", "paris", True),  # text folds as BIRD folds it
        (None, None, True),
        (None, 0, False),
    ],
)
def test_each_case(pred: object, gold: object, expected: bool) -> None:
    assert _correct(pred, gold) is expected


def test_unordered_rows_match_with_tolerance() -> None:
    pred = [["b", 2 / 3], ["a", 1 / 3]]
    gold = [["a", 0.33333333333333337], ["b", 0.6666666666666667]]
    verdict = grade_results(pred_columns=["n", "v"], pred_rows=pred, gold_columns=["n", "v"], gold_rows=gold)
    assert verdict["correct"] is True


def test_order_sensitive_rows_do_not_reorder() -> None:
    verdict = grade_results(
        pred_columns=["n"], pred_rows=[[2], [1]], gold_columns=["n"], gold_rows=[[1], [2]], order_sensitive=True
    )
    assert verdict["correct"] is False


def test_a_turn_with_gold_rows_uses_the_row_comparison_even_beside_a_digest() -> None:
    """The harness passes both when a published digest exists; the rows decide."""
    digest = result_fingerprint(["a"], [[123]])
    verdict = grade_turn(
        outcome="answered",
        pred_columns=["a"],
        pred_rows=[["00123"]],
        gold_columns=["a"],
        gold_rows=[[123]],
        gold_fingerprint=digest,
    )
    assert verdict["correct"] is False
    # BIRD's normaliser still reads both as 123.0, so the recorded digests agree.
    assert verdict["pred_fingerprint"] == verdict["gold_fingerprint"] == digest


def test_a_turn_with_only_a_digest_falls_back_to_it() -> None:
    digest = result_fingerprint(["a"], [[1 / 3]])
    verdict = grade_turn(outcome="answered", pred_columns=["a"], pred_rows=[[1 / 3]], gold_fingerprint=digest)
    assert verdict["correct"] is True


class _Connector:
    """Returns canned rows per statement, and records which statements ran."""

    def __init__(self, results: dict[str, list[list[object]]]) -> None:
        self.results = results
        self.ran: list[str] = []

    def execute(self, sql: str) -> tuple[list[str], list[list[object]], bool]:
        self.ran.append(sql)
        return ["a"], self.results[sql], False


def test_the_harness_executes_gold_beside_a_digest_so_the_tolerance_applies() -> None:
    """The published digest is BIRD's exact-float hash, so a prediction differing in the 17th
    digit mismatches it. Only the executed gold rows let :func:`results_match` decide."""
    from governed_bi.eval.harness import project_turn

    connector = _Connector({"SELECT pred": [[1 / 3]], "SELECT gold": [[0.33333333333333337]]})
    record = {
        "outcome": "answered",
        "terminal_reason": None,
        "execution": {"attempts": []},
        "usage": [],
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "generated_sql": "SELECT pred",
    }
    state = {"answer": {"answer_text": "x", "outcome": "answered", "record": record}, "licensed": [], "schemas": []}
    question = {
        "question_id": "q1",
        "db_id": "s",
        "question": "what share?",
        "gold_sql": "SELECT gold",
        "gold_fingerprint": result_fingerprint(["a"], [[0.33333333333333337]]),
    }
    row = project_turn(state, question=question, arm="t", connector=connector)
    assert connector.ran == ["SELECT pred", "SELECT gold"]
    assert row["correct"] is True
