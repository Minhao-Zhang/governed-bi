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
