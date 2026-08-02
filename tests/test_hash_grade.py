"""Hermetic unit tests for the production EX grader (``eval/hash_grade.py``).

This grader decides ``correct`` / ``correct_strict`` for every headline EX number,
yet had no direct coverage — a drift in the vendored normalizer would silently
mis-grade and corrupt the moat proof (audit finding Q1). These tests pin the
normalizer output byte-for-byte (fixed SHA-256 digests) and exercise every branch
of ``score_sql_hashes`` against a stub gateway — no DB, no network.
"""

from __future__ import annotations

import pytest

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
    assert grade["error"].startswith("exec_error:")
    assert "boom" in grade["error"]


def test_score_infrastructure_error_uses_infra_prefix():
    """Timeouts / connection deaths must not share ``exec_error:`` with bad SQL.

    ``exec_error:`` is treated as answered-and-wrong; ``infra_error:`` is a crash
    that blocks quotability (audit E4).
    """
    from governed_bi.eval.hash_grade import is_infrastructure_error

    class _Timeout(Exception):
        pass

    class _TimeoutGateway:
        def execute(self, sql, identity):  # noqa: ARG002
            raise _Timeout("canceling statement due to statement timeout")

    class QueryCanceled(Exception):
        pass

    class _CanceledGateway:
        def execute(self, sql, identity):  # noqa: ARG002
            raise QueryCanceled("canceling statement due to statement timeout")

    class OperationalError(Exception):
        pass

    class _ConnClosedGateway:
        def execute(self, sql, identity):  # noqa: ARG002
            raise OperationalError("server closed the connection unexpectedly")

    gold = GoldHash(question_id="q", hash_lenient=_L_AB, hash_strict=_S_AB)
    for gw in (_TimeoutGateway(), _CanceledGateway(), _ConnClosedGateway()):
        grade = score_sql_hashes("SELECT 1", gold, gw, _IDENTITY)
        assert grade["correct"] is False
        assert grade["error"].startswith("infra_error:"), grade["error"]

    # sqlite-style SQL fault wrapped as OperationalError stays a model error.
    class _SqlFaultGateway:
        def execute(self, sql, identity):  # noqa: ARG002
            raise OperationalError("no such column: missing")

    grade = score_sql_hashes("SELECT missing", gold, _SqlFaultGateway(), _IDENTITY)
    assert grade["error"].startswith("exec_error:"), grade["error"]
    assert not is_infrastructure_error(OperationalError("no such column: missing"))
    assert is_infrastructure_error(
        OperationalError("server closed the connection unexpectedly")
    )


def test_score_truncated_result_is_not_hashed_as_complete():
    """A row-cap clip must not be hashed as if it were the full result (audit E4)."""

    class _TruncGateway:
        def execute(self, sql, identity):  # noqa: ARG002
            return QueryResult(
                columns=["n", "s"],
                rows=[(1, "A"), (2, "b")],
                row_count=2,
                truncated=True,
            )

    gold = GoldHash(question_id="q", hash_lenient=_L_AB, hash_strict=_S_AB)
    grade = score_sql_hashes("SELECT ...", gold, _TruncGateway(), _IDENTITY)
    assert grade["correct"] is False
    assert grade["correct_strict"] is False
    assert grade["error"].startswith("infra_error:truncated:")
    assert grade["hash_lenient"] is None
    assert grade["hash_strict"] is None


# --------------------------------------------------------------------------- #
# The grading contract, pinned case by case.
#
# `normalise_result` is the foundation under every number this project produces. A
# subtle edit — deduping rows, coercing NULL to empty, sorting columns — would move
# every EX in every arm and nothing else would fail, because the harness has no
# independent oracle for "is the grader right". Three properties were pinned before
# (row order, case folding, different-rows-differ); the rest were behaviour nobody
# had written down.
#
# Vendored from BIRD-Data-Obfuscation's reference implementation, so these are also
# the assertions that would catch a drift away from the upstream definition.
# --------------------------------------------------------------------------- #

_CONTRACT = [
    # (name, rows_a, rows_b, lenient_match, strict_match)
    ("row order is insensitive", [(1, "a"), (2, "b")], [(2, "b"), (1, "a")], True, True),
    ("duplicate rows are significant", [(1,), (1,)], [(1,)], False, False),
    ("column position is significant", [(1, "a")], [("a", 1)], False, False),
    ("NULL is not the empty string", [(None,)], [("",)], False, False),
    ("NULL is not zero", [(None,)], [(0,)], False, False),
    ("int and float compare equal", [(1,)], [(1.0,)], True, True),
    ("text case is folded (lenient only)", [("A",)], [("a",)], True, False),
    ("surrounding whitespace is stripped", [(" a ",)], [("a",)], True, True),
    ("a numeric string equals its number (lenient only)", [("1",)], [(1,)], True, False),
]


@pytest.mark.parametrize(
    "name,a,b,lenient,strict", _CONTRACT, ids=[c[0] for c in _CONTRACT]
)
def test_the_grading_contract(name, a, b, lenient, strict):
    from governed_bi.eval.hash_grade import (
        hash_normalised_result,
        hash_normalised_result_strict,
    )

    assert (hash_normalised_result(a) == hash_normalised_result(b)) is lenient, (
        f"lenient grading changed for: {name}"
    )
    assert (
        hash_normalised_result_strict(a) == hash_normalised_result_strict(b)
    ) is strict, f"strict grading changed for: {name}"


def test_strict_is_never_more_permissive_than_lenient():
    """`strict` exists to be the tighter of the two. If a pair matches under strict it
    must match under lenient, or `correct_strict` could exceed `correct`."""
    from governed_bi.eval.hash_grade import (
        hash_normalised_result,
        hash_normalised_result_strict,
    )

    for name, a, b, _lenient, _strict in _CONTRACT:
        if hash_normalised_result_strict(a) == hash_normalised_result_strict(b):
            assert hash_normalised_result(a) == hash_normalised_result(b), name


def test_an_empty_result_is_not_a_null_row():
    """`empty_result` and "one row holding NULL" are different answers, and the
    `result_shape` taxonomy leans on telling them apart."""
    from governed_bi.eval.hash_grade import hash_normalised_result

    assert hash_normalised_result([]) != hash_normalised_result([(None,)])


# --------------------------------------------------------------------------- #
# The gold pre-flight must fail closed on gold it cannot execute.
#
# `validate_gold_hashes_live` counted an execution error as "not checked": it lowered
# `n_checked` and never touched `agree_rate`. `_datalake_gold_selfcheck` then computed
# the rate over only the rows that ran, and the caller gated on `agree_rate < 1.0` —
# so one agreeing row across sixty-nine schemas reported 1.0 and the run proceeded to
# spend a model budget grading against gold it had never confirmed.
#
# Found by accident: omitting `gold_sql_field="sql_rename"` makes gold fall back to
# the un-obfuscated `sql_sqlite`, which parses fine and names tables the obfuscated
# Postgres does not have. Eleven of twelve schemas failed to execute and the gate
# passed. A wrong DSN, an unloaded schema and a bad `search_path` all look the same.
# --------------------------------------------------------------------------- #


class _ExplodingGateway:
    """Executes nothing; every gold query raises, as it would against the wrong DSN."""

    def __init__(self, message="relation \"customers\" does not exist"):
        self.message = message

    def execute(self, sql, identity):
        raise RuntimeError(self.message)


class _AgreeingGateway:
    """Returns rows whose lenient hash is whatever the caller pinned as gold."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, identity):
        class _R:
            pass

        r = _R()
        r.rows = self.rows
        return r


def _item(qid, sql="SELECT 1"):
    class _I:
        pass

    i = _I()
    i.question_id = qid
    i.sql = sql
    return i


def test_an_execution_error_is_counted_not_silently_skipped():
    from governed_bi.eval.hash_grade import GoldHash, validate_gold_hashes_live

    items = [_item(f"q{i}") for i in range(4)]
    gold = {
        f"q{i}": GoldHash(question_id=f"q{i}", hash_lenient="x", hash_strict="x")
        for i in range(4)
    }
    res = validate_gold_hashes_live(
        items, gold, _ExplodingGateway(), object(), sample=4
    )

    assert res["n_checked"] == 0
    assert res["n_exec_errors"] == 4, (
        "an exec error must be counted, or a caller cannot tell 'nothing ran' from "
        "'nothing needed to run'"
    )
    assert res["agree_rate"] is None
    assert res["n_no_gold"] == 0 and res["n_unusable_gold"] == 0


def test_missing_and_unusable_gold_are_counted_apart_from_exec_errors():
    """They mean different things: an exec error is our configuration being wrong,
    while missing gold is a property of the dataset no run can fix."""
    from governed_bi.eval.hash_grade import GoldHash, validate_gold_hashes_live

    rows = [(1,)]
    from governed_bi.eval.hash_grade import hash_normalised_result

    h = hash_normalised_result(rows)
    items = [_item("has_gold"), _item("no_gold"), _item("unusable")]
    gold = {
        "has_gold": GoldHash(question_id="has_gold", hash_lenient=h, hash_strict=h),
        # `usable` is derived: a recorded error makes it False regardless of the hash.
        "unusable": GoldHash(
            question_id="unusable", hash_lenient=h, hash_strict=h, error="stale"
        ),
    }
    res = validate_gold_hashes_live(
        items, gold, _AgreeingGateway(rows), object(), sample=3
    )

    assert res["n_checked"] == 1
    assert res["n_matched"] == 1
    assert res["agree_rate"] == 1.0
    assert res["n_exec_errors"] == 0
    assert res["n_no_gold"] == 1
    assert res["n_unusable_gold"] == 1


# --------------------------------------------------------------------------- #
# The pre-flight must notice when the gold SQL we submit is not the statement the
# gold hash was computed from.
#
# `GoldHash.sql_sha256` has always carried that identity and nothing read it. A
# `SELECT *` gold on a decoy-bearing database is a different query from the
# star-expanded statement the hash was taken of, so the answer key graded wrong
# against itself: `correct=False`, `nrows_match=True`, `error=None` — a row that
# reads exactly like a model being wrong. The sampled execution check cannot find
# it (3 questions in 6,743), so the digest sweep is unsampled.
# --------------------------------------------------------------------------- #


def test_a_gold_sql_that_is_not_the_hashed_statement_is_counted(caplog):
    import logging as _logging

    from governed_bi.eval.hash_grade import (
        GoldHash,
        hash_normalised_result,
        sql_sha256,
        validate_gold_hashes_live,
    )

    rows = [(1,)]
    h = hash_normalised_result(rows)
    expanded = 'SELECT "a", "b" FROM t'
    items = [_item("star", sql="SELECT * FROM t"), _item("plain", sql="SELECT 1")]
    gold = {
        # The hash was computed from the EXPANDED statement, not from what we submit.
        "star": GoldHash(
            question_id="star",
            hash_lenient=h,
            hash_strict=h,
            sql_sha256=sql_sha256(expanded),
        ),
        "plain": GoldHash(
            question_id="plain",
            hash_lenient=h,
            hash_strict=h,
            sql_sha256=sql_sha256("SELECT 1"),
        ),
    }
    with caplog.at_level(_logging.WARNING, logger="governed_bi.eval"):
        res = validate_gold_hashes_live(
            items, gold, _AgreeingGateway(rows), object(), sample=2
        )

    assert res["n_gold_sql_mismatch"] == 1
    assert res["gold_sql_mismatch_ids"] == ["star"]
    assert "sql_sha256 mismatch" in caplog.text
    # And it is NOT folded into agree_rate: the sampled rows still agree here, which
    # is exactly why the mismatch needs its own counter.
    assert res["agree_rate"] == 1.0


def test_the_digest_sweep_is_not_limited_by_sample():
    """`sample` bounds executions. The digest costs nothing, so it sweeps everything —
    otherwise a 5-item sample over thousands of questions never draws the offender."""
    from governed_bi.eval.hash_grade import (
        GoldHash,
        hash_normalised_result,
        sql_sha256,
        validate_gold_hashes_live,
    )

    rows = [(1,)]
    h = hash_normalised_result(rows)
    items = [_item(f"q{i}", sql=f"SELECT {i}") for i in range(20)]
    gold = {
        f"q{i}": GoldHash(
            question_id=f"q{i}",
            hash_lenient=h,
            hash_strict=h,
            sql_sha256=sql_sha256("SOMETHING ELSE"),
        )
        for i in range(20)
    }
    res = validate_gold_hashes_live(
        items, gold, _AgreeingGateway(rows), object(), sample=2
    )

    assert res["n_checked"] == 2
    assert res["n_gold_sql_mismatch"] == 20
    assert len(res["gold_sql_mismatch_ids"]) == 5  # capped for the log line only


def test_gold_without_a_recorded_sql_sha256_is_not_reported_as_a_mismatch():
    """An older artifact omits the field; absence of evidence is not a mismatch."""
    from governed_bi.eval.hash_grade import (
        GoldHash,
        hash_normalised_result,
        validate_gold_hashes_live,
    )

    rows = [(1,)]
    h = hash_normalised_result(rows)
    items = [_item("q0", sql="SELECT 1")]
    gold = {"q0": GoldHash(question_id="q0", hash_lenient=h, hash_strict=h)}
    res = validate_gold_hashes_live(
        items, gold, _AgreeingGateway(rows), object(), sample=1
    )
    assert res["n_gold_sql_mismatch"] == 0


def test_the_selfcheck_aggregate_reports_which_schemas_could_not_execute(monkeypatch):
    """`_datalake_gold_selfcheck` is what the run gates on. It has to surface exec
    failures per schema, not fold them into a rate computed over the survivors."""
    from governed_bi.eval import run_datalake as rd

    def _fake_validate(items, gold_hashes, gw, identity, *, sample):
        # `address` runs and agrees; every other schema fails to execute.
        db = getattr(gw, "_test_db", None)
        if db == "address":
            return {
                "n_checked": 1, "n_matched": 1, "agree_rate": 1.0,
                "n_exec_errors": 0, "n_no_gold": 0, "n_unusable_gold": 0, "errors": [],
            }
        return {
            "n_checked": 0, "n_matched": 0, "agree_rate": None,
            "n_exec_errors": 1, "n_no_gold": 0, "n_unusable_gold": 0,
            "errors": ['q: exec relation "customers" does not exist'],
        }

    class _Conn:
        def __init__(self, dsn, schema=None):
            self.schema = schema

        def close(self):
            pass

    class _GW:
        def __init__(self, conn, **kw):
            self._test_db = conn.schema

    monkeypatch.setattr(rd, "validate_gold_hashes_live", _fake_validate)
    monkeypatch.setattr(rd, "PostgresConnector", _Conn)
    monkeypatch.setattr(rd, "Gateway", _GW)

    pairs = [(_item(f"q_{db}"), db) for db in ("address", "airline", "beer_factory")]
    res = rd._datalake_gold_selfcheck(pairs, {}, "dsn", object())

    assert res["n_checked"] == 1
    assert res["agree_rate"] == 1.0, "the surviving row genuinely agreed"
    # ...and that is exactly why the rate alone must not be the gate.
    assert res["n_exec_errors"] == 2
    assert set(res["exec_error_dbs"]) == {"airline", "beer_factory"}
    assert res["n_dbs"] == 3


def test_a_schema_with_no_usable_gold_is_reported_but_not_an_exec_error(monkeypatch):
    """Nothing to execute is not a failure to execute. Those questions are
    ungradeable for every arm equally, so it is worth reporting and wrong to abort."""
    from governed_bi.eval import run_datalake as rd

    def _fake_validate(items, gold_hashes, gw, identity, *, sample):
        return {
            "n_checked": 0, "n_matched": 0, "agree_rate": None,
            "n_exec_errors": 0, "n_no_gold": 1, "n_unusable_gold": 0, "errors": [],
        }

    class _Conn:
        def __init__(self, dsn, schema=None):
            self.schema = schema

        def close(self):
            pass

    monkeypatch.setattr(rd, "validate_gold_hashes_live", _fake_validate)
    monkeypatch.setattr(rd, "PostgresConnector", _Conn)
    monkeypatch.setattr(rd, "Gateway", lambda conn, **kw: object())

    res = rd._datalake_gold_selfcheck([(_item("q"), "odd_db")], {}, "dsn", object())
    assert res["n_exec_errors"] == 0
    assert res["dbs_without_usable_gold"] == ["odd_db"]


# --------------------------------------------------------------------------- #
# The gate is proportional, and more sampling buys redundancy.
#
# `validate_gold_hashes_live` catches `Exception` broadly and cannot tell a
# misconfiguration from a query that crossed the 60 s gateway timeout or a gold row
# BIRD never flagged as broken. Aborting on any single exec error would let one slow
# query make the whole split unrunnable, deterministically, with no way past it —
# worse than the fail-open it replaced. Misconfiguration takes out essentially every
# schema; an unlucky query takes out one.
# --------------------------------------------------------------------------- #


def _selfcheck_with(monkeypatch, per_db_results):
    """Drive `_datalake_gold_selfcheck` with a scripted per-db validator result."""
    from governed_bi.eval import run_datalake as rd

    class _Conn:
        def __init__(self, dsn, schema=None):
            self.schema = schema

        def close(self):
            pass

    class _GW:
        def __init__(self, conn, **kw):
            self.db = conn.schema

    monkeypatch.setattr(rd, "PostgresConnector", _Conn)
    monkeypatch.setattr(rd, "Gateway", _GW)
    monkeypatch.setattr(
        rd,
        "validate_gold_hashes_live",
        lambda items, gold, gw, ident, *, sample: per_db_results[gw.db],
    )
    pairs = [(_item(f"q_{db}"), db) for db in per_db_results]
    return rd._datalake_gold_selfcheck(pairs, {}, "dsn", object())


def _ok(n=1):
    return {"n_checked": n, "n_matched": n, "agree_rate": 1.0, "n_exec_errors": 0,
            "n_no_gold": 0, "n_unusable_gold": 0, "errors": []}


def _all_failed(n=1):
    return {"n_checked": 0, "n_matched": 0, "agree_rate": None, "n_exec_errors": n,
            "n_no_gold": 0, "n_unusable_gold": 0, "errors": ["q: exec timeout"]}


def _partly_failed():
    """One sampled row failed to execute; another executed and agreed."""
    return {"n_checked": 1, "n_matched": 1, "agree_rate": 1.0, "n_exec_errors": 1,
            "n_no_gold": 0, "n_unusable_gold": 0, "errors": ["q: exec timeout"]}


def test_a_schema_that_verified_on_a_second_row_is_not_counted_as_failed(monkeypatch):
    """This is what makes `--gold-per-db` redundancy rather than more ways to abort:
    the grader demonstrably works on that schema."""
    res = _selfcheck_with(monkeypatch, {"a": _partly_failed(), "b": _ok()})

    assert res["n_exec_errors"] == 0, "a verified schema must not contribute to the fatal count"
    assert res["exec_error_dbs"] == {}
    assert set(res["partial_exec_error_dbs"]) == {"a"}, "but it is still reported"
    assert res["n_dbs_verified"] == 2


def test_a_schema_with_nothing_verified_is_counted_as_failed(monkeypatch):
    res = _selfcheck_with(monkeypatch, {"a": _all_failed(), "b": _ok()})

    assert res["n_exec_errors"] == 1
    assert set(res["exec_error_dbs"]) == {"a"}
    assert res["partial_exec_error_dbs"] == {}
    assert res["n_dbs_verified"] == 1


def test_the_gate_aborts_when_most_schemas_could_not_run_their_gold(monkeypatch):
    """The case that was silent: eleven of twelve schemas failing while the one
    survivor reported agree_rate 1.0.

    Calls the gate. The earlier version recomputed `share > THRESHOLD` itself and
    never touched `_assert_gold_is_trustworthy`, so deleting the gate outright left
    this test green (AUDIT T2).
    """
    import pytest as _pytest

    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    results = {f"db{i}": _all_failed() for i in range(11)}
    results["ok"] = _ok()
    res = _selfcheck_with(monkeypatch, results)

    assert res["n_checked"] == 1
    assert res["agree_rate"] == 1.0, "the survivor genuinely agreed"
    with _pytest.raises(RuntimeError, match="configuration fault"):
        _assert_gold_is_trustworthy(res, n_schemas=12)


def test_the_gate_does_not_abort_for_one_awkward_schema_in_many(monkeypatch):
    """One slow query out of sixty-nine must not make the split unrunnable."""
    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    results = {f"db{i}": _ok() for i in range(68)}
    results["slow"] = _all_failed()
    res = _selfcheck_with(monkeypatch, results)

    assert len(res["exec_error_dbs"]) == 1
    # The gate itself must let this through, not merely the arithmetic behind it.
    _assert_gold_is_trustworthy(res, n_schemas=69)
    assert res["n_dbs_verified"] == 68


def test_the_abort_threshold_sits_between_the_two_cases(monkeypatch):
    """Both failure modes, driven through the gate rather than through arithmetic:
    too low and one bad query aborts the split, too high and a misconfiguration passes."""
    import pytest as _pytest

    from governed_bi.eval.run_datalake import (
        _GOLD_EXEC_FAILURE_ABORT_FRACTION,
        _assert_gold_is_trustworthy,
    )

    assert 1 / 69 < _GOLD_EXEC_FAILURE_ABORT_FRACTION < 11 / 12

    one_bad = {f"db{i}": _ok() for i in range(68)}
    one_bad["slow"] = _all_failed()
    _assert_gold_is_trustworthy(_selfcheck_with(monkeypatch, one_bad), n_schemas=69)

    mostly_bad = {f"db{i}": _all_failed() for i in range(11)}
    mostly_bad["ok"] = _ok()
    with _pytest.raises(RuntimeError):
        _assert_gold_is_trustworthy(_selfcheck_with(monkeypatch, mostly_bad), n_schemas=12)


def test_the_gold_preflight_runs_before_the_build_phase_spends_on_a_model():
    """The gate used to sit after the builds, so a wrong DSN or the wrong gold field
    aborted a run that had already paid for a curator pass and an SME round on every
    schema. The pre-flight needs only Postgres and the split files.

    This is a source-order check, which is weaker than driving the harness — but the
    alternative needs live Postgres, a model and an hour, and the ordering is exactly
    the kind of thing a later edit reshuffles without noticing. Same technique as
    `test_datalake_row_discipline.py`'s row-builder checks, and labelled as such.
    """
    import inspect

    from governed_bi.eval.run_datalake import run_datalake

    src = inspect.getsource(run_datalake)
    # Located by occurrence, not by matching the call's argument text: the argument list
    # is formatted across lines and a substring match on it breaks on reflow, which is
    # noise rather than a regression.
    calls = [
        i
        for i in range(len(src))
        if src.startswith("_assert_gold_is_trustworthy(", i)
    ]
    assert len(calls) == 2, f"expected two gate calls, found {len(calls)}"
    build = src.index("built = run_build_phase(")

    assert calls[0] < build, (
        "the gold pre-flight must run before the build phase, or a configuration "
        "fault costs a full curator pass over every schema before it is caught"
    )
    assert build < calls[1], (
        "the post-build re-check runs over the scored pool, which only exists after "
        "the builds"
    )


def test_both_gold_checks_go_through_the_same_gate():
    """Two call sites deciding independently what counts as trustworthy is how they
    drift. Both must call `_assert_gold_is_trustworthy`."""
    import inspect

    from governed_bi.eval.run_datalake import run_datalake

    src = inspect.getsource(run_datalake)
    assert src.count("_assert_gold_is_trustworthy(") == 2, (
        "expected exactly two gate calls (pre-flight and post-build)"
    )
    # And no call site should re-implement the thresholds inline.
    assert "_GOLD_EXEC_FAILURE_ABORT_FRACTION" not in src, (
        "the abort threshold is the gate helper's business, not the driver's"
    )


# --------------------------------------------------------------------------- #
# The denominator has to be the schemas the run asked for, not the ones sampled.
#
# The two gate calls sample different sets: the pre-flight covers every requested
# schema, the post-build one only those that built. Deriving the fraction from each
# meant the same fixed set of gold failures became a larger share after the build, so
# a configuration the pre-flight had correctly called "a few awkward queries" could
# cross the threshold and abort — because *unrelated* schemas failed to build. That is
# exactly the abort-after-paying the pre-flight was hoisted to avoid.
# --------------------------------------------------------------------------- #


def _check(n_dbs, failed, agree=1.0, checked=1):
    return {
        "n_checked": checked, "agree_rate": agree, "n_dbs": n_dbs,
        "failed_dbs": [], "n_exec_errors": len(failed),
        "exec_error_dbs": {db: "q: exec timeout" for db in failed},
        "partial_exec_error_dbs": {}, "dbs_without_usable_gold": [],
    }


def test_build_failures_elsewhere_cannot_push_gold_failures_over_the_threshold():
    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    # 8 schemas requested, 2 with unrunnable gold = 25%, which warns.
    _assert_gold_is_trustworthy(_check(8, ["bad_a", "bad_b"]), n_schemas=8)

    # Post-build: 4 unrelated schemas failed to build (curator crash, timeout), so only
    # 4 are sampled — the same 2 bad ones plus 2 good. Against the sampled pool that is
    # 50% and would abort; against what the run asked for it is still 25%.
    post = _check(4, ["bad_a", "bad_b"])
    _assert_gold_is_trustworthy(post, n_schemas=8)  # must not raise

    with pytest.raises(RuntimeError, match="failed to execute"):
        _assert_gold_is_trustworthy(post)  # deriving it from the sample does abort


def test_a_genuinely_systematic_failure_still_aborts_with_the_stable_denominator():
    """The stable denominator must not become a way to never abort."""
    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    with pytest.raises(RuntimeError, match="configuration fault"):
        _assert_gold_is_trustworthy(
            _check(12, [f"db{i}" for i in range(11)]), n_schemas=12
        )


def test_one_awkward_schema_does_not_abort_a_small_smoke_run():
    """`--limit-dbs 3` is the runbook's own smoke command. A share alone made a single
    slow gold row abort it — and abort claiming "this is a configuration fault", which
    one failure out of three is no evidence of."""
    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    for n in (2, 3, 4, 5, 8, 69):
        _assert_gold_is_trustworthy(_check(n, ["slow"]), n_schemas=n)


def test_two_failures_in_a_tiny_pool_still_abort():
    """The minimum count must not swallow the case where nearly everything failed."""
    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    with pytest.raises(RuntimeError, match="configuration fault"):
        _assert_gold_is_trustworthy(_check(3, ["a", "b"]), n_schemas=3)


def test_total_failure_is_caught_before_the_share_rule_ever_applies():
    """One schema, its only gold row unrunnable: `n_checked == 0` aborts first, with a
    message about verifying nothing rather than about a configuration share."""
    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    with pytest.raises(RuntimeError, match="verified 0 rows"):
        _assert_gold_is_trustworthy(_check(1, ["only"], checked=0), n_schemas=1)


def test_a_mismatching_schema_is_not_counted_as_verified(monkeypatch):
    """`n_dbs_verified` subtracts hash mismatches too. Unreachable through the gate —
    any mismatch drags the aggregate `agree_rate` below 1.0 and aborts — but this
    function is importable, and a field that only tells the truth when the caller gates
    correctly is the defect shape this module keeps producing."""
    res = _selfcheck_with(
        monkeypatch,
        {
            "agrees": _ok(),
            "mismatches": {
                "n_checked": 1, "n_matched": 0, "agree_rate": 0.0,
                "n_exec_errors": 0, "n_no_gold": 0, "n_unusable_gold": 0,
                "errors": ["q: hash mismatch"],
            },
        },
    )
    assert res["failed_dbs"] == ["mismatches"]
    assert res["n_dbs_verified"] == 1, "the mismatching schema is not verified"


def test_a_mismatch_anywhere_aborts_so_the_verified_count_is_never_read_stale():
    """Pins the invariant the paragraph above leans on."""
    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    with pytest.raises(RuntimeError, match="disagreed with live gold"):
        _assert_gold_is_trustworthy(_check(69, [], agree=0.98), n_schemas=69)


# --------------------------------------------------------------------------- #
# Dilution: the other direction of the denominator problem.
#
# The gold share is measured against `len(wanted)`, which is right for its own
# question — is this a systematic misconfiguration across what we asked for — but it
# means a gold problem confined to a small surviving pool reads as a small share. The
# answer is not to teach the gold check to detect build failures; it is to refuse to
# serve a pool that lost most of its members, which is a distinct failure mode with
# its own gate.
# --------------------------------------------------------------------------- #


def test_the_extreme_dilution_case_is_unreachable_and_caught_earlier(monkeypatch):
    """"Every sampled schema's gold failed" cannot coexist with `n_checked > 0`: a
    schema only enters `exec_error_dbs` when its own `n_checked` is zero, so all-failed
    sums to zero and aborts on "verified 0 rows" before any share is consulted.

    Pinned because it is the reason the share rule does not need to handle that case,
    and a later edit to the branch structure could quietly break the implication."""
    res = _selfcheck_with(monkeypatch, {d: _all_failed() for d in ("a", "b", "c")})

    assert res["n_checked"] == 0
    assert sorted(res["exec_error_dbs"]) == ["a", "b", "c"]

    from governed_bi.eval.run_datalake import _assert_gold_is_trustworthy

    with pytest.raises(RuntimeError, match="verified 0 rows"):
        _assert_gold_is_trustworthy(res, n_schemas=20)


def test_a_pool_that_mostly_failed_to_build_is_refused_before_serving():
    """The reachable dilution case — 20 requested, 5 built, 4 of those gold-broken — is
    a build-attrition failure wearing a gold-gate costume. The coverage gate stops it,
    so the diluted share never gets the chance to matter.

    This test previously asserted only arithmetic about the threshold constant
    (`5/20 < FLOOR`) and never called the gate: it passed with the gate deleted, with
    the comparison flipped, and with the denominator reversed. That is the failure mode
    this file spends most of its length guarding other code against.
    """
    from governed_bi.eval.run_datalake import _assert_build_coverage

    with pytest.raises(RuntimeError, match="only 5 of 20"):
        _assert_build_coverage(
            built=[f"db{i}" for i in range(5)],
            wanted=[f"db{i}" for i in range(20)],
            build_errors={f"db{i}": "RuntimeError: curator crash" for i in range(5, 20)},
        )


def test_a_run_that_lost_a_handful_of_schemas_is_still_served():
    """A scale run must not be thrown away for a few awkward schemas — they are named in
    `build_errors` and block quoting, which is the proportionate response."""
    from governed_bi.eval.run_datalake import _assert_build_coverage

    _assert_build_coverage(
        built=[f"db{i}" for i in range(66)],
        wanted=[f"db{i}" for i in range(69)],
        build_errors={f"db{i}": "boom" for i in range(66, 69)},
    )


def test_every_build_failing_says_so_rather_than_quoting_a_percentage():
    from governed_bi.eval.run_datalake import _assert_build_coverage

    with pytest.raises(RuntimeError, match="every db failed to build"):
        _assert_build_coverage(built=[], wanted=["a", "b"], build_errors={"a": "x", "b": "y"})


def test_the_coverage_gate_names_the_failures_and_counts_the_rest():
    """An operator reading this needs the scale first and the names second, and needs to
    know the list is truncated."""
    from governed_bi.eval.run_datalake import _assert_build_coverage

    with pytest.raises(RuntimeError) as err:
        _assert_build_coverage(
            built=["ok"],
            wanted=[f"db{i}" for i in range(10)],
            build_errors={f"db{i}": f"boom {i}" for i in range(9)},
        )
    msg = str(err.value)
    assert "only 1 of 10" in msg
    assert "10%" in msg
    assert "db0: boom 0" in msg
    assert "+6 more" in msg, "the truncated list must say how much it hid"
    assert "--limit-dbs" in msg, "the message must say what to do next"


def test_a_fully_built_resume_is_not_refused():
    """The highest-risk regression this gate could cause: `_build_db_corpora` returns
    early on a resume without raising, so every already-built schema still lands in
    `built`. If it did not, the gate would make every resume of a large run impossible."""
    from governed_bi.eval.run_datalake import _assert_build_coverage

    dbs = [f"db{i}" for i in range(20)]
    _assert_build_coverage(built=dbs, wanted=dbs, build_errors={})


def test_build_coverage_and_gold_share_are_separate_thresholds():
    """Pinned so a later tweak cannot collapse them into one number. They answer
    different questions and one must not be tuned as a proxy for the other."""
    from governed_bi.eval.run_datalake import (
        _BUILD_COVERAGE_ABORT_FRACTION,
        _GOLD_EXEC_FAILURE_ABORT_FRACTION,
    )

    assert _BUILD_COVERAGE_ABORT_FRACTION != _GOLD_EXEC_FAILURE_ABORT_FRACTION
    assert 0 < _GOLD_EXEC_FAILURE_ABORT_FRACTION < _BUILD_COVERAGE_ABORT_FRACTION < 1


# --------------------------------------------------------------------------- #
# Free-pass counters (Audit E2)
# --------------------------------------------------------------------------- #


def test_free_pass_counts_correct_with_empty_gold():
    from governed_bi.eval.hash_grade import free_pass_counts

    rows = [
        {
            "question_id": "q1",
            "correct": True,
            "gold_nrows": 0,
            "generated_sql": "SELECT 1 WHERE false",
            "tables_used": [],
        },
        {
            "question_id": "q2",
            "correct": True,
            "gold_nrows": 3,
            "generated_sql": "SELECT a FROM t",
            "tables_used": ["tbl_t"],
        },
        {
            "question_id": "q3",
            "correct": False,
            "gold_nrows": 0,
            "generated_sql": "SELECT 1 WHERE false",
            "tables_used": [],
        },
    ]
    counts = free_pass_counts(rows)
    assert counts["n_correct_with_empty_gold"] == 1
    assert counts["n_correct_and_pred_has_no_from"] == 1


def test_summarise_rows_increments_empty_gold_free_pass():
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        {
            "question_id": "q1",
            "db_id": "d",
            "arm": "curated",
            "split": "test",
            "correct": True,
            "gold_nrows": 0,
            "generated_sql": "SELECT 1 WHERE false",
            "tables_used": [],
        },
        {
            "question_id": "q2",
            "db_id": "d",
            "arm": "curated",
            "split": "test",
            "correct": True,
            "gold_nrows": 2,
            "generated_sql": "SELECT a FROM t",
            "tables_used": ["tbl_t"],
        },
    ]
    s = _summarise_rows("curated", rows)
    assert s["n_correct_with_empty_gold"] == 1
    assert s["n_correct_and_pred_has_no_from"] == 1
    assert s["n_correct_and_zero_table_overlap"] == 0


# --------------------------------------------------------------------------- #
# AUDIT T1: `load_gold_hashes` loads the ground truth behind every published
# number, and every gold test monkeypatched around it.
# --------------------------------------------------------------------------- #


def _write_gold_lines(path, rows):
    import json as _json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(_json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_load_gold_hashes_filters_by_db_and_dsn_key_but_not_split(tmp_path):
    """`db_id` and `dsn_key` gate; the row's own `split` label does NOT.

    That label is stale in the shipped artifact: the split was re-drawn after the
    hashes were computed and the label was not regenerated, so 79% of rows marked
    `split=test` are questions that now live in `train_final.jsonl`. Filtering on it
    left 286 of 1,389 test questions gradeable while every one of them has a hash, and
    the failure was a silently smaller n rather than a wrong number. Safe to ignore
    because the two splits are disjoint on `(db_id, question_id)` and no pair carries
    two gold rows, both checked against the artifact.
    """
    from governed_bi.eval.hash_grade import load_gold_hashes

    rows = [
        {"question_id": "1", "db_id": "beer", "split": "test", "dsn_key": "rename_decoy",
         "hash_lenient": "aa", "hash_strict": "bb", "nrows": 2},
        {"question_id": "2", "db_id": "other", "split": "test", "dsn_key": "rename_decoy",
         "hash_lenient": "cc", "hash_strict": "dd"},
        # Mislabelled `train` for a question the current split calls test. This is the
        # 79% case, and it must still be gradeable.
        {"question_id": "3", "db_id": "beer", "split": "train", "dsn_key": "rename_decoy",
         "hash_lenient": "ee", "hash_strict": "ff"},
        {"question_id": "4", "db_id": "beer", "split": "test", "dsn_key": "other_dsn",
         "hash_lenient": "gg", "hash_strict": "hh"},
    ]
    _write_gold_lines(tmp_path / "eval_dataset" / "gold_result_hashes_rename_decoy.jsonl", rows)

    out = load_gold_hashes(tmp_path, db_id="beer")
    assert set(out) == {"1", "3"}, "the stale split label must not gate, but db/dsn must"
    assert out["1"].hash_lenient == "aa"
    assert out["1"].nrows == 2
    assert out["3"].hash_lenient == "ee"


def test_conflicting_gold_for_one_question_raises_rather_than_picking(tmp_path):
    """The one case where ignoring the split label could mis-grade.

    Two different golds for one `(db_id, question_id)` means the label was the only
    thing that could tell them apart, and it is not trustworthy here. Silently keeping
    the last row read would grade against an arbitrary one of the two.
    """
    import pytest as _pytest

    from governed_bi.eval.hash_grade import load_gold_hashes

    _write_gold_lines(
        tmp_path / "eval_dataset" / "gold_result_hashes_rename_decoy.jsonl",
        [
            {"question_id": "1", "db_id": "beer", "split": "test", "hash_lenient": "aa"},
            {"question_id": "1", "db_id": "beer", "split": "train", "hash_lenient": "zz"},
        ],
    )
    with _pytest.raises(ValueError, match="conflicting gold hashes"):
        load_gold_hashes(tmp_path, db_id="beer")


def test_an_identical_gold_row_twice_is_not_a_conflict(tmp_path):
    """Dedup, not paranoia: the same question hashed twice under both split labels is
    the shape the stale artifact actually has, and it agrees with itself."""
    from governed_bi.eval.hash_grade import load_gold_hashes

    _write_gold_lines(
        tmp_path / "eval_dataset" / "gold_result_hashes_rename_decoy.jsonl",
        [
            {"question_id": "1", "db_id": "beer", "split": "test",
             "hash_lenient": "aa", "sql_sha256": "s1"},
            {"question_id": "1", "db_id": "beer", "split": "train",
             "hash_lenient": "aa", "sql_sha256": "s1"},
        ],
    )
    assert set(load_gold_hashes(tmp_path, db_id="beer")) == {"1"}


def test_load_gold_hashes_accepts_the_artifacts_layout(tmp_path):
    from governed_bi.eval.hash_grade import load_gold_hashes

    _write_gold_lines(
        tmp_path / "artifacts" / "gold_result_hashes_rename_decoy.jsonl",
        [{"question_id": "1", "db_id": "beer", "hash_lenient": "aa", "hash_strict": "bb"}],
    )
    assert set(load_gold_hashes(tmp_path, db_id="beer")) == {"1"}


def test_load_gold_hashes_raises_when_the_file_is_absent(tmp_path):
    import pytest as _pytest

    from governed_bi.eval.hash_grade import load_gold_hashes

    with _pytest.raises(FileNotFoundError, match="gold hash file not found"):
        load_gold_hashes(tmp_path, db_id="beer")


def test_load_gold_hashes_skips_blank_lines(tmp_path):
    from governed_bi.eval.hash_grade import load_gold_hashes

    path = tmp_path / "eval_dataset" / "gold_result_hashes_rename_decoy.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"question_id":"1","db_id":"beer","hash_lenient":"aa","hash_strict":"bb"}\n\n\n',
        encoding="utf-8",
    )
    assert set(load_gold_hashes(tmp_path, db_id="beer")) == {"1"}
