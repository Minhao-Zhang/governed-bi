"""Thread-pool serve scheduler for the eval drivers.

Spec: ``docs/measurement.md`` (the ``workers`` knob).

The per-question serve loop is the wall-clock bottleneck (one LLM-and-DB-bound
agentic turn per question). This module runs that loop across ``workers`` OS
threads, each owning its **own** connector / gateway / solver — built lazily on
first use, thread-local, reused across every task that lands on that thread.
Threads fit the workload: the drivers are sync blocking-IO code and the GIL
releases during the network round-trips that dominate the wall-clock.

Two invariants make parallelism safe (see the design doc's results-invariance
argument):

- **Isolation.** Every worker gets a distinct ``(connector, gateway, solver)``.
  psycopg connections are not thread-safe and the serve graph closes over
  per-turn mutable state, so nothing may be shared across threads.
- **Deterministic aggregation.** Results come back in the original submission
  order (``ThreadPoolExecutor.map`` preserves it), so the caller iterates them
  exactly as the serial loop would and no counter is mutated off-thread.

``workers == 1`` is never routed here: the drivers keep their serial path so the
default is byte-identical to the pre-concurrency behaviour.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Above this the operator is almost certainly starving their Postgres
# ``max_connections`` budget; warn loudly but proceed unchanged (the box owner,
# not this code, sizes to the real connection ceiling — design doc §Config).
MAX_SANE_WORKERS = 32


def resolve_workers(workers: int) -> int:
    """Validate an operator-supplied worker count.

    A value below 1 is meaningless for a pool and is floored to 1 (serial) with
    a warning. A value above :data:`MAX_SANE_WORKERS` is left **unchanged** — the
    design forbids silently reducing it — but warns loudly so a run that would
    exhaust the DB connection budget is never a surprise.
    """
    if workers < 1:
        print(f"*** WARNING: workers={workers} < 1 is invalid; using 1 (serial). ***")
        return 1
    if workers > MAX_SANE_WORKERS:
        print(
            f"*** WARNING: workers={workers} exceeds the sane cap of "
            f"{MAX_SANE_WORKERS}; proceeding UNCHANGED. Size this to your Postgres "
            f"max_connections (minus headroom) or the pool will starve. ***"
        )
    return workers


@dataclass
class ServeWorker:
    """One worker's private serve context: its own connector + gateway + solver.

    The connector is closed at pool teardown; the gateway and solver are used for
    both solving and grading a task so a question never crosses connections.
    """

    connector: Any
    gateway: Any
    solver: Any


@dataclass
class WorkerStats:
    """How much work one worker thread actually did.

    ``n_tasks`` / ``n_failures`` are mutated only by the owning thread, so they need
    no lock; ``close_error`` is written by the teardown loop on the calling thread,
    after the pool has joined and every worker thread is gone.
    """

    worker_index: int
    n_tasks: int = 0
    n_failures: int = 0
    close_error: str | None = None


class PoolResult(list):
    """The pool's results, plus how the work was actually distributed.

    Both drivers already consume a plain ``list``, and the concurrency contract
    (``tests/test_eval_concurrency.py``) is that a pooled run and a serial run agree
    on every scored field. Hanging the counters off the *container* instead of the
    rows keeps them out of that comparison for free: list equality ignores the
    subclass, and the drivers rebuild their row list from the elements, so a
    scheduling counter can never leak into a scored number.
    """

    workers: "list[WorkerStats]"

    @property
    def n_tasks(self) -> int:
        return sum(w.n_tasks for w in self.workers)

    @property
    def n_failures(self) -> int:
        return sum(w.n_failures for w in self.workers)

    @property
    def close_errors(self) -> "list[str]":
        return [w.close_error for w in self.workers if w.close_error]


def run_ordered_pool(
    items: list[T],
    *,
    workers: int,
    make_worker: Callable[[int], ServeWorker],
    run_task: Callable[[ServeWorker, T], R],
    on_result: "Callable[[R], None] | None" = None,
) -> PoolResult:
    """Run ``run_task`` over ``items`` across ``workers`` threads, in order.

    ``make_worker(thread_index)`` builds a fresh :class:`ServeWorker` the first
    time a given thread needs one; it is cached thread-locally and reused for
    every subsequent task on that thread. Each built worker is registered under a
    lock so all of them are closed at teardown, even the ones that only ran one
    task. Results are returned in the same order as ``items``.

    ``on_result`` is invoked once per result **on the calling thread, in
    submission order**, as each one becomes available. It is the durability seam:
    without it a multi-hour run holds every row in memory and loses all of them if
    the process dies, so there is nothing for ``--resume`` to resume from. Because
    it runs on one thread in a fixed order it needs no lock of its own, and the
    file it appends to is written in the same order the serial path would.

    The returned :class:`PoolResult` also carries a :class:`WorkerStats` per built
    worker, so a run can say how the work was distributed and whether any task or
    any teardown failed — the pool was previously unobserved end to end.
    """
    local = threading.local()
    built: list[tuple[ServeWorker, WorkerStats]] = []
    built_lock = threading.Lock()
    index_counter = {"n": 0}
    index_lock = threading.Lock()

    def _worker() -> tuple[ServeWorker, WorkerStats]:
        pair = getattr(local, "pair", None)
        if pair is None:
            with index_lock:
                idx = index_counter["n"]
                index_counter["n"] += 1
            pair = (make_worker(idx), WorkerStats(worker_index=idx))
            local.pair = pair
            with built_lock:
                built.append(pair)
        return pair

    def _run(item: T) -> R:
        ctx, stats = _worker()
        stats.n_tasks += 1
        try:
            return run_task(ctx, item)
        except Exception:
            # Counted and re-raised. A pool that absorbed task errors would turn a
            # crashing arm into a merely-refusing one, which is exactly the confusion
            # that made a whole three-arm run unquotable.
            stats.n_failures += 1
            raise

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # ``map`` preserves input order regardless of completion order, so the
            # caller aggregates results exactly as the serial loop would. Iterating
            # (rather than materialising) lets ``on_result`` persist each row as it
            # lands instead of only after the last one finishes.
            results = PoolResult()
            for result in pool.map(_run, items):
                if on_result is not None:
                    on_result(result)
                results.append(result)
            with built_lock:
                # The same mutable ``WorkerStats`` objects the threads own, so the
                # ``finally`` below can still record a teardown failure onto the object
                # this call is about to hand back (``finally`` runs before the return
                # value reaches the caller).
                results.workers = [stats for _ctx, stats in built]
            return results
    finally:
        with built_lock:
            to_close = list(built)
        for ctx, stats in to_close:
            try:
                ctx.connector.close()
            except Exception as err:
                # Teardown runs in a ``finally``, so re-raising would replace a real
                # task error with a close() error. But swallowing it in silence was the
                # one failure in this codebase that left no trace at all: a leaked
                # connection starves the next run's connection budget and nothing
                # anywhere says why. Record it and say it out loud instead.
                stats.close_error = f"{type(err).__name__}: {err}"
                print(
                    f"*** WARNING: worker {stats.worker_index} connector.close() "
                    f"failed: {stats.close_error} — connection may be leaked. ***"
                )
        if to_close:
            print(
                f"  pool: {sum(s.n_tasks for _c, s in to_close)} task(s) over "
                f"{len(to_close)} worker(s), "
                f"{sum(s.n_failures for _c, s in to_close)} failure(s); "
                f"per-worker tasks {[s.n_tasks for _c, s in to_close]}"
            )
