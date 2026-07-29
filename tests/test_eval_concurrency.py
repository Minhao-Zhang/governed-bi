"""Serve-loop concurrency invariance (docs/measurement.md).

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

import json
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
    run_ordered_pool,
)
from governed_bi.eval.run_datalake import _read_rows, _run_pool_arm
from governed_bi.eval.run_experiment import _run_arm_generations
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


def _strip_cost(summary: dict) -> dict:
    """The scored half of an arm summary — everything but wall-clock/token cost.

    Recurses, because ``by_db`` holds a full per-database summary each carrying its own
    ``cost`` block. Stripping only the top level compared scheduler-dependent
    wall-clock numbers nested one level down and failed for the very reason this
    exclusion exists.
    """
    def _scored_cost(cost: dict) -> dict:
        # Only the wall-clock fields are scheduler-dependent. Token totals, dollar
        # totals and the priced-row count are pure functions of the questions and the
        # solver, so they must match across widths — and `n_rows_priced` in particular
        # is now a load-bearing denominator for `ladder_deltas`, which makes it a result
        # rather than a cost artifact. Dropping the whole block hid all three.
        return {k: v for k, v in cost.items() if not k.endswith("latency_sec")}

    return {
        k: (
            {db: _strip_cost(v) for db, v in val.items()}
            if k == "by_db"
            else (_scored_cost(val) if k == "cost" else val)
        )
        for k, val in summary.items()
    }


def test_stub_satisfies_meta_solver_protocol():
    assert isinstance(_StubSolver(), MetaSolver)


# --------------------------------------------------------------------------- #
# Invariance: pooled data-lake driver (_run_pool_arm)
# --------------------------------------------------------------------------- #


def test_datalake_pool_arm_workers_invariance(tmp_path):
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
        # Explicitly None: this test does not exercise the routing-escape metric, and
        # the argument is required so a forgotten production wiring cannot silently
        # disable it.
        arm_corpus=None,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
    )

    serial_path = tmp_path / "generations.serial.jsonl"
    rows_serial, summary_serial = _run_pool_arm(
        solver=_StubSolver(),
        gateway=_EchoGateway(),
        serve_workers=1,
        out_path=serial_path,
        **common,
    )

    built: list[ServeWorker] = []

    def factory(idx: int) -> ServeWorker:
        w = ServeWorker(connector=_EchoConn(), gateway=_EchoGateway(), solver=_StubSolver())
        built.append(w)
        return w

    parallel_path = tmp_path / "generations.parallel.jsonl"
    rows_parallel, summary_parallel = _run_pool_arm(
        solver=_StubSolver(),
        gateway=_EchoGateway(),
        serve_workers=4,
        worker_factory=factory,
        out_path=parallel_path,
        **common,
    )

    assert len(built) >= 2, "expected real fan-out across worker threads"
    assert _strip_latency(rows_parallel) == _strip_latency(rows_serial)
    # Every *scored* field must match exactly. The nested "cost" block is excluded
    # because wall-clock is scheduler-dependent by design — that it differs is the
    # point of concurrency, and folding it in with the results would make the
    # invariance guarantee untestable.
    assert _strip_cost(summary_parallel) == _strip_cost(summary_serial)
    assert set(summary_serial["cost"]) == {
        "total_latency_sec",
        "mean_latency_sec",
        "total_cost_est_usd",
        "n_rows_priced",
        "total_tokens",
    }
    # The streaming sink must persist the same rows, in the same order, on both
    # paths — otherwise a resumed pooled run would replay a different set than a
    # resumed serial one.
    assert _strip_latency(_read_rows(parallel_path)) == _strip_latency(
        _read_rows(serial_path)
    )
    assert len(_read_rows(serial_path)) == len(pairs)
    # The run actually exercised the branches we care about.
    assert summary_serial["refusal_rate"] > 0
    assert 0 < summary_serial["ex_lenient"] < 1
    assert summary_serial["routing_recall"] > 0
    assert summary_serial["schema_pick_accuracy"] is not None
    assert summary_serial["decoy_touch_rate"] > 0


# --------------------------------------------------------------------------- #
# Resume: an interrupted run must score identically to an uninterrupted one
# --------------------------------------------------------------------------- #


def _pool_args(pairs, gold):
    return dict(
        arm="curated",
        pairs=pairs,
        gold_hashes=gold,
        identity=IDENTITY,
        bird_dir=None,
        suspect_by_db=SUSPECT_BY_DB,
        # Explicitly None: this test does not exercise the routing-escape metric, and
        # the argument is required so a forgotten production wiring cannot silently
        # disable it.
        arm_corpus=None,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
    )


class _CountingSolver(_StubSolver):
    """Counts how many questions were actually served, so a resume that silently
    re-serves everything (or skips too much) fails loudly."""

    def __init__(self) -> None:
        self.served: list[str] = []

    def solve_with_meta(self, question: str):
        self.served.append(question)
        return super().solve_with_meta(question)


def test_resume_replays_scored_rows_and_matches_a_clean_run(tmp_path):
    items = _build_items(12)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)

    clean_path = tmp_path / "clean.jsonl"
    _rows, summary_clean = _run_pool_arm(
        solver=_StubSolver(), gateway=_EchoGateway(), out_path=clean_path,
        **_pool_args(pairs, gold),
    )

    # Simulate a run killed after 5 questions, then resumed over the full pool.
    partial_path = tmp_path / "partial.jsonl"
    first = _CountingSolver()
    _run_pool_arm(
        solver=first, gateway=_EchoGateway(), out_path=partial_path,
        **_pool_args(pairs[:5], gold),
    )
    assert len(first.served) == 5

    second = _CountingSolver()
    _rows, summary_resumed = _run_pool_arm(
        solver=second, gateway=_EchoGateway(), out_path=partial_path, resume=True,
        **_pool_args(pairs, gold),
    )
    assert len(second.served) == 7, "resume must serve only the unscored remainder"
    assert len(_read_rows(partial_path)) == 12
    # Scored fields must match a clean run exactly. Cost is excluded for the same
    # reason as in the concurrency test: a resumed run's wall-clock is split across
    # two processes, so equality there would be asserting the wrong invariant.
    assert _strip_cost(summary_resumed) == _strip_cost(summary_clean)


def test_without_resume_a_stale_file_is_truncated_not_appended(tmp_path):
    """Re-running into an existing directory without --resume must not double-count
    the questions already there."""
    items = _build_items(6)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)
    path = tmp_path / "gen.jsonl"

    for _ in range(2):
        _rows, summary = _run_pool_arm(
            solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
            **_pool_args(pairs, gold),
        )
    assert summary["n"] == 6
    assert len(_read_rows(path)) == 6


def test_resume_ignores_rows_outside_the_current_pool(tmp_path):
    """A narrower --limit must not drag previously-scored questions into the
    denominator of a smaller run."""
    items = _build_items(12)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)
    path = tmp_path / "gen.jsonl"

    _run_pool_arm(
        solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
        **_pool_args(pairs, gold),
    )
    _rows, summary = _run_pool_arm(
        solver=_StubSolver(), gateway=_EchoGateway(), out_path=path, resume=True,
        **_pool_args(pairs[:4], gold),
    )
    assert summary["n"] == 4


def test_resume_refuses_to_mix_splits_with_disjoint_question_ids(tmp_path):
    """The guard must inspect every row on disk, not just rows in the current pool.

    BIRD's train and test question ids are disjoint, so narrowing to the pool
    first drops the foreign-split rows and makes the check unreachable — while the
    file quietly accumulates two splits. A same-ids test cannot catch that.
    """
    path = tmp_path / "gen.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {"question_id": f"train_only_{i}", "split": "train", "correct": True}
            )
            + "\n"
            for i in range(3)
        ),
        encoding="utf-8",
    )
    items = _build_items(3)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    with pytest.raises(RuntimeError, match="split"):
        _run_pool_arm(
            solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
            split="test", resume=True, **_pool_args(pairs, _gold_hashes(items)),
        )


def test_resume_survives_a_row_truncated_mid_write(tmp_path):
    """Appending onto a partial final line would splice the next row into the
    wreckage and lose both; the sink must terminate the fragment first."""
    path = tmp_path / "gen.jsonl"
    items = _build_items(4)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)

    _run_pool_arm(
        solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
        **_pool_args(pairs[:2], gold),
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"question_id": "q2", "corr')  # killed mid-write

    _rows, summary = _run_pool_arm(
        solver=_StubSolver(), gateway=_EchoGateway(), out_path=path, resume=True,
        **_pool_args(pairs, gold),
    )
    on_disk = _read_rows(path)
    assert summary["n"] == 4
    assert len(on_disk) == 4, "the re-served row must survive the truncated fragment"
    assert sorted(r["question_id"] for r in on_disk) == ["q0", "q1", "q2", "q3"]


def test_resume_refuses_to_mix_splits(tmp_path):
    items = _build_items(4)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)
    path = tmp_path / "gen.jsonl"

    _run_pool_arm(
        solver=_StubSolver(), gateway=_EchoGateway(), out_path=path, split="train",
        **_pool_args(pairs, gold),
    )
    with pytest.raises(RuntimeError, match="split"):
        _run_pool_arm(
            solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
            split="test", resume=True, **_pool_args(pairs, gold),
        )


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


# --------------------------------------------------------------------------- #
# Pool observability (per-worker counts; teardown failures)
# --------------------------------------------------------------------------- #


def _pool_factory(conn_cls=_EchoConn):
    def factory(idx: int) -> ServeWorker:
        return ServeWorker(connector=conn_cls(), gateway=_EchoGateway(), solver=None)

    return factory


def test_pool_result_reports_how_work_was_distributed():
    items = list(range(9))
    out = run_ordered_pool(
        items,
        workers=3,
        make_worker=_pool_factory(),
        run_task=lambda w, i: i * 2,
    )
    # A plain list compares equal to the result, which is exactly why the counters
    # live on the container: they cannot leak into a scored row comparison.
    assert out == [i * 2 for i in items]
    assert out.n_tasks == len(items)
    assert out.n_failures == 0
    assert out.close_errors == []
    assert sum(w.n_tasks for w in out.workers) == len(items)
    assert {w.worker_index for w in out.workers} == set(range(len(out.workers)))


def test_worker_teardown_failure_is_recorded_and_printed(capsys):
    """The audit's one failure with no signal anywhere: a bare ``except: pass`` around
    ``connector.close()``. It still must not mask a task error, so it is reported
    rather than raised."""

    class _BadConn:
        def close(self) -> None:
            raise RuntimeError("connection reset by peer")

    out = run_ordered_pool(
        [1, 2, 3],
        workers=2,
        make_worker=_pool_factory(_BadConn),
        run_task=lambda w, i: i,
    )
    assert out == [1, 2, 3]  # a leaked connection must not fail a multi-hour run
    assert out.close_errors
    assert all("connection reset by peer" in e for e in out.close_errors)
    printed = capsys.readouterr().out
    assert "connector.close() failed" in printed


def test_task_failure_is_counted_and_reraised(capsys):
    def boom(worker: ServeWorker, item: int) -> int:
        if item == 2:
            raise RuntimeError("task exploded")
        return item

    with pytest.raises(RuntimeError, match="task exploded"):
        run_ordered_pool([1, 2, 3], workers=1, make_worker=_pool_factory(), run_task=boom)
    # Recorded on the way out, not swallowed: a pool that absorbs task errors turns a
    # crashing arm into a merely-refusing one.
    assert "1 failure(s)" in capsys.readouterr().out


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


# --------------------------------------------------------------------------- #
# A resume re-serves crashed turns.
#
# `quotable()` refuses any arm with a non-zero crash rate, so one bad turn in 10,150
# disqualifies a run that cost hours and a real model budget. Resume used to hand that
# same row straight back — `done_ids` was built from every row on disk regardless of
# outcome — leaving hand-editing the JSONL as the only recovery from a transient
# provider failure that a re-serve would very likely clear.
# --------------------------------------------------------------------------- #


def _write_rows(path, rows):
    """Rewrite a generations file. Local rather than imported: the driver's own writer
    lives in `run_experiment` and this test only needs to stage a fixture."""
    payload = [json.dumps(r, ensure_ascii=False) for r in rows]
    nl = chr(10)
    path.write_text(nl.join(payload) + nl, encoding="utf-8")


def _crash_row(rows, qid):
    """Rewrite one scored row on disk into the shape a crashed turn leaves behind."""
    out = []
    for r in rows:
        if str(r.get("question_id")) == qid:
            r = {
                **r,
                "outcome": "crashed",
                "refused_by": "model_error",
                "error_type": "RateLimitError",
                "generated_sql": None,
                "correct": False,
            }
        out.append(r)
    return out


def test_a_resume_re_serves_a_crashed_turn(tmp_path):
    items = _build_items(6)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)
    path = tmp_path / "generations.curated.jsonl"

    _run_pool_arm(solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
                  **_pool_args(pairs, gold))
    rows = _read_rows(path)
    assert len(rows) == 6
    victim = str(rows[2]["question_id"])
    _write_rows(path, _crash_row(rows, victim))

    second = _CountingSolver()
    resumed, summary = _run_pool_arm(
        solver=second, gateway=_EchoGateway(), out_path=path, resume=True,
        **_pool_args(pairs, gold),
    )

    assert second.served, "the crashed turn was not re-served"
    assert len(second.served) == 1, f"only the crashed turn should re-serve: {second.served}"
    # Exactly one row per question — a stale crashed row left beside the new one would
    # double-count in every denominator, and `eval.analysis` rejects the file outright.
    on_disk = _read_rows(path)
    assert len(on_disk) == 6
    ids = [str(r["question_id"]) for r in on_disk]
    assert len(set(ids)) == 6, f"duplicate question_id after re-serve: {ids}"
    # Crash rate is cleared on the new rows — but the re-serve is durable so it
    # cannot silently restore quotability (audit E1).
    assert summary["crash_rate"] == 0.0, "the re-served turn is still recorded as a crash"
    assert summary["n_re_served"] == 1, "re-serve count must land in the arm summary"


def test_replay_crashed_keeps_the_old_behaviour(tmp_path):
    """The honest opt-in: keep crashed rows, leave crash_rate visible, re-serve nothing."""
    items = _build_items(6)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)
    path = tmp_path / "generations.curated.jsonl"

    _run_pool_arm(solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
                  **_pool_args(pairs, gold))
    rows = _read_rows(path)
    _write_rows(path, _crash_row(rows, str(rows[2]["question_id"])))

    second = _CountingSolver()
    _rows, summary = _run_pool_arm(
        solver=second, gateway=_EchoGateway(), out_path=path, resume=True,
        replay_crashed=True, **_pool_args(pairs, gold),
    )
    assert second.served == [], "nothing should be re-served under --replay-crashed"
    assert summary["crash_rate"] > 0.0, "the crash must survive the replay"
    assert summary["n_re_served"] == 0


def test_a_resume_with_no_crashes_serves_nothing(tmp_path):
    """The check must not re-serve healthy rows — that would silently double the cost
    of every resume."""
    items = _build_items(6)
    pairs = [(item, DBS[i % len(DBS)]) for i, item in enumerate(items)]
    gold = _gold_hashes(items)
    path = tmp_path / "generations.curated.jsonl"

    _run_pool_arm(solver=_StubSolver(), gateway=_EchoGateway(), out_path=path,
                  **_pool_args(pairs, gold))
    second = _CountingSolver()
    _rows, summary = _run_pool_arm(
        solver=second, gateway=_EchoGateway(), out_path=path, resume=True,
        **_pool_args(pairs, gold),
    )
    assert second.served == []
    assert len(_read_rows(path)) == 6
    assert summary["n_re_served"] == 0


# --------------------------------------------------------------------------- #
# Oracle rungs fan out too
# --------------------------------------------------------------------------- #


def test_an_oracle_rung_gets_one_isolated_solver_per_worker(monkeypatch):
    """Rungs were pinned to one worker; step 3 is three of them over the whole split.

    The stated reason was that a rung "rebuilds a graph per narrowed corpus, so it
    cannot share the per-arm worker factory". But that cache is closure-local to one
    `oracle_solver` call — which makes a *per-worker* solver safe, not impossible. It
    is the isolation every fair arm already had.

    What has to hold: each worker index gets its OWN solver (a shared one would race
    on the graph cache and on the `n_built` counter behind `session_id`), its own
    connector and gateway, and a distinct session id.
    """
    from governed_bi.eval import run_datalake as mod
    from governed_bi.eval.oracle import OracleRung

    built: list[dict] = []

    def fake_oracle_solver(rung, corpus, gateway, settings, identity, **kw):
        record = {"rung": rung, "gateway": gateway, **kw}
        built.append(record)
        return object()

    monkeypatch.setattr(mod, "oracle_solver", fake_oracle_solver)
    monkeypatch.setattr(mod, "PostgresConnector", lambda dsn, schema=None: _EchoConn())
    monkeypatch.setattr(mod, "Gateway", lambda conn, **kw: _EchoGateway())

    factory = mod.make_serve_worker_factory(
        corpus=object(),
        pg_dsn="postgresql://x/y",
        settings=object(),
        identity=IDENTITY,
        model=object(),
        arm="curated",
        rung=OracleRung.schema,
        gold=object(),
        n_workers=4,
    )
    workers = [factory(i) for i in range(4)]

    assert len({id(w.solver) for w in workers}) == 4, "workers shared one solver"
    assert len({id(w.connector) for w in workers}) == 4
    assert len({id(w.gateway) for w in workers}) == 4
    assert len({r["session_id"] for r in built}) == 4, built
    # Each worker's gateway is the one its solver was built against, or a question
    # would solve on one connection and grade on another.
    for worker, record in zip(workers, built):
        assert record["gateway"] is worker.gateway

    # Total compiled-graph footprint is held flat: the per-solver cap is divided by
    # the worker count, not paid once per worker.
    assert all(r["graph_cache_max"] * 4 <= 32 for r in built), built

    # ...flat up to 8 workers, and honestly NOT flat past that — the floor of 4 takes
    # over, which is deliberate (a cap of 1 defeats the reuse that matters) but is not
    # what "does not grow with width" would mean. Pinned so the docstring and the
    # runbook cannot drift back into claiming it unqualified.
    def _cap(n: int) -> int:
        return max(4, 32 // max(1, n))

    assert [_cap(n) * n for n in (1, 2, 4, 8)] == [32, 32, 32, 32]
    assert _cap(16) * 16 == 64
    assert _cap(32) * 32 == 128  # MAX_SANE_WORKERS


def test_the_oracle_factory_refuses_to_build_without_a_gold_index():
    """`gold` is the one argument a rung cannot be constructed without, and passing
    `None` would surface as an unrelated failure deep inside the first turn."""
    from governed_bi.eval import run_datalake as mod
    from governed_bi.eval.oracle import OracleRung

    with pytest.raises(ValueError, match="gold index"):
        mod.make_serve_worker_factory(
            corpus=object(),
            pg_dsn="postgresql://x/y",
            settings=object(),
            identity=IDENTITY,
            model=object(),
            arm="curated",
            rung=OracleRung.tables,
            gold=None,
        )


def test_the_curried_factory_carries_the_rung_through(monkeypatch):
    """The last place an oracle rung can be lost, and the paid run is its first caller.

    `arm_worker_factory` was a closure over `run_datalake`'s locals, so no test could
    reach it: dropping `rung=` on the way to `make_serve_worker_factory` left the whole
    suite green while making every rung serve as an ordinary arm under a rung's name —
    `oracle_schema`, `oracle_tables`, `oracle_tables_padded` in the artifact, all of
    them actually just `curated` served again. Those are the headroom bounds every
    other number in the runbook is read against.

    Unreachable offline: `--skip-agent` rejects every rung but `oracle_sql`, and the
    worker count is forced to 1 without a model.
    """
    from governed_bi.eval import run_datalake as mod
    from governed_bi.eval.oracle import OracleRung

    seen: list[dict] = []
    monkeypatch.setattr(
        mod,
        "oracle_solver",
        lambda rung, corpus, gateway, settings, identity, **kw: seen.append(
            {"rung": rung, "corpus": corpus, **kw}
        )
        or object(),
    )
    monkeypatch.setattr(
        mod, "agent_solver", lambda *a, **kw: seen.append({"rung": None}) or object()
    )
    monkeypatch.setattr(mod, "PostgresConnector", lambda dsn, schema=None: _EchoConn())
    monkeypatch.setattr(mod, "Gateway", lambda conn, **kw: _EchoGateway())

    gold = object()
    curated_corpus = object()
    bindings = mod.ServeBindings(
        corpora_serve={"curated": curated_corpus, "baseline": object()},
        pg_dsn="postgresql://x/y",
        settings=object(),
        identity=IDENTITY,
        model=object(),
        embedder=None,
        gold=gold,
    )

    plan = mod.plan_arm_serving(
        rung=OracleRung.schema,
        source_arm="oracle_schema",
        oracle_base="curated",
        effective_workers=4,
        has_model=True,
    )
    mod.arm_worker_factory(plan, bindings)(0)

    assert seen, "no solver was built"
    assert seen[0]["rung"] is OracleRung.schema, "the rung was dropped"
    assert seen[0]["corpus"] is curated_corpus, "a rung narrows its BASE arm's corpus"
    assert seen[0]["gold"] is gold, "a rung without gold cannot narrow anything"

    # A fair arm still gets the ordinary solver and no gold.
    seen.clear()
    fair = mod.plan_arm_serving(
        rung=None,
        source_arm="curated",
        oracle_base="curated",
        effective_workers=4,
        has_model=True,
    )
    mod.arm_worker_factory(fair, bindings)(0)
    assert seen[0]["rung"] is None
