"""Hermetic unit tests for the production EX grader (``eval/hash_grade.py``).

This grader decides ``correct`` / ``correct_strict`` for every headline EX number,
yet had no direct coverage — a drift in the vendored normalizer would silently
mis-grade and corrupt the moat proof (audit finding Q1). These tests pin the
normalizer output byte-for-byte (fixed SHA-256 digests) and exercise every branch
of ``score_sql_hashes`` against a stub gateway — no DB, no network.
"""

from __future__ import annotations

from governed_bi.eval.hash_grade import (
    GoldHash,
    hash_normalised_result,
    hash_normalised_result_strict,
    normalise_result,
    score_sql_hashes,
)
from governed_bi.gateway import Identity
from governed_bi.gateway.connectors.base import QueryResult

_IDENTITY = Identity(user="test", all_access=True)

# Pinned digests for known inputs — a change here means the vendored normalizer
# drifted from BIRD and every EX number is suspect. Recompute deliberately, never
# to "make the test pass".
_L_AB = "7094883efc9573f0e71e62f1387b6465ae9e350f03dd6ccd619cfa8382e99210"
_S_AB = "a6b89078045e612ffbf9b55bcbded1587a44c731709e5df708fd2f15a02a0e35"


class _StubGateway:
    """Returns a fixed result set for any SQL (no DB)."""

    def __init__(self, rows: list[tuple], columns: tuple[str, ...] = ("x",)) -> None:
        self._rows = rows
        self._columns = list(columns)

    def execute(self, sql: str, identity: Identity) -> QueryResult:  # noqa: ARG002
        return QueryResult(
            columns=self._columns,
            rows=self._rows,
            row_count=len(self._rows),
            truncated=False,
        )


class _RaisingGateway:
    def execute(self, sql: str, identity: Identity) -> QueryResult:  # noqa: ARG002
        raise RuntimeError("boom")


# --- normalizer: fixed digests + invariants -------------------------------- #


def test_hash_is_row_order_independent():
    assert hash_normalised_result([(1, "A"), (2, "b")]) == hash_normalised_result(
        [(2, "b"), (1, "A")]
    )


def test_hash_matches_pinned_digest():
    # Guards against silent normalizer drift (Q1).
    assert hash_normalised_result([(1, "A"), (2, "b")]) == _L_AB
    assert hash_normalised_result_strict([(1, "A"), (2, "b")]) == _S_AB


def test_normalise_lowercases_and_strips_non_numeric():
    # BIRD's lenient normalizer folds case + surrounding whitespace on text cells.
    assert normalise_result([("  Foo  ",), ("foo",)]) == [("foo",), ("foo",)]


def test_different_rows_hash_differently():
    assert hash_normalised_result([(1,)]) != hash_normalised_result([(2,)])


# --- score_sql_hashes: every branch ---------------------------------------- #


def test_score_refusal_is_not_correct():
    grade = score_sql_hashes(None, None, _StubGateway([(1,)]), _IDENTITY)
    assert grade["correct"] is False
    assert grade["correct_strict"] is False
    assert grade["error"] == "refusal"


def test_score_missing_gold_hash():
    grade = score_sql_hashes("SELECT 1", None, _StubGateway([(1,)]), _IDENTITY)
    assert grade["correct"] is False
    assert grade["error"] == "missing_gold_hash"


def test_score_unusable_gold_hash():
    gold = GoldHash(question_id="q", hash_lenient=None, hash_strict=None, error="stale")
    grade = score_sql_hashes("SELECT 1", gold, _StubGateway([(1,)]), _IDENTITY)
    assert grade["correct"] is False
    assert grade["error"].startswith("gold_unusable")


def test_score_matching_hash_is_correct():
    rows = [(1, "A"), (2, "b")]
    gold = GoldHash(question_id="q", hash_lenient=_L_AB, hash_strict=_S_AB)
    grade = score_sql_hashes(
        "SELECT ...", gold, _StubGateway(rows, ("n", "s")), _IDENTITY
    )
    assert grade["correct"] is True
    assert grade["correct_strict"] is True
    assert grade["error"] is None


def test_score_non_matching_hash_is_incorrect():
    gold = GoldHash(question_id="q", hash_lenient=_L_AB, hash_strict=_S_AB)
    grade = score_sql_hashes("SELECT 9", gold, _StubGateway([(9,)]), _IDENTITY)
    assert grade["correct"] is False
    assert grade["correct_strict"] is False
    assert grade["error"] is None


def test_score_execution_error_is_not_correct():
    gold = GoldHash(question_id="q", hash_lenient=_L_AB, hash_strict=_S_AB)
    grade = score_sql_hashes("SELECT boom", gold, _RaisingGateway(), _IDENTITY)
    assert grade["correct"] is False
    assert "boom" in grade["error"]
