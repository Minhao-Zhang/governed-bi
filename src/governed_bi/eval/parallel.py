"""Thread-pool serve scheduler for the eval drivers.

Spec: ``docs/plans/eval-concurrency-design.md`` (the ``workers`` knob).

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


def run_ordered_pool(
    items: list[T],
    *,
    workers: int,
    make_worker: Callable[[int], ServeWorker],
    run_task: Callable[[ServeWorker, T], R],
) -> list[R]:
    """Run ``run_task`` over ``items`` across ``workers`` threads, in order.

    ``make_worker(thread_index)`` builds a fresh :class:`ServeWorker` the first
    time a given thread needs one; it is cached thread-locally and reused for
    every subsequent task on that thread. Each built worker is registered under a
    lock so all of them are closed at teardown, even the ones that only ran one
    task. Results are returned in the same order as ``items``.
    """
    local = threading.local()
    built: list[ServeWorker] = []
    built_lock = threading.Lock()
    index_counter = {"n": 0}
    index_lock = threading.Lock()

    def _worker() -> ServeWorker:
        ctx = getattr(local, "ctx", None)
        if ctx is None:
            with index_lock:
                idx = index_counter["n"]
                index_counter["n"] += 1
            ctx = make_worker(idx)
            local.ctx = ctx
            with built_lock:
                built.append(ctx)
        return ctx

    def _run(item: T) -> R:
        return run_task(_worker(), item)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # ``map`` preserves input order regardless of completion order, so the
            # caller aggregates results exactly as the serial loop would.
            return list(pool.map(_run, items))
    finally:
        with built_lock:
            to_close = list(built)
        for ctx in to_close:
            try:
                ctx.connector.close()
            except Exception:  # teardown must not mask a task error
                pass
