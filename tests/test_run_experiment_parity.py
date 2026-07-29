"""Driver parity: the pinned single-db harness must record what the pooled one does.

Both drivers grade through the same ``score_sql_hashes`` call and both aggregate
their own rows, so any field one keeps and the other drops is a measurement that
one driver's runs simply do not have — and the two then disagree about what a run
records. These tests pin the overlap (result shape per row, cost/attempt
aggregates) and the None-vs-zero discipline the aggregates depend on.

Driven with a scripted solver and an in-memory echo gateway: no model, no graph,
no Postgres.
"""

from __future__ import annotations

from dataclasses import asdict

from governed_bi.eval.dataset import EvalItem
from governed_bi.eval.hash_grade import GoldHash, hash_normalised_result
from governed_bi.eval.run_experiment import _cost_block, _run_arm_generations
from governed_bi.gateway import Identity
from governed_bi.gateway.connectors.base import QueryResult

IDENTITY = Identity(user="eval", all_access=True)


class _EchoGateway:
    """A query's result set is a pure function of its SQL, so grading is fixed."""

    def execute(self, sql: str, identity: Identity) -> QueryResult:
        return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)


class _ScriptedSolver:
    def __init__(self, by_question: dict[str, str | None]) -> None:
        self._by_question = by_question

    def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
        return self._by_question[question], {
            "attempts": 2,
            "usage": {"total_tokens": 7},
            "cost_est_usd": 0.0,
        }

    def solve(self, question: str) -> str | None:
        return self.solve_with_meta(question)[0]


def _arm(items, gold, solver):
    return _run_arm_generations(
        arm="curated",
        solver=solver,
        items=items,
        gold_hashes=gold,
        gateway=_EchoGateway(),
        identity=IDENTITY,
        bird_dir=None,
        suspect_columns=frozenset(),
        dialect="postgres",
    )


def _fixture():
    """One correct row, one wrong-hash-but-right-rowcount row, one refusal."""
    hit_sql = "SELECT 1 AS n"
    shape_sql = "SELECT 2 AS n"
    items = [
        EvalItem(question="q hit", sql=hit_sql, question_id="q0", difficulty="simple"),
        EvalItem(
            question="q shape", sql=shape_sql, question_id="q1", difficulty="simple"
        ),
        EvalItem(
            question="q refuse", sql=hit_sql, question_id="q2", difficulty="simple"
        ),
    ]
    gold = {
        "q0": GoldHash(
            "q0",
            hash_lenient=hash_normalised_result([(hit_sql,)]),
            hash_strict=None,
            nrows=1,
        ),
        "q1": GoldHash("q1", hash_lenient="wrong", hash_strict=None, nrows=1),
        "q2": GoldHash("q2", hash_lenient="wrong", hash_strict=None, nrows=1),
    }
    solver = _ScriptedSolver({"q hit": hit_sql, "q shape": shape_sql, "q refuse": None})
    return items, gold, solver


def test_rows_carry_result_shape_fields():
    rows, _summary, _extra = _arm(*_fixture())
    hit, shape, refused = rows

    assert (hit["pred_nrows"], hit["pred_ncols"], hit["gold_nrows"]) == (1, 1, 1)
    assert hit["nrows_match"] is True
    assert hit["correct"] is True

    # Right row count, wrong hash — the projection/ordering class, which is only
    # visible if these fields reach the row.
    assert shape["correct"] is False
    assert shape["nrows_match"] is True

    # A refusal executed nothing. Zeros here would claim an observed empty result;
    # score_sql_hashes omits the shape keys on that branch, so they must stay None.
    assert refused["generated_sql"] is None
    for key in ("pred_nrows", "pred_ncols", "gold_nrows", "nrows_match"):
        assert refused[key] is None, key


def test_arm_summary_reports_shape_and_attempt_aggregates():
    _rows, summary, _extra = _arm(*_fixture())
    assert summary.n_wrong_but_nrows_match == 1  # the refusal is not counted here
    assert summary.mean_attempts == 2.0


def test_cost_block_matches_the_pooled_driver_shape():
    """Both drivers must publish the same cost keys, or a reader breaks on one."""
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [{"latency_sec": 1.5, "cost_est_usd": 0.25, "usage": {"total_tokens": 3}}]
    assert set(_cost_block(rows)) == set(_summarise_rows("curated", rows)["cost"])

    pooled = _summarise_rows("curated", rows)
    ours = asdict(_arm(*_fixture())[1])
    for key in (
        "mean_attempts",
        "n_wrong_but_nrows_match",
        "n_missing_gold",
        "n_correct_with_empty_gold",
        "n_correct_and_pred_has_no_from",
        "n_correct_and_zero_table_overlap",
    ):
        assert key in pooled and key in ours, key


def test_cost_block_totals():
    rows = [
        {"latency_sec": 1.0, "cost_est_usd": 0.25, "usage": {"total_tokens": 3}},
        {"latency_sec": 2.0, "cost_est_usd": 0.75, "usage": {"total_tokens": 4}},
    ]
    assert _cost_block(rows) == {
        "total_latency_sec": 3.0,
        "mean_latency_sec": 1.5,
        "total_cost_est_usd": 1.0,
        # How many rows the total covers. `ladder_deltas` divides by this total, and a
        # crashed turn burns model calls while recording no cost — so a partial total
        # would understate the price per answer by exactly the unpriced share.
        "n_rows_priced": 2,
        "total_tokens": 7,
    }


def test_cost_block_keeps_no_data_distinct_from_zero():
    """A missing input and a genuine zero are different facts.

    Reporting the first as 0.0 (or the second as None) is how an instrumentation gap
    turns into a confident number nobody questions.
    """
    unmeasured = _cost_block([{"correct": True}, {"correct": False}])
    assert unmeasured == {
        "total_latency_sec": None,
        "mean_latency_sec": None,
        "total_cost_est_usd": None,
        # Zero, not None: two rows were examined and neither carried a cost. That is a
        # count of an absence, which is exactly what lets `ladder_deltas` tell "nothing
        # was priced" from "this build records no cost field at all".
        "n_rows_priced": 0,
        "total_tokens": None,
    }
    assert _cost_block([]) == unmeasured

    free = _cost_block(
        [{"latency_sec": 0.0, "cost_est_usd": 0.0, "usage": {"total_tokens": 0}}]
    )
    assert free["total_cost_est_usd"] == 0.0
    assert free["total_latency_sec"] == 0.0
    assert free["total_tokens"] == 0


# --------------------------------------------------------------------------- #
# Crash / refusal parity with the pooled driver
# --------------------------------------------------------------------------- #


class _CrashingSolver:
    """Raises for one question, answers the rest. Mirrors a real solver blowing up."""

    def __init__(self, crash_on: str, sql: str) -> None:
        self._crash_on = crash_on
        self._sql = sql

    def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
        if question == self._crash_on:
            raise KeyError("schema")
        return self._sql, {}

    def solve(self, question: str) -> str | None:
        return self.solve_with_meta(question)[0]


def _two_items(sql: str):
    return [
        EvalItem(question="q ok", sql=sql, question_id="q0", difficulty="simple"),
        EvalItem(question="q boom", sql=sql, question_id="q1", difficulty="simple"),
    ]


def test_a_crash_is_not_scored_as_a_refusal():
    """The defect that cost a set of numbers, in the single-db driver.

    Until this landed, ``refused = sql is None`` meant a solver exception and a
    deliberate refusal were the same row, so ``refusal_rate`` absorbed crashes here
    exactly as it did in the pooled driver.
    """
    sql = "SELECT 1 AS n"
    gold = {
        "q0": GoldHash(
            "q0",
            hash_lenient=hash_normalised_result([(sql,)]),
            hash_strict=None,
            nrows=1,
        )
    }
    rows, summary, _ = _arm(_two_items(sql), gold, _CrashingSolver("q boom", sql))

    assert summary.n_crashed == 1
    assert summary.crash_rate == 0.5
    assert summary.refusal_rate == 0.0, "a crash is our bug, not the model declining"
    assert summary.by_outcome == {"answered": 1, "crashed": 1}

    crashed = next(r for r in rows if r["question_id"] == "q1")
    assert crashed["outcome"] == "crashed"
    assert "KeyError" in str(crashed["error"]), "the exception must survive into the row"


def test_a_genuine_refusal_is_still_a_refusal():
    sql = "SELECT 1 AS n"

    class _Refuser:
        def solve_with_meta(self, question):
            return None, {"refused_by": "no_coverage"}

        def solve(self, question):
            return None

    rows, summary, _ = _arm(_two_items(sql), {}, _Refuser())
    assert summary.n_crashed == 0
    assert summary.crash_rate == 0.0
    assert summary.refusal_rate == 1.0
    assert rows[0]["failed_stage"] == "assemble"


def test_an_empty_arm_reports_unmeasured_rather_than_zero():
    rows, summary, _ = _arm([], {}, _ScriptedSolver({}))
    assert rows == []
    assert summary.n == 0
    assert summary.ex_lenient is None
    assert summary.refusal_rate is None
    assert summary.crash_rate is None
    assert summary.decoy_touch_rate is None


def test_manifest_host_comes_from_the_dsn_not_a_literal():
    """The manifest used to hard-code 127.0.0.1:5435 whatever --pg-dsn said, so two
    runs against different databases were indistinguishable in the record."""
    from governed_bi.eval.harness import _dsn_host

    assert _dsn_host("host=db.internal port=6000 dbname=bird user=u password=p") == (
        "db.internal:6000"
    )
    assert _dsn_host("host=127.0.0.1 port=5435") == "127.0.0.1:5435"
    assert _dsn_host("host=onlyhost") == "onlyhost"
    assert _dsn_host("") == "?"


def test_manifest_host_never_carries_the_password():
    from governed_bi.eval.harness import _dsn_host

    dsn = "host=h port=1 dbname=d user=u password=hunter2"
    assert "hunter2" not in _dsn_host(dsn)
    assert "user" not in _dsn_host(dsn)


# --------------------------------------------------------------------------- #
# Delivery verification must actually observe this driver's rows.
#
# `fingerprint_arm` reads context_hash / n_notes_injected / injected_note_ids /
# context_chars. The single-db driver's row builder recorded none of them, so the
# check built specifically to catch "the corpus never reached the prompt" reported
# `n_rows_observed=0` on every row of every run it produced — which is
# indistinguishable, in the summary, from a clean pass.
# --------------------------------------------------------------------------- #


def test_the_row_builder_records_every_field_the_treatment_check_reads():
    import inspect

    from governed_bi.eval import run_experiment as mod

    src = inspect.getsource(mod)
    for field in (
        "injected_note_ids",
        "n_notes_injected",
        "context_chars",
        "context_hash",
    ):
        assert f'"{field}": meta.get("{field}")' in src, (
            f"{field} is read by fingerprint_arm and never written by this driver"
        )


def test_a_fingerprint_over_this_drivers_rows_is_observed_not_blank():
    """The behavioural version: build rows the way the driver does and check the
    fingerprint sees them."""
    from governed_bi.eval.treatment import fingerprint_arm

    rows = [
        {
            "question_id": f"q{i}",
            "context_hash": f"h{i}",
            "n_notes_injected": 2,
            "injected_note_ids": ["note_a", "note_b"],
            "context_chars": 1234,
        }
        for i in range(3)
    ]
    fp = fingerprint_arm("curated", rows, corpus_note_assets=9)
    assert fp.observed is True
    assert fp.n_rows_observed == 3
    assert fp.n_notes_injected == 6
    assert fp.note_injection_rate == 1.0


def test_a_row_without_delivery_fields_reads_as_unverified_not_as_zero():
    """The regression's shape: absence must not look like a measured no-op."""
    from governed_bi.eval.treatment import fingerprint_arm

    fp = fingerprint_arm("curated", [{"question_id": "q1"}], corpus_note_assets=9)
    assert fp.observed is False
    assert fp.note_injection_rate is None


def test_treatment_and_errors_land_where_the_ledger_looks():
    """`eval.index._undelivered` reads `summary["arms"][arm]["treatment"]`. They used
    to be written only under a per-arm sidecar the gate never looked at."""
    import inspect

    from governed_bi.eval import index
    from governed_bi.eval import run_experiment as mod

    assert 'summary.get("arms")' in inspect.getsource(index._undelivered)
    src = inspect.getsource(mod)
    assert 'block[key] = extra[key]' in src
    assert 'for key in ("errors", "treatment")' in src


def test_the_single_db_driver_indexes_its_run():
    """It never touched the ledger, so nothing it produced could be marked
    not-quotable however badly it went — and it is the driver whose numbers were
    quoted."""
    import inspect

    from governed_bi.eval import run_experiment as mod

    src = inspect.getsource(mod.main)
    assert "index_run" in src
    assert "not quotable" in src.lower()
