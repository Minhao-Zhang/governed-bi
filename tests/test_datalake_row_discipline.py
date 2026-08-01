"""Row-shape and absent-vs-zero discipline in the pooled data-lake driver.

Both grade through the same ``score_sql_hashes`` call, so a field the row
builder drops is a measurement the run simply does not have. These tests pin
the result-shape fields per row, the cost/attempt aggregates, the None-vs-zero
discipline the aggregates depend on, and the manifest host redaction.

Driven with a scripted solver and an in-memory echo gateway: no model, no
graph, no Postgres.
"""

from __future__ import annotations

from governed_bi.eval import run_datalake
from governed_bi.eval.dataset import EvalItem
from governed_bi.eval.harness import _cost_block
from governed_bi.eval.hash_grade import GoldHash, hash_normalised_result
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


def test_rows_carry_result_shape_fields(tmp_path):
    items, gold, solver = _fixture()
    rows, _summary = run_datalake._run_pool_arm(
        arm="curated",
        solver=solver,
        pairs=[(it, "db_a") for it in items],
        gold_hashes=gold,
        gateway=_EchoGateway(),
        identity=IDENTITY,
        bird_dir=None,
        suspect_by_db={},
        arm_corpus=None,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
        out_path=tmp_path / "generations.curated.jsonl",
    )
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
# context_chars — the fields that catch "the corpus never reached the prompt".
# --------------------------------------------------------------------------- #


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
