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
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: Seconds between heartbeat lines. A three-arm ladder runs ~13 h with a p99
#: question of ~6 min, so a minute is frequent enough to see a stall and rare
#: enough not to bury the driver's own output.
DEFAULT_HEARTBEAT_S = 60.0

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


class PoolAborted(RuntimeError):
    """Placeholder raised INSTEAD of running a task once the pool is aborting.

    Never surfaces to a caller: ``run_ordered_pool`` re-raises the original
    ``on_result`` exception, and the tasks that raise this one are the ones whose
    results are already being discarded. It exists so an abandoned task returns
    immediately rather than spending a model call.
    """


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
    #: Live counters for the run that produced this list (see :class:`PoolProgress`).
    progress: "PoolProgress"

    @property
    def n_tasks(self) -> int:
        return sum(w.n_tasks for w in self.workers)

    @property
    def n_failures(self) -> int:
        return sum(w.n_failures for w in self.workers)

    @property
    def close_errors(self) -> "list[str]":
        return [w.close_error for w in self.workers if w.close_error]


@dataclass
class PoolProgress:
    """Live counters for one pool run. The distinction the ledger cannot make.

    ``ThreadPoolExecutor.map`` submits every task eagerly and then yields results
    **in submission order**, so a slow question at the head of the queue blocks
    result *delivery* while the workers behind it keep finishing. Measured: with 4
    workers, 40 tasks and a 5 s head task, 39 tasks had finished and **zero** rows
    had been written at t = 4 s. Nothing is stalled — but the only two artifacts a
    running eval produces (the generations JSONL, and ``_ServeProgress``, which
    ticks from the same blocked callback) both report zero progress. On
    2026-08-01 that made an operator twice declare a healthy run dead.

    So: ``done`` counts tasks that RETURNED; ``written`` counts results that
    reached ``on_result``. ``done - written`` is the head-of-line lag, and it is
    the number that separates "nothing is happening" from "everything is happening
    and one row is late". ``done`` is also the honest ETA denominator.

    Counters are mutated under :attr:`lock` because worker threads write ``done``
    while the calling thread writes ``written`` and the heartbeat thread reads both.
    """

    total: int
    started: int = 0
    done: int = 0
    written: int = 0
    failed: int = 0
    #: Wall-clock seconds per completed task, for a real p50/p99.
    durations: list[float] = field(default_factory=list)
    lock: Any = field(default_factory=threading.Lock)
    t0: float = field(default_factory=time.perf_counter)

    @property
    def in_flight(self) -> int:
        return self.started - self.done

    def line(self) -> str:
        with self.lock:
            started, done, written = self.started, self.done, self.written
            failed = self.failed
            durs = sorted(self.durations)
        elapsed = time.perf_counter() - self.t0
        rate = (done / elapsed * 60.0) if elapsed > 0 else 0.0
        eta = ""
        if 0 < done < self.total:
            eta = f" | eta {(self.total - done) * (elapsed / done) / 60.0:.0f}m"
        pct = f"p50 {durs[len(durs) // 2]:.0f}s" if durs else "p50 -"
        p99 = f"p99 {durs[min(len(durs) - 1, int(0.99 * len(durs)))]:.0f}s" if durs else "p99 -"
        return (
            f"  pool [t+{elapsed / 60.0:.0f}m]: {done}/{self.total} done "
            f"(written {written}, lag {done - written}), "
            f"{started - done} in flight, {failed} failed | "
            f"{rate:.1f}/min | {pct} {p99}{eta}"
        )


def run_ordered_pool(
    items: list[T],
    *,
    workers: int,
    make_worker: Callable[[int], ServeWorker],
    run_task: Callable[[ServeWorker, T], R],
    on_result: "Callable[[R], None] | None" = None,
    heartbeat_s: float = DEFAULT_HEARTBEAT_S,
    on_heartbeat: "Callable[[PoolProgress], None] | None" = None,
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

    ``heartbeat_s`` reports a :class:`PoolProgress` snapshot from a daemon thread on
    that interval (0 disables), through ``on_heartbeat`` (default: print its
    ``line()``). It exists because every other progress signal a run emits is
    downstream of ``on_result``, and ``on_result`` is head-of-line blocked — see
    :class:`PoolProgress`. Emitted from here rather than from the driver so no call
    site has to opt in: the run that most needed this had no way to ask for it. The
    callback is also the only seam from which the *mid-run* counters are observable,
    which is what a test of the lag has to look at — at the end, lag is always 0.
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

    progress = PoolProgress(total=len(items))

    # Set when ``on_result`` raises. Checked before a task does any work, so the tasks
    # ``ThreadPoolExecutor.__exit__`` insists on draining cost nothing.
    #
    # This is what makes an abort from ``on_result`` actually save money. ``pool.map``
    # submits EVERY item up front, and ``__exit__`` calls ``shutdown(wait=True)`` — no
    # ``cancel_futures`` — so an exception escaping the loop body below does not stop the
    # remaining questions: the executor waits for all of them. On a 1351-question arm
    # aborting at row 12, that is 1339 model calls after the decision to stop. The
    # generator's own ``finally`` would cancel the queued futures, but it only runs when
    # the generator is closed, and at ``__exit__`` time it is still referenced by the
    # unwinding frame. Hence both halves: ``gen.close()`` below to cancel what has not
    # started, and this flag for whatever the executor has already handed to a thread.
    aborting = threading.Event()

    def _run(item: T) -> R:
        if aborting.is_set():
            # Not counted as a failure: nothing was attempted, and this result is
            # discarded. Counting it would put phantom crashes in the pool's own stats.
            raise PoolAborted("pool aborting; task not started")
        ctx, stats = _worker()
        stats.n_tasks += 1
        with progress.lock:
            progress.started += 1
        t_start = time.perf_counter()
        try:
            return run_task(ctx, item)
        except Exception:
            # Counted and re-raised. A pool that absorbed task errors would turn a
            # crashing arm into a merely-refusing one, which is exactly the confusion
            # that made a whole three-arm run unquotable.
            stats.n_failures += 1
            with progress.lock:
                progress.failed += 1
            raise
        finally:
            # In a ``finally`` so a crashed question still counts as no longer in
            # flight; otherwise a run with crashes reports phantom busy workers
            # forever and the ETA never converges.
            with progress.lock:
                progress.done += 1
                progress.durations.append(time.perf_counter() - t_start)

    stop = threading.Event()

    report = on_heartbeat or (lambda p: print(p.line(), flush=True))

    def _heartbeat() -> None:
        while not stop.wait(heartbeat_s):
            report(progress)

    beat: "threading.Thread | None" = None
    if heartbeat_s and heartbeat_s > 0 and items:
        beat = threading.Thread(target=_heartbeat, name="pool-heartbeat", daemon=True)
        beat.start()

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # ``map`` preserves input order regardless of completion order, so the
            # caller aggregates results exactly as the serial loop would. Iterating
            # (rather than materialising) lets ``on_result`` persist each row as it
            # lands instead of only after the last one finishes.
            results = PoolResult()
            stream = pool.map(_run, items)
            for result in stream:
                if on_result is not None:
                    try:
                        on_result(result)
                    except BaseException:
                        # The durability seam is also the abort seam: it is the only
                        # code that sees every row, on one thread, in order. A serve
                        # circuit breaker lives here (see eval.serve_breaker), and it is
                        # worthless if stopping costs the same as finishing.
                        aborting.set()
                        stream.close()  # cancels every future not yet started
                        raise
                results.append(result)
                with progress.lock:
                    progress.written += 1
            with built_lock:
                # The same mutable ``WorkerStats`` objects the threads own, so the
                # ``finally`` below can still record a teardown failure onto the object
                # this call is about to hand back (``finally`` runs before the return
                # value reaches the caller).
                results.workers = [stats for _ctx, stats in built]
            results.progress = progress
            return results
    finally:
        stop.set()
        if beat is not None:
            beat.join(timeout=1.0)
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
