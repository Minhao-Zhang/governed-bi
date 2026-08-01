"""The serve-phase circuit breaker, and the abort path that makes it worth having.

The incident (2026-07-31): a serve run reached 48% crashed by row 655 of 1351 and ran
to the end, two hours and roughly thirty dollars, before ``crash_rate > 0 -> not
quotable`` said so from ``summary.json``. Per-worker duplicate embedding had exhausted a
shared 1M TPM budget; a second, unrelated run inherited 39 crashed rows from the same
cause and nothing linked the two. The BUILD phase has ``_assert_build_coverage`` and
``_quarantine_curator_failures`` for exactly this shape. The SERVE phase, where the money
goes, had nothing.

Two things have to hold, and they pull against each other:

* the breaker must fire early enough to save the spend, and
* it must never fire on a healthy arm — the real ones on this benchmark finish at 0 and
  2 crashes out of 1351, and a gate that cries wolf gets switched off forever.

Every constant in ``eval.serve_breaker`` is pinned below against a *seeded simulation*,
not against itself. Moving any of them turns
``test_the_operating_characteristics_are_what_the_docstring_claims`` red, which is the
only thing standing between the module docstring's table and the class of defect this
repo keeps finding: a number that describes the world, written as a literal, pinned to
nothing.
"""

from __future__ import annotations

import random
import threading

import pytest

from governed_bi.eval.parallel import PoolAborted, ServeWorker, run_ordered_pool
from governed_bi.eval.serve_breaker import (
    MIN_CRASHES,
    MIN_ROWS,
    ServeCircuitBreaker,
)


def _drive(p_crash, *, seed, n=1351, onset=0, p_pre=0.0):
    """Feed one arm's worth of Bernoulli crashes and return the trip row (or None)."""
    rng = random.Random(seed)
    breaker = ServeCircuitBreaker(arm="curated", total=n)
    for i in range(n):
        breaker.observe(crashed=rng.random() < (p_pre if i < onset else p_crash))
        if breaker.tripped:
            return breaker.tripped_at_row
    return None


def _characteristics(p_crash, *, trials=400, **kw):
    trips = [_drive(p_crash, seed=s, **kw) for s in range(trials)]
    hit = [t for t in trips if t is not None]
    return len(hit) / trials, (sorted(hit)[len(hit) // 2] if hit else None)


# --------------------------------------------------------------------------- #
# Does not fire on a healthy arm
# --------------------------------------------------------------------------- #


def test_the_real_healthy_arms_can_never_trip():
    """The 2026-08-01 luna-max ladder: baseline 0/1351, curated 2/1351.

    Not "unlikely to trip" — *cannot*. Two crashes is below ``MIN_CRASHES``, so no
    sequence of 1351 rows containing two crashes reaches the evidence test at all. That
    is the property worth having: the operator does not have to trust a probability.
    """
    breaker = ServeCircuitBreaker(arm="curated", total=1351)
    for i in range(1351):
        breaker.observe(crashed=i in (200, 900))
    assert breaker.n_crashed == 2
    assert breaker.tripped is False
    assert breaker.crash_rate == pytest.approx(2 / 1351)


def test_a_burst_at_the_very_start_does_not_kill_the_run():
    """Cold connection pools, a provider warming up. Four crashes in the first four
    rows is not evidence about 1351, and ``MIN_ROWS`` says so."""
    breaker = ServeCircuitBreaker(arm="baseline", total=1351)
    for _ in range(MIN_CRASHES - 1):
        breaker.observe(crashed=True)
    assert breaker.tripped is False
    assert breaker.n_rows < MIN_ROWS


def test_a_clean_arm_never_trips():
    assert _characteristics(0.0, trials=20) == (0.0, None)


# --------------------------------------------------------------------------- #
# Does fire, fast, on the incident
# --------------------------------------------------------------------------- #


def test_the_incident_trips_inside_the_first_thirty_rows():
    """48% crashed. The real run reached row 1351; this stops at ~20."""
    rate, median = _characteristics(0.48)
    assert rate == 1.0
    assert median is not None and median <= 30, median


def test_a_failure_that_starts_late_is_caught_ten_rows_later():
    """The incident's actual shape, and the reason the accumulator is floored at zero.

    600 clean rows, then the embedding channel dies. An unfloored sequential test has
    ~92 nats of negative evidence banked by then and needs ~100 crashed rows to work it
    off before it can fire. The changepoint form fires ten rows in.
    """
    rate, median = _characteristics(0.48, trials=100, onset=600, p_pre=0.0)
    assert rate == 1.0
    assert median is not None and median - 600 <= 25, median


def test_the_zero_floor_is_load_bearing_and_not_decoration():
    """A clean prefix must bank no negative evidence, so the storm after it is judged on
    its own terms rather than having to pay off a surplus first."""
    late = ServeCircuitBreaker(arm="a")
    for _ in range(600):
        late.observe(crashed=False)
    assert late.log_evidence == 0.0, "a clean prefix must not bank negative evidence"
    while not late.tripped:
        late.observe(crashed=True)
    # Five crashed rows is ``MIN_CRASHES``: the storm trips the moment it can, which is
    # the floor talking, not the evidence. Without the zero floor the accumulator sits at
    # about -122 nats here and the same five crashes leave it nowhere near the threshold.
    assert late.tripped_at_row - 600 == MIN_CRASHES
    assert late.n_crashed == MIN_CRASHES


# --------------------------------------------------------------------------- #
# The docstring's table, pinned
# --------------------------------------------------------------------------- #


def test_the_operating_characteristics_are_what_the_docstring_claims():
    """A seeded measurement of every row of the module docstring's table.

    This is the pin. The thresholds in ``eval.serve_breaker`` are choices, and a choice
    with no measured consequence beside it is exactly the shape of the price table that
    was wrong by 9x with nothing able to tell. Move any constant and this goes red with
    the new numbers in the failure message; update the table, do not delete the test.
    """
    expected = {
        # true rate: (P(ever trips), median trip row or None)
        0.0015: (0.0, None),
        0.005: (0.0, None),
        0.01: (0.0075, 634),
        0.02: (0.0575, 656),
        0.03: (0.29, 630),
        0.05: (0.91, 364),
        0.10: (1.0, 70),
        0.48: (1.0, 20),
    }
    measured = {p: _characteristics(p) for p in expected}
    for p, (want_rate, want_median) in expected.items():
        got_rate, got_median = measured[p]
        assert got_rate == pytest.approx(want_rate, abs=0.02), (p, measured)
        if want_median is None:
            assert got_median is None, (p, measured)
        else:
            assert got_median == pytest.approx(want_median, rel=0.15), (p, measured)


def test_the_warning_fires_before_the_trip_and_only_once():
    """One crash in every four rows: bad, but slow enough that the warning has room.

    The warning must NOT share the trip's ``MIN_ROWS`` / ``MIN_CRASHES`` floors. With
    them it was unreachable on a fast failure — the floors and the trip threshold clear
    on the same row — so the one signal an operator could have acted on never printed.
    """
    breaker = ServeCircuitBreaker(arm="a")
    warned_at = None
    for i in range(400):
        breaker.observe(crashed=(i % 4 == 0))
        if breaker.should_warn:
            assert warned_at is None, "the warning must not repeat"
            warned_at = breaker.n_rows
        if breaker.tripped:
            break
    assert warned_at is not None
    assert breaker.tripped
    assert warned_at < breaker.tripped_at_row


# --------------------------------------------------------------------------- #
# The artifact it leaves
# --------------------------------------------------------------------------- #


def test_the_state_block_is_written_whether_or_not_it_fired():
    """A gate that leaves a trace only when it fires cannot, afterwards, be told from a
    gate that was never wired up. Half this repo's defects have that shape."""
    quiet = ServeCircuitBreaker(arm="baseline", total=10)
    for _ in range(10):
        quiet.observe(crashed=False)
    state = quiet.state()
    assert state["tripped"] is False
    assert state["n_rows_observed"] == 10
    assert state["crash_rate"] == 0.0
    # The policy travels with the verdict: an archived marker has to be readable without
    # the source revision that wrote it.
    assert state["policy"]["p_tolerable"] == 0.02
    assert state["policy"]["min_crashes"] == MIN_CRASHES


def test_the_counts_freeze_at_the_trip():
    """A pooled run has tasks in flight when the breaker fires. Letting their rows push
    ``n_rows`` up would make the marker describe a sample the decision was not made on."""
    breaker = ServeCircuitBreaker(arm="a")
    while not breaker.tripped:
        breaker.observe(crashed=True)
    frozen = breaker.state()
    for _ in range(50):
        breaker.observe(crashed=False)
    assert breaker.state() == frozen


def test_the_message_names_the_arm_the_counts_and_the_cross_run_hazard():
    breaker = ServeCircuitBreaker(arm="curated_sme", total=1351)
    while not breaker.tripped:
        breaker.observe(crashed=True)
    msg = breaker.message()
    assert "curated_sme" in msg
    assert "--resume" in msg
    # The 2026-07-31 incident took down a SECOND run through a shared token budget, and
    # nothing said so. An operator who restarts without checking repeats it.
    assert "SECOND" in msg


# --------------------------------------------------------------------------- #
# Aborting has to be cheaper than finishing
# --------------------------------------------------------------------------- #


def _pool_factory():
    return lambda idx: ServeWorker(
        connector=type("C", (), {"close": lambda self: None})(),
        gateway=None,
        solver=None,
    )


def test_an_abort_from_on_result_stops_the_pool_spending():
    """The property that makes the breaker worth wiring at all.

    ``pool.map`` submits EVERY item up front and ``ThreadPoolExecutor.__exit__`` calls
    ``shutdown(wait=True)`` with no ``cancel_futures``, so an exception escaping the
    result loop does not stop the remaining questions — the executor waits for all of
    them. Before the cooperative abort, aborting a 1351-question arm at row 12 still paid
    for 1339 model calls, which is the entire point of a circuit breaker undone.
    """
    ran: list[int] = []
    lock = threading.Lock()

    def task(_worker, item):
        with lock:
            ran.append(item)
        return item

    def on_result(item):
        if item == 5:
            raise RuntimeError("breaker tripped")

    with pytest.raises(RuntimeError, match="breaker tripped"):
        run_ordered_pool(
            list(range(400)),
            workers=4,
            make_worker=_pool_factory(),
            run_task=task,
            on_result=on_result,
            heartbeat_s=0,
        )
    # Generous on purpose: the workers are mid-flight when the abort lands, so a handful
    # more get through. What must not happen is all 400.
    assert len(ran) < 60, len(ran)


def test_the_abandoned_tasks_are_not_counted_as_failures():
    """A ``PoolAborted`` is not a crash. Counting it would put phantom crashes in the
    pool's own stats — the ``crash is not a refusal`` confusion, one layer over."""
    seen: list[BaseException] = []

    def task(_worker, item):
        try:
            return item
        except PoolAborted as err:  # pragma: no cover - defensive
            seen.append(err)
            raise

    def on_result(item):
        if item == 2:
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        run_ordered_pool(
            list(range(200)),
            workers=4,
            make_worker=_pool_factory(),
            run_task=task,
            on_result=on_result,
            heartbeat_s=0,
        )
    assert seen == []


# --------------------------------------------------------------------------- #
# It is actually wired up
#
# Three functions in this repo have tests, exports and zero production call sites
# (`schema_misroute_report`, `treatment_reasons`, `reliability_tier`). A breaker with a
# thorough test file and no caller would be the fourth, and it would look identical to a
# working one from here. So: exercise the driver's own seam.
# --------------------------------------------------------------------------- #


def test_the_driver_seam_writes_a_marker_and_raises(tmp_path):
    from governed_bi.eval.run_datalake import (
        SERVE_BREAKER_MARKER,
        _observe_serve_health,
    )
    from governed_bi.eval.serve_breaker import ServeCircuitBreakerTripped

    out_path = tmp_path / "generations.curated.jsonl"
    breaker = ServeCircuitBreaker(arm="curated", total=1351)
    crashed = {"question_id": "q", "outcome": "crashed"}
    with pytest.raises(ServeCircuitBreakerTripped):
        for i in range(100):
            _observe_serve_health(breaker, dict(crashed, question_id=f"q{i}"), out_path=out_path)
    marker = tmp_path / SERVE_BREAKER_MARKER
    assert marker.exists(), "a tripped arm raises, so summary.json never lands — the "
    "marker is the only thing that survives to say why"
    import json

    body = json.loads(marker.read_text(encoding="utf-8"))
    assert body["arm"] == "curated"
    assert body["tripped"] is True
    assert body["policy"]["min_crashes"] == MIN_CRASHES
    assert "SECOND" in body["reason"]


def test_the_driver_seam_leaves_a_healthy_arm_alone(tmp_path):
    from governed_bi.eval.run_datalake import (
        SERVE_BREAKER_MARKER,
        _observe_serve_health,
    )

    out_path = tmp_path / "generations.baseline.jsonl"
    breaker = ServeCircuitBreaker(arm="baseline", total=1351)
    for i in range(1351):
        row = {"question_id": f"q{i}", "outcome": "crashed" if i in (5, 900) else "correct"}
        _observe_serve_health(breaker, row, out_path=out_path)
    assert not (tmp_path / SERVE_BREAKER_MARKER).exists()
    assert breaker.tripped is False


def test_the_serve_loop_calls_the_seam():
    """`_persist` is the only place that sees every row on one thread in order. If the
    call moves out of it the breaker is silently off, and every other test here passes."""
    import inspect

    from governed_bi.eval import run_datalake

    src = inspect.getsource(run_datalake._run_pool_arm)
    assert "_observe_serve_health(breaker" in src
    # In `_persist`, not somewhere else in the function: only that callback runs on the
    # submitting thread in submission order, which is what makes the breaker lock-free
    # and makes the pooled and serial paths see the same sequence.
    persist = src.split("def _persist(")[1].split("\n        try:")[0]
    assert "_observe_serve_health(breaker" in persist


def test_the_breaker_state_reaches_the_arm_summary():
    """`summary.json` -> the ledger. A gate whose verdict never leaves the process is a
    print statement, which is what the SME no-op detector was for weeks."""
    import inspect

    from governed_bi.eval import run_datalake

    src = inspect.getsource(run_datalake._run_pool_arm)
    assert 'summary["circuit_breaker"]' in src


def test_a_pool_with_no_abort_is_unchanged():
    """The abort seam must not perturb the normal path: same results, same order."""
    out = run_ordered_pool(
        list(range(50)),
        workers=4,
        make_worker=_pool_factory(),
        run_task=lambda _w, item: item * 2,
        on_result=lambda _r: None,
        heartbeat_s=0,
    )
    assert list(out) == [i * 2 for i in range(50)]
