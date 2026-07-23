"""Serve-loop concurrency invariance (docs/plans/eval-concurrency-design.md).

The executable form of the design's results-invariance argument: the parallel
serve routine with ``workers >= 3`` must produce byte-identical per-question rows
(modulo the timing-only ``latency_sec`` field) and an identical summary to the
serial ``workers == 1`` path.

These tests isolate the *scheduler + aggregation + ordering* — the only thing
concurrency can change — by driving the two drivers' serve routines with a
deterministic stub solver and an in-memory echo gateway. No real graph, model, or
Postgres is involved, so any divergence is a scheduling/ordering bug, not model
nondeterminism.
"""

from __future__ import annotations

import time
from dataclasses import asdict

import pytest

from governed_bi.eval.arms import MetaSolver
from governed_bi.eval.dataset import EvalItem
from governed_bi.eval.hash_grade import (
    GoldHash,
    hash_normalised_result,
    hash_normalised_result_strict,
)
from governed_bi.eval.parallel import (
    MAX_SANE_WORKERS,
    ServeWorker,
    resolve_workers,
)
from governed_bi.eval.run_datalake import _run_pool_arm
from governed_bi.eval.run_experiment import ArmSummary, _run_arm_generations
from governed_bi.gateway import Identity
from governed_bi.gateway.connectors.base import QueryResult

DBS = ["db_a", "db_b", "db_c"]
SUSPECT_BY_DB = {
    "db_a": frozenset({"decoy_a"}),
    "db_b": frozenset({"decoy_b"}),
    "db_c": frozenset(),
}
IDENTITY = Identity(user="eval", all_access=True)


# --------------------------------------------------------------------------- #
# Deterministic stubs (no graph / model / DB)
# --------------------------------------------------------------------------- #


def _sql_for(i: int) -> str | None:
    """Deterministic per-question SQL. Some refuse; some touch a decoy column."""
    if i % 4 == 3:
        return None  # refusal
    if i % 5 == 0:
        return f'SELECT "decoy_a", "decoy_b" FROM "t{i}"'  # touches suspect sets
    return f"SELECT {i} AS n"


def _meta_for(i: int, db: str) -> dict:
    """Deterministic per-question audit meta, varying the routing fields so the
    pooled driver's routing/pick counters are actually exercised."""
    routed = [db] if i % 3 != 0 else ["other_schema"]  # true schema sometimes dropped
    if i % 3 == 0:
        pick = None
    elif i % 3 == 1:
        pick = db  # correct pick
    else:
        pick = "other_schema"  # wrong pick
    return {
        "refused_by": "refuse_gate" if _sql_for(i) is None else None,
        "failed_layer": None,
        "graded_delivery": bool(i % 2),
        "coverage_best_effort": False,
        "tier": "certified" if i % 2 else "unverified",
        "semantic_assurance": "verified" if i % 2 else "unverified",
        "safety_clearance": True,
        "attempts": i % 3,
        "routed_schemas": routed,
        "schema_pick": pick,
        "total_schemas": len(DBS),
        "usage": {"total_tokens": 10 + i},
        "cost_est_usd": 0.001 * i,
    }


class _StubSolver:
    """A :class:`MetaSolver` whose output is a pure function of the question — no
    shared state, safe to instantiate per worker and call concurrently."""

    def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
        i, db = _QUESTION_INDEX[question]
        time.sleep(0.01)  # widen the window so >1 worker thread is actually used
        return _sql_for(i), _meta_for(i, db)

    def solve(self, question: str) -> str | None:
        return self.solve_with_meta(question)[0]


class _EchoConn:
    def close(self) -> None:  # ServeWorker teardown calls this
        pass


class _EchoGateway:
    """Deterministic gateway: a query's result set is a pure function of its SQL,
    so grading is identical regardless of which worker executes it."""

    def execute(self, sql: str, identity: Identity) -> QueryResult:
        return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)


# question -> (index, db); built per test from the items.
_QUESTION_INDEX: dict[str, tuple[int, str]] = {}


def _build_items(n: int) -> list[EvalItem]:
    items: list[EvalItem] = []
    _QUESTION_INDEX.clear()
    diffs = ["simple", "moderate", "challenging"]
    for i in range(n):
        db = DBS[i % len(DBS)]
        q = f"question {i}"
        _QUESTION_INDEX[q] = (i, db)
        items.append(
            EvalItem(
                question=q,
                sql=f"SELECT {i} AS n",  # gold reference for the crosscheck
                question_id=f"q{i}",
                difficulty=diffs[i % len(diffs)],
            )
        )
    return items


def _gold_hashes(items: list[EvalItem]) -> dict[str, GoldHash]:
    """Half the produced items match (gold == echo hash); the rest miss."""
    out: dict[str, GoldHash] = {}
    for item in items:
        i, _db = _QUESTION_INDEX[item.question]
        qid = str(item.question_id)
        sql = _sql_for(i)
        if sql is None:
            out[qid] = GoldHash(qid, hash_lenient="unused", hash_strict="unused")
            continue
        if i % 2 == 0:  # correct: gold matches the echo gateway's hash of this SQL
            out[qid] = GoldHash(
                qid,
                hash_lenient=hash_normalised_result([(sql,)]),
                hash_strict=hash_normalised_result_strict([(sql,)]),
                nrows=1,
            )
        else:  # incorrect: deliberately wrong hash
            out[qid] = GoldHash(qid, hash_lenient="wrong", hash_strict="wrong", nrows=1)
    return out


def _strip_latency(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "latency_sec"} for r in rows]


def test_stub_satisfies_meta_solver_protocol():
    assert isinstance(_StubSolver(), MetaSolver)


# --------------------------------------------------------------------------- #
# Invariance: pooled data-lake driver (_run_pool_arm)
# --------------------------------------------------------------------------- #


def test_datalake_pool_arm_workers_invariance():
    items = _build_items(12)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)

    common = dict(
        arm="curated",
        pairs=pairs,
        gold_hashes=gold,
        identity=IDENTITY,
        bird_dir=None,
        suspect_by_db=SUSPECT_BY_DB,
        dialect="postgres",
    )

    rows_serial, summary_serial = _run_pool_arm(
        solver=_StubSolver(), gateway=_EchoGateway(), serve_workers=1, **common
    )

    built: list[ServeWorker] = []

    def factory(idx: int) -> ServeWorker:
        w = ServeWorker(connector=_EchoConn(), gateway=_EchoGateway(), solver=_StubSolver())
        built.append(w)
        return w

    rows_parallel, summary_parallel = _run_pool_arm(
        solver=_StubSolver(),
        gateway=_EchoGateway(),
        serve_workers=4,
        worker_factory=factory,
        **common,
    )

    assert len(built) >= 2, "expected real fan-out across worker threads"
    assert _strip_latency(rows_parallel) == _strip_latency(rows_serial)
    assert summary_parallel == summary_serial
    # The run actually exercised the branches we care about.
    assert summary_serial["refusal_rate"] > 0
    assert 0 < summary_serial["ex_lenient"] < 1
    assert summary_serial["routing_recall"] > 0
    assert summary_serial["schema_pick_accuracy"] is not None
    assert summary_serial["decoy_touch_rate"] > 0


# --------------------------------------------------------------------------- #
# Invariance: pinned per-DB driver (_run_arm_generations)
# --------------------------------------------------------------------------- #


def test_experiment_arm_generations_workers_invariance():
    items = _build_items(12)
    gold = _gold_hashes(items)
    suspect = frozenset({"decoy_a", "decoy_b"})

    common = dict(
        arm="curated",
        items=items,
        gold_hashes=gold,
        identity=IDENTITY,
        bird_dir=None,
        suspect_columns=suspect,
        dialect="postgres",
    )

    rows_serial, summary_serial, extra_serial = _run_arm_generations(
        solver=_StubSolver(), gateway=_EchoGateway(), serve_workers=1, **common
    )

    built: list[ServeWorker] = []

    def factory(idx: int) -> ServeWorker:
        w = ServeWorker(connector=_EchoConn(), gateway=_EchoGateway(), solver=_StubSolver())
        built.append(w)
        return w

    rows_parallel, summary_parallel, extra_parallel = _run_arm_generations(
        solver=_StubSolver(),
        gateway=_EchoGateway(),
        serve_workers=4,
        worker_factory=factory,
        **common,
    )

    assert len(built) >= 2, "expected real fan-out across worker threads"
    assert _strip_latency(rows_parallel) == _strip_latency(rows_serial)
    assert asdict(summary_parallel) == asdict(summary_serial)
    assert extra_parallel == extra_serial
    assert summary_serial.refusal_rate > 0
    assert 0 < summary_serial.ex_lenient < 1
    assert summary_serial.decoy_touch_rate > 0
    # Cross-check agreement was computed for the produced (non-refused) items.
    assert extra_serial["ex_crosscheck_n"] > 0


def test_missing_factory_when_parallel_raises():
    items = _build_items(3)
    gold = _gold_hashes(items)
    with pytest.raises(ValueError, match="worker_factory"):
        _run_arm_generations(
            arm="curated",
            solver=_StubSolver(),
            items=items,
            gold_hashes=gold,
            gateway=_EchoGateway(),
            identity=IDENTITY,
            bird_dir=None,
            suspect_columns=frozenset(),
            dialect="postgres",
            serve_workers=2,
            worker_factory=None,
        )


# --------------------------------------------------------------------------- #
# Pool-sizing guard (resolve_workers)
# --------------------------------------------------------------------------- #


def test_resolve_workers_clamps_and_warns(capsys):
    # Below 1 is floored to serial with a warning.
    assert resolve_workers(0) == 1
    assert resolve_workers(-4) == 1
    # A sane value passes through silently.
    assert resolve_workers(4) == 4
    capsys.readouterr()
    # Above the cap: unchanged (never silently reduced) but loudly warned.
    assert resolve_workers(MAX_SANE_WORKERS + 50) == MAX_SANE_WORKERS + 50
    out = capsys.readouterr().out
    assert "exceeds the sane cap" in out


def test_config_eval_workers_parsed(tmp_path):
    from governed_bi.config import load_settings

    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text(
        "[eval]\nworkers = 6\nserve_workers = 9\n", encoding="utf-8"
    )
    settings = load_settings(cfg, apply_local=False)
    assert settings.eval_workers == 6
    assert settings.eval_serve_workers == 9
    assert settings.serve_worker_count() == 9  # split override wins


def test_config_eval_workers_defaults(tmp_path):
    from governed_bi.config import load_settings

    cfg = tmp_path / "governed_bi.toml"
    cfg.write_text("[eval]\nworkers = 3\n", encoding="utf-8")
    settings = load_settings(cfg, apply_local=False)
    assert settings.eval_workers == 3
    assert settings.eval_serve_workers is None
    assert settings.serve_worker_count() == 3  # falls back to workers
